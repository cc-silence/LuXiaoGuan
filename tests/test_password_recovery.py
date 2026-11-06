"""Durable, device-bound cleanup across disconnect and process restart."""
import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock,patch
from modules.capture_services.openwrt.command_runner import RemoteCommandResult
from modules.capture_services.openwrt.password_recovery import PasswordRecoveryJournal,recover_pending
from modules.capture_services.openwrt.password_recovery_store import RecoveryStore
from modules.capture_services.openwrt.password_restore import runtime_snapshot
from modules.capture_services.openwrt.reconnect_cleanup import ReconnectCleanup
from tests import test_password_environment as environment_fixtures

SID='a'*32
FP='SHA256:'+'a'*43
OTHER='SHA256:'+'b'*43
STATE={'radio1':{'up':True,'pending':False,'disabled':False,'interfaces':[{'section':'original','ifname':'phy1-ap0','config':{'key':'DO-NOT-STORE'}}]}}
BASELINE=runtime_snapshot(json.dumps(STATE))


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'records'
        self.store=RecoveryStore(self.root)
        self.journal=PasswordRecoveryJournal(self.store,SID,FP)
        self.journal.capture({'original':None},{'original':'radio1'},copy.deepcopy(BASELINE))
        self.journal.arm({'original'},{'radio1'})
    def runner(self,failed='',runtime=None):
        runner=NS(ssh_client=NS(host_fingerprint=FP),run=Mock(),operation_diagnostics=None)
        def run(operation,command,**kwargs):
            if operation==failed:return RemoteCommandResult(operation,1)
            outputs={'password_recovery_changes':'\0','password_recovery_apply':'PASSWORD_RECOVERY_APPLIED',
                     'password_restore_config_check':'RESTORE_CONFIG_OK',
                     'password_restore_wireless_check':json.dumps(STATE if runtime is None else runtime),
                     'password_restore_network_check':'{"interface":[]}'}
            return RemoteCommandResult(operation,0,outputs[operation])
        runner.run.side_effect=run
        return runner
    def test_disconnect_retains_record_then_new_process_recovers_without_passwords(self):
        failed=self.runner(failed='password_recovery_apply')
        with self.assertRaises(RuntimeError):self.journal.recover(failed,FP)
        self.assertEqual(len(self.store.records(FP)),1)
        text=next(self.root.glob('*.json')).read_text(encoding='utf-8')
        self.assertNotIn('DO-NOT-STORE',text)
        self.assertNotIn('ssid',text)
        restarted=RecoveryStore(self.root);runner=self.runner()
        self.assertEqual(recover_pending(restarted,runner,FP),1)
        self.assertEqual(restarted.records(FP),[])
        command=next(call.args[1] for call in runner.run.call_args_list if call.args[0]=='password_recovery_apply')
        self.assertIn('.routermanager_session',command)
        self.assertIn('wireless.wifinet_pw'+SID[:8],command)
        self.assertIn('network.pwnet_'+SID[:8],command)
        self.assertIn('wireless.original.disabled',command)
        self.assertNotIn('wifi down',command)
        self.assertNotIn('network.lan',command)
        self.assertNotIn('/etc/init.d/network restart',command)
    def test_other_router_is_never_modified(self):
        runner=self.runner()
        self.assertEqual(recover_pending(self.store,runner,OTHER),0)
        runner.run.assert_not_called()
        with self.assertRaises(RuntimeError):self.journal.recover(runner,OTHER)
        self.assertEqual(len(self.store.records(FP)),1)
    def test_cancel_retains_record_and_submits_nothing(self):
        cancel=Event();cancel.set();runner=self.runner()
        with self.assertRaises(InterruptedError):self.journal.recover(runner,FP,cancel)
        runner.run.assert_not_called()
        self.assertEqual(len(self.store.records(FP)),1)
    def test_config_check_failure_does_not_acknowledge_recovery(self):
        runner=self.runner(failed='password_restore_config_check')
        with self.assertRaises(RuntimeError):self.journal.recover(runner,FP)
        self.assertEqual(len(self.store.records(FP)),1)
    def test_runtime_failure_does_not_acknowledge_recovery(self):
        runner=self.runner(failed='password_restore_wireless_check')
        with patch('modules.capture_services.openwrt.password_recovery.SIGNAL_READY_SECONDS',0):
            with self.assertRaises(RuntimeError):self.journal.recover(runner,FP)
        self.assertEqual(len(self.store.records(FP)),1)
    def test_pending_record_blocks_new_wireless_tasks(self):
        backend=type('Backend',(),{})()
        backend.ssh_client=NS(host_fingerprint=FP)
        backend.password_recovery_store=self.store
        cleanup=ReconnectCleanup(backend)
        with self.assertRaises(RuntimeError):cleanup.require_ready()
        self.journal.recover(self.runner(),FP)
        cleanup.require_ready()
    def test_prepared_but_unarmed_record_does_not_reload_network(self):
        self.journal.discard()
        self.journal.capture({'original':None},{'original':'radio1'},copy.deepcopy(BASELINE))
        runner=self.runner();self.journal.recover(runner,FP)
        runner.run.assert_not_called()
        self.assertEqual(self.store.records(FP),[])
    def test_malformed_record_is_preserved_and_rejected(self):
        path=next(self.root.glob('*.json'))
        data=json.loads(path.read_text(encoding='utf-8'));data['modified']=['not_owned']
        path.write_text(json.dumps(data),encoding='utf-8')
        with self.assertRaises(ValueError):self.store.records(FP)
        self.assertTrue(path.exists())
    def test_write_ahead_record_and_markers_precede_first_configuration_change(self):
        verifier,runner=environment_fixtures.PasswordEnvironmentTests().fixture()
        verifier.recovery_journal=PasswordRecoveryJournal(self.store,'b'*32,FP)
        verifier.recovery_journal.capture(verifier._original_disabled,verifier._original_devices,verifier._restore_check.baseline)
        def check(operation,commands,event):
            data=self.store.records(FP)
            row=next(row for row in data if row['session']=='b'*32)
            self.assertEqual(set(row['modified']),{'ap5','old5'})
            self.assertEqual(row['radios'],['radio1'])
            self.assertTrue(any('routermanager_session=' in command for command in commands))
        verifier._run_batch=Mock(side_effect=check)
        verifier._run=Mock()
        verifier._setup_environment('wifinet_pw'+'b'*8,'pwnet_'+'b'*8,'radio1',None)
        verifier._run_batch.assert_called_once()


    def test_backend_login_recovers_before_device_probe_and_stops_on_failure(self):
        from modules.capture_services.backends.openwrt_backend import OpenWrtCaptureBackend
        class ProbeReached(Exception):pass
        for failed in (True,False):
            with self.subTest(failed=failed):
                backend=OpenWrtCaptureBackend(ssh_client=Mock())
                backend.password_recovery_store=RecoveryStore(self.root)
                backend.ssh_client.connect.return_value=NS(fingerprint=FP,key_type='ssh-ed25519')
                backend.command_runner=self.runner(failed='password_recovery_apply' if failed else '')
                backend.command_runner.radio_diagnostics=Mock()
                backend.command_runner.operation_diagnostics=Mock()
                backend.reconnect_cleanup.recover=Mock(return_value='')
                backend.device_probe.probe=Mock(side_effect=ProbeReached)
                progress=Mock()
                expected=RuntimeError if failed else ProbeReached
                with self.assertRaises(expected):
                    backend.connect(dict(host='192.0.2.1',port=22,user='root'),'fixture',
                                    expected_fingerprint=FP,progress_callback=progress)
                if failed:
                    backend.device_probe.probe.assert_not_called()
                    self.assertEqual(len(self.store.records(FP)),1)
                else:
                    backend.device_probe.probe.assert_called_once()
                    self.assertEqual(self.store.records(FP),[])
                backend.ssh_client.close.assert_called_once()
                self.assertGreaterEqual(progress.call_count,2)

    def test_unaffected_radio_can_have_unknown_runtime_flags(self):
        baseline=copy.deepcopy(BASELINE)
        baseline['radio0']=dict(up=None,pending=None,disabled=None,sections={})
        journal=PasswordRecoveryJournal(self.store,'c'*32,FP)
        journal.capture({'original':None},{'original':'radio1'},baseline)
        journal.arm({'original'},{'radio1'})
        with self.assertRaises(ValueError):journal.arm({'original'},{'radio0'})


if __name__=='__main__':unittest.main()
