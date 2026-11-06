"""Authentication-bound attempts, transport limits and existing restore contracts."""
from collections import deque
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace as NS
from unittest.mock import Mock,patch
import hashlib
import unittest
from modules.capture_services.openwrt.password_control import PasswordControl,ControlUnavailable,AuthenticationTimeout,open_control,_event
from modules.capture_services.openwrt.password_verification import OpenWrtPasswordVerifier,PasswordVerifyCancelled
from modules.capture_services.openwrt.command_runner import RemoteCommandResult
from modules.capture_services.openwrt.operation_diagnostics import OperationDiagnostics

BSSID='02:11:22:33:44:55'
class Protocol:
    def __init__(self,modes=('wrong','good')):
        self.closed=False;self.commands=[];self.output=bytearray(b'Interactive mode\n\n> ');self.number=0
        self.modes=deque(modes);self.mode='wrong';self.active=None
    def sendall(self,data):
        for command in data.decode().splitlines():
            self.commands.append(command)
            if command=='ping':reply='PONG'
            elif command=='help':reply='pmksa_flush = flush PMKSA cache'
            elif command=='add_network':self.number+=1;reply=str(self.number)
            elif command.startswith('select_network '):
                self.active=command.split()[1];self.mode=self.modes.popleft();reply='OK'
                if self.mode=='wrong':reply+=f'\n<3>CTRL-EVENT-SSID-TEMP-DISABLED id={self.active} ssid="fixture" auth_failures=1 duration=10 reason=WRONG_KEY'
                elif self.mode=='old_wrong':reply+=f'\n<3>CTRL-EVENT-SSID-TEMP-DISABLED id={int(self.active)-1} ssid="fixture" auth_failures=1 duration=10 reason=WRONG_KEY'
                elif self.mode=='cancel':self.cancel.set()
            elif command=='status':
                state='COMPLETED' if self.mode in ('good','old_wrong','wrong_bssid','old_id') else 'DISCONNECTED'
                bssid='02:11:22:33:44:66' if self.mode=='wrong_bssid' else BSSID
                ident='0' if self.mode=='old_id' else self.active
                reply=f'wpa_state={state}\nid={ident}\nbssid={bssid}'
            else:reply='OK'
            self.output.extend((reply+'\n').encode())
    def recv_ready(self):return bool(self.output)
    def recv(self,n):raw=bytes(self.output[:n]);del self.output[:n];return raw
    def recv_stderr_ready(self):return False
    def exit_status_ready(self):return self.closed
    def close(self):self.closed=True

class PasswordControlTests(unittest.TestCase):
    def control(self,modes=('wrong','good'),clock=None):
        channel=Protocol(modes);channel.cancel=Event()
        ssh=Mock();streams=[Mock(),Mock(),Mock()]
        ssh.exec_command.return_value=(*streams,channel)
        journal=OperationDiagnostics()
        kwargs={'clock':clock} if clock else {}
        control=PasswordControl(ssh,'phy7-sta0',channel.cancel,journal=journal,**kwargs)
        self.addCleanup(control.close)
        return control,channel,journal

    def test_request_timeout_diagnostic_identifies_verb_without_credentials(self):
        from modules.capture_services.openwrt.password_control import ControlRequestTimeout
        control,channel,journal=self.control()
        with patch.object(control,'_readline',side_effect=TimeoutError('controlled')):
            with self.assertRaises(ControlRequestTimeout) as failure:
                control.request('set_network 1 psk secret-fixture')
        self.assertEqual(failure.exception.operation,'set_network')
        evidence=str(journal.snapshot())
        self.assertIn('native_rpc_set_network_timeout',evidence)
        self.assertNotIn('secret-fixture',evidence)
        self.assertNotIn('secret-fixture',str(failure.exception))

    def test_wrong_key_advances_to_fresh_id_and_authenticated_needs_no_dhcp(self):
        control,channel,journal=self.control()
        self.assertFalse(control.attempt('fixture',BSSID,'wrong-password'))
        self.assertTrue(control.attempt('fixture',BSSID,'right-password'))
        self.assertLess(channel.commands.index('add_network',channel.commands.index('add_network')+1),channel.commands.index('remove_network 1'))
        self.assertIn('disable_network 1',channel.commands)
        self.assertEqual(channel.commands.count('pmksa_flush'),3)
        evidence=str(journal.snapshot())
        self.assertIn('wrong_key',evidence);self.assertIn('authenticated',evidence)
        self.assertNotIn('password',evidence.replace('password_auth',''))
        self.assertNotIn('right-password',' '.join(channel.commands))

    def test_old_wrong_key_event_cannot_reject_the_new_candidate(self):
        control,channel,journal=self.control(('old_wrong',))
        self.assertTrue(control.attempt('fixture',BSSID,'candidate'))

    def test_success_requires_current_network_and_bssid(self):
        for mode in ('wrong_bssid','old_id'):
            with self.subTest(mode=mode):
                control,channel,_=self.control((mode,))
                with self.assertRaises(AuthenticationTimeout):
                    control.attempt('fixture',BSSID,'candidate',timeout=0)

    def test_passphrase_derivation_and_raw_psk_do_not_depend_on_shell_quoting(self):
        control,channel,_=self.control(('good','good'))
        password='p a"ss\\word'
        self.assertTrue(control.attempt('fixture',BSSID,password))
        expected=hashlib.pbkdf2_hmac('sha1',password.encode(),b'fixture',4096,32).hex()
        self.assertIn('set_network 1 psk '+expected,channel.commands)
        raw='ab'*32
        self.assertTrue(control.attempt('fixture',BSSID,raw))
        self.assertIn('set_network 2 psk '+raw,channel.commands)

    def test_cancel_during_attempt_is_prompt_and_close_reclaims_streams(self):
        control,channel,_=self.control(('cancel',))
        with self.assertRaises(InterruptedError):control.attempt('fixture',BSSID,'candidate')
        control.close();self.assertTrue(channel.closed);self.assertEqual(control.streams,())

    def test_only_exact_native_wrong_key_is_an_early_rejection(self):
        for line in ('CTRL-EVENT-DISCONNECTED bssid='+BSSID,
                     'CTRL-EVENT-SSID-TEMP-DISABLED id=1 ssid="fixture" reason=CONN_FAILED',
                     'CTRL-EVENT-SSID-TEMP-DISABLED id=1 ssid="reason=WRONG_KEY" reason=AUTH_FAILED'):
            self.assertIsNone(_event(line))

    def test_reused_network_id_stops_instead_of_accepting_stale_state(self):
        control,channel,_=self.control(('wrong',))
        self.assertFalse(control.attempt('fixture',BSSID,'candidate'))
        channel.number=0
        with self.assertRaises(RuntimeError):control.attempt('fixture',BSSID,'candidate')

    def test_identity_probe_missing_tool_falls_back_and_ambiguous_interface_stops(self):
        runner=Mock();runner.run.return_value=RemoteCommandResult('probe',127)
        self.assertIsNone(open_control(runner,'owned',Event()))
        import json
        runner.run.return_value=RemoteCommandResult('probe',0,json.dumps({'radio7':{'interfaces':[{'section':'owned','ifname':'phy7-sta0'},{'section':'owned','ifname':'phy8-sta0'}]}}))
        with self.assertRaises(RuntimeError):open_control(runner,'owned',Event())
        runner.ssh_client.exec_command.assert_not_called()

    def test_native_cancel_keeps_existing_verifier_cancel_semantics(self):
        verifier=OpenWrtPasswordVerifier(Mock())
        with patch('modules.capture_services.openwrt.password_control.open_control',side_effect=InterruptedError):
            with self.assertRaises(PasswordVerifyCancelled):
                with verifier._control_session('owned',Event()):pass

    def test_candidate_loop_does_not_reload_each_password_and_closes_on_success(self):
        verifier=OpenWrtPasswordVerifier(Mock())
        verifier._pick_radio=Mock(return_value='radio7');verifier._setup_environment=Mock()
        verifier._set_credentials=Mock();verifier._verify_connection=Mock()
        control=Mock();control.attempt.side_effect=[False,True]
        target=NS(ssid='fixture',bssid=BSSID,band='2.4G',encryption='WPA2-PSK')
        with TemporaryDirectory() as folder,patch('modules.capture_services.openwrt.password_control.open_control',return_value=control):
            result=verifier._verify_single(target,['wrong-password','right-password'],0,1,'owned','net',folder,None,None,False)
            self.assertTrue(result['entry'].found);self.assertEqual(result['entry'].tried,2)
            self.assertIn('right-password',Path(result['entry'].password_file).read_text(encoding='utf-8'))
        verifier._setup_environment.assert_called_once();verifier._set_credentials.assert_not_called()
        verifier._verify_connection.assert_not_called();control.close.assert_called_once()

    def test_dictionary_rejects_invalid_64_character_key_and_oversized_utf8(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'dict';path.write_text('g'*64+'\n'+'ab'*32+'\n'+'\u4e2d'*30+'\nvalid-password\n',encoding='utf-8')
            values,skipped=OpenWrtPasswordVerifier.load_candidates(path)
        self.assertEqual(values,('ab'*32,'valid-password'));self.assertEqual(skipped,2)

    def test_native_carriage_return_redraw_separates_reply_from_event(self):
        control,channel,_=self.control(('wrong',))
        original=channel.sendall
        def send(data):
            original(data)
            channel.output=channel.output.replace(b'OK\n<3>',b'OK\r<3>')
        channel.sendall=send
        self.assertFalse(control.attempt('fixture',BSSID,'candidate'))

    def test_unknown_failure_waits_for_deadline_without_claiming_wrong_key(self):
        control,channel,journal=self.control(('unknown',))
        with self.assertRaises(AuthenticationTimeout):
            control.attempt('fixture',BSSID,'candidate',timeout=.02)
        evidence=str(journal.snapshot())
        self.assertIn('authentication_unconfirmed',evidence)
        self.assertNotIn('wrong_key',evidence)

    def test_missing_native_cache_capability_closes_before_any_candidate(self):
        channel=Protocol();original=channel.sendall
        def send(data):
            original(data)
            channel.output=channel.output.replace(b'pmksa_flush = flush PMKSA cache',b'Unknown command')
        channel.sendall=send
        ssh=Mock();ssh.exec_command.return_value=(*[Mock() for _ in range(3)],channel)
        with self.assertRaises(ControlUnavailable):PasswordControl(ssh,'owned',Event())
        self.assertTrue(channel.closed)
        self.assertNotIn('add_network',channel.commands)

    def test_native_event_redraw_of_partially_typed_ping_is_not_a_reply(self):
        control,channel,_=self.control(('wrong',))
        original=channel.sendall
        def send(data):
            original(data)
            channel.output=channel.output.replace(b'OK\n<3>',b'OK\n> pi\r<3>')
        channel.sendall=send
        self.assertFalse(control.attempt('fixture',BSSID,'candidate'))

    def test_supplicant_without_cache_control_falls_back_before_candidates(self):
        channel=Protocol();original=channel.sendall
        def send(data):
            original(data)
            if data.startswith(b'pmksa_flush\n'):channel.output=channel.output.replace(b'OK\n',b'UNKNOWN COMMAND\n')
        channel.sendall=send
        ssh=Mock();ssh.exec_command.return_value=(*[Mock() for _ in range(3)],channel)
        with self.assertRaises(ControlUnavailable):PasswordControl(ssh,'owned',Event())
        self.assertTrue(channel.closed)
        self.assertNotIn('add_network',channel.commands)
