"""Recovery evidence, cancellation cleanup and conservative security admission."""
import copy
import json
from pathlib import Path
from threading import Event
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
from modules.capture_services.openwrt.command_runner import RemoteCommandResult
from modules.capture_services.openwrt.password_restore import PasswordRestoreCheck, runtime_snapshot, _configuration_command
from modules.capture_services.openwrt.password_security import unsupported_security_note
from modules.capture_services.openwrt.password_verification import OpenWrtPasswordVerifier, PasswordTargetResult

STATE={'radio7':{'up':True,'pending':False,'disabled':False,'interfaces':[{'section':'cfg123','ifname':'phy7-ap0','config':{'key':'private-fixture'}}]}}
FLAGS={'cfg123':None}
DEVICES={'cfg123':'radio7'}


class RestoreTests(unittest.TestCase):
    def fixture(self, state=None, networks=None):
        runner=Mock()
        state=copy.deepcopy(STATE if state is None else state)
        def run(operation,command,**options):
            payload={'password_restore_baseline':json.dumps(STATE),
                     'password_restore_config_check':'RESTORE_CONFIG_OK',
                     'password_restore_wireless_check':json.dumps(state),
                     'password_restore_network_check':json.dumps({'interface':[] if networks is None else networks})}
            return RemoteCommandResult(operation,0,payload.get(operation,''))
        runner.run.side_effect=run
        return runner,PasswordRestoreCheck(runner,runtime_snapshot(json.dumps(STATE)))

    def verify(self,check):
        return check.verify('owned_sta','owned_net',FLAGS,DEVICES,set(FLAGS))

    def test_healthy_config_and_runtime_both_required(self):
        runner,check=self.fixture()
        self.assertEqual(self.verify(check),'')
        self.assertEqual(runner.run.call_count,3)
        self.assertTrue(all(c.kwargs['cancel_event'] is None for c in runner.run.call_args_list))
        command=runner.run.call_args_list[0].args[1]
        self.assertIn('wireless.owned_sta',command);self.assertIn('network.owned_net',command)
        self.assertIn('wireless.cfg123.disabled',command)
        self.assertNotIn('private-fixture',command)
        self.assertNotIn('reload',command)

    def test_runtime_pending_down_missing_or_wrong_flags_never_pass(self):
        variants=[]
        for key,value in [('pending',True),('up',False),('disabled',True),('interfaces',[])]:
            state=copy.deepcopy(STATE);state['radio7'][key]=value;variants.append(state)
        variants.append({})
        for state in variants:
            with self.subTest(state=state):
                unused,check=self.fixture(state)
                self.assertTrue(self.verify(check))

    def test_stale_temporary_network_or_wireless_is_reported(self):
        unused,check=self.fixture(networks=[{'interface':'owned_net','up':False}])
        self.assertTrue(self.verify(check))
        state=copy.deepcopy(STATE);state['radio7']['interfaces'].append({'section':'owned_sta','ifname':'phy7-sta1'})
        unused,check=self.fixture(state)
        self.assertTrue(self.verify(check))

    def test_malformed_network_entries_do_not_prove_temporary_network_absent(self):
        for rows in [[{}],[{'interface':None}],[{'interface':''}]]:
            runner,check=self.fixture(networks=rows)
            self.assertTrue(self.verify(check))

    def test_unknown_original_disabled_state_cannot_be_treated_as_recovered(self):
        for value in [None,0,'false']:
            baseline=copy.deepcopy(STATE);baseline['radio7']['disabled']=value
            runner,unused=self.fixture()
            check=PasswordRestoreCheck(runner,runtime_snapshot(json.dumps(baseline)))
            self.assertTrue(self.verify(check))

    def test_originally_nonrunning_interface_must_not_be_reported_as_restored_when_started(self):
        baseline=copy.deepcopy(STATE);baseline['radio7']['interfaces'][0].pop('ifname')
        runner,unused=self.fixture()
        check=PasswordRestoreCheck(runner,runtime_snapshot(json.dumps(baseline)))
        self.assertTrue(self.verify(check))

    def test_invalid_kernel_interface_name_is_not_running_evidence(self):
        for name in [' ','invalid/interface','x'*16]:
            state=copy.deepcopy(STATE);state['radio7']['interfaces'][0]['ifname']=name
            runner,check=self.fixture(state)
            self.assertTrue(self.verify(check))

    def test_untouched_other_radio_does_not_block_recovery(self):
        state=copy.deepcopy(STATE);state['radio99']={'up':False,'pending':True,'interfaces':[]}
        unused,check=self.fixture(state)
        self.assertEqual(self.verify(check),'')

    def test_empty_original_radio_still_has_runtime_checked(self):
        baseline={'radio7':{'up':False,'pending':False,'disabled':False,'interfaces':[]}}
        current=copy.deepcopy(baseline);current['radio7']['up']=True
        runner,unused=self.fixture(current)
        check=PasswordRestoreCheck(runner,runtime_snapshot(json.dumps(baseline)));check.affected_radios.add('radio7')
        self.assertTrue(check.verify('owned_sta','owned_net',{}, {},set()))

    def test_bad_command_result_missing_token_and_timeout_never_pass(self):
        for result in [RemoteCommandResult('read',1),RemoteCommandResult('read',0,''),RemoteCommandResult('read',0,'RESTORE_CONFIG_OK',timed_out=True),RemoteCommandResult('read',0,'RESTORE_CONFIG_OK',cancelled=True)]:
            runner,check=self.fixture();runner.run.side_effect=None;runner.run.return_value=result
            self.assertTrue(self.verify(check))
            self.assertEqual(runner.run.call_count,1)

    def test_transport_exception_cannot_leak_raw_error_to_user(self):
        runner,check=self.fixture();runner.run.side_effect=OSError('private-fixture')
        message=self.verify(check)
        self.assertTrue(message);self.assertNotIn('private-fixture',message)
        self.assertNotIn(chr(92)+'u',message)

    def test_diagnostics_keep_fixed_reason_codes_without_private_config(self):
        from modules.capture_services.openwrt.operation_diagnostics import OperationDiagnostics
        state=copy.deepcopy(STATE);state['radio7']['pending']=True
        runner,check=self.fixture(state);runner.operation_diagnostics=OperationDiagnostics()
        self.assertTrue(self.verify(check))
        snapshot=str(runner.operation_diagnostics.snapshot())
        self.assertIn('wireless_not_settled',snapshot)
        self.assertNotIn('private-fixture',snapshot)

    def test_restore_diagnostics_survive_actual_export_redaction(self):
        from modules.capture_services.openwrt.operation_diagnostics import OperationDiagnostics, sanitized_operation_evidence
        from modules.display_privacy import masker_for
        state=copy.deepcopy(STATE);state['radio7']['pending']=True
        runner,check=self.fixture(state);runner.operation_diagnostics=OperationDiagnostics()
        self.assertTrue(self.verify(check))
        exported=sanitized_operation_evidence(runner.operation_diagnostics.snapshot(),masker_for(['private-fixture']))
        rows=[r for r in exported['workflows'] if r.get('operation')=='password_restore_check']
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[-1]['reason'],'wireless_not_settled')
        self.assertEqual(rows[-1]['stage'],'runtime')
        self.assertNotIn('private-fixture',str(exported))

    def test_multiline_quoted_uci_value_cannot_change_original_interface_flags(self):
        from modules.capture_services.openwrt.password_environment import interface_snapshot
        text = ("wireless.cfg123=wifi-iface\nwireless.cfg123.device='radio7'\n"
                "wireless.cfg123.ssid='line one\nwireless.cfg123.disabled=\"1\"\nline two'\n")
        flags,devices=interface_snapshot(text)
        self.assertEqual(flags,{'cfg123':None})
        self.assertEqual(devices,{'cfg123':'radio7'})

    def test_multiline_value_cannot_create_an_unrelated_interface_in_baseline(self):
        from modules.capture_services.openwrt.password_environment import interface_snapshot
        text = ("wireless.cfg123=wifi-iface\nwireless.cfg123.device='radio7'\n"
                "wireless.cfg123.ssid='line one\nwireless.foreign=wifi-iface\n"
                "wireless.foreign.device=\"radio99\"\nline two'\n")
        flags,devices=interface_snapshot(text)
        self.assertEqual(set(flags),{'cfg123'})
        self.assertEqual(devices,{'cfg123':'radio7'})

    def test_uci_quote_escapes_and_list_values_preserve_only_original_flags(self):
        from modules.capture_services.openwrt.password_environment import interface_snapshot
        text=("wireless.cfg123=wifi-iface\nwireless.cfg123.device='radio7'\n"
              "wireless.cfg123.ssid='a'\\''b # name'\n"
              "wireless.cfg123.network='lan' 'guest'\n"
              "wireless.cfg123.disabled='false'\n")
        flags,devices=interface_snapshot(text)
        self.assertEqual(flags,{'cfg123':'false'})
        self.assertEqual(devices,DEVICES)

    def test_malformed_or_duplicate_uci_fields_fail_closed(self):
        from modules.capture_services.openwrt.password_environment import interface_snapshot
        prefix="wireless.cfg123=wifi-iface\nwireless.cfg123.device='radio7'\n"
        for suffix in ["wireless.cfg123.ssid='unfinished\n", "wireless.cfg123.device='radio99'\n"]:
            with self.assertRaises(ValueError):interface_snapshot(prefix+suffix)

    def test_runtime_snapshot_is_bounded_and_strips_credentials(self):
        snapshot=runtime_snapshot(json.dumps(STATE))
        self.assertNotIn('private-fixture',str(snapshot))
        for text in ['[]','{} invalid',json.dumps({'radio7':{'interfaces':[]}}),json.dumps({'radio7':{'interfaces':[{}]}})]:
            try:runtime_snapshot(text)
            except ValueError:continue
            self.assertEqual(text,json.dumps({'radio7':{'interfaces':[]}}))

    def test_capture_preserves_cancel_and_uses_stable_config_names(self):
        runner,unused=self.fixture();cancel=Event()
        check=PasswordRestoreCheck.capture(runner,cancel)
        self.assertIs(runner.run.call_args.kwargs['cancel_event'],cancel)
        verifier=OpenWrtPasswordVerifier(runner)
        runner.run.side_effect=None;runner.run.return_value=RemoteCommandResult('read',0,"wireless.cfg123=wifi-iface\nwireless.cfg123.device='radio7'")
        verifier._read_disabled_map(None)
        self.assertEqual(runner.run.call_args.args[1],'uci -X show wireless')
        self.assertNotIn('private-fixture',str(check.baseline))

    def test_generated_readback_command_in_real_posix_shell(self):
        import shutil, subprocess
        shell=shutil.which('sh')
        if shell is None:
            candidate=Path('C:/Program Files/Git/bin/bash.exe')
            shell=str(candidate) if candidate.is_file() else None
        if shell is None:self.skipTest('POSIX shell unavailable')
        for fault in ['', 'temporary', 'wrong_device', 'unexpected_disabled', 'unreadable', 'lookup_io', 'lookup_silent_failure']:
            fixture=r"""uci() {
 case "$*" in
 '-q show wireless'|'-q show network') [ "$FAULT" != unreadable ]; return;;
 '-q get wireless.cfg123.device') if [ "$FAULT" = wrong_device ]; then printf radio99; else printf radio7; fi; return;;
 '-q get wireless.cfg123.disabled') printf 1; return;;
 'get wireless.owned_sta'|'get network.owned_net'|'get wireless.cfg123.disabled')
   if [ "$FAULT" = temporary ] && [ "$*" = 'get wireless.owned_sta' ]; then printf wifi-iface; return 0; fi
   if [ "$FAULT" = unexpected_disabled ] && [ "$*" = 'get wireless.cfg123.disabled' ]; then printf 1; return 0; fi
   if [ "$FAULT" = lookup_io ]; then printf 'uci: I/O error\n' >&2; return 1; fi
   if [ "$FAULT" != lookup_silent_failure ]; then printf 'uci: Entry not found\n' >&2; fi
   return 1;;
 *) return 1;;
 esac
}
"""
            command=_configuration_command('owned_sta','owned_net',FLAGS,DEVICES,set(FLAGS))
            import os
            environment=dict(os.environ,FAULT=fault)
            result=subprocess.run([shell,'-c',fixture+command],capture_output=True,text=True,timeout=10,env=environment)
            with self.subTest(fault=fault):
                self.assertEqual(result.returncode==0,not bool(fault),result.stderr)
                self.assertEqual(result.stdout,'RESTORE_CONFIG_OK' if not fault else '')

    def test_multiline_value_mentioning_owned_section_is_not_a_real_section(self):
        import shutil, subprocess
        shell=shutil.which('sh')
        if shell is None:
            candidate=Path('C:/Program Files/Git/bin/bash.exe')
            shell=str(candidate) if candidate.is_file() else None
        if shell is None:self.skipTest('POSIX shell unavailable')
        fixture=r"""uci() {
 case "$*" in
 '-q show wireless'|'-q -X show wireless') printf "wireless.cfg123=wifi-iface\nwireless.cfg123.ssid='line one\nwireless.owned_sta=wifi-iface\nline two'\n"; return 0;;
 '-q show network'|'-q -X show network') printf 'network.lan=interface\n'; return 0;;
 '-q get wireless.cfg123.device') printf radio7; return 0;;
 'get wireless.owned_sta'|'get network.owned_net'|'get wireless.cfg123.disabled') printf 'uci: Entry not found\n' >&2; return 1;;
 *) return 1;;
 esac
}
"""
        command=_configuration_command('owned_sta','owned_net',FLAGS,DEVICES,set(FLAGS))
        result=subprocess.run([shell,'-c',fixture+command],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout,'RESTORE_CONFIG_OK')

    def test_failed_absence_lookup_cannot_hide_a_remaining_section(self):
        import shutil, subprocess
        shell=shutil.which('sh')
        if shell is None:
            candidate=Path('C:/Program Files/Git/bin/bash.exe')
            shell=str(candidate) if candidate.is_file() else None
        if shell is None:self.skipTest('POSIX shell unavailable')
        fixture=r"""uci() {
 case "$*" in
 '-q show wireless'|'-q -X show wireless') printf 'wireless.owned_sta=wifi-iface\nwireless.cfg123=wifi-iface\n'; return 0;;
 '-q show network'|'-q -X show network') printf 'network.lan=interface\n'; return 0;;
 '-q get wireless.cfg123.device') printf radio7; return 0;;
 *) return 1;;
 esac
}
"""
        command=_configuration_command('owned_sta','owned_net',FLAGS,DEVICES,set(FLAGS))
        result=subprocess.run([shell,'-c',fixture+command],capture_output=True,text=True,timeout=10)
        self.assertNotEqual(result.returncode,0,'A failed get is not evidence that the section is absent')
        self.assertEqual(result.stdout,'')

    def test_unsafe_names_and_missing_original_values_fail_before_any_remote_read(self):
        for name in ['bad;command','@wifi-iface[0]']:
            with self.assertRaises(ValueError):_configuration_command(name,'owned_net',FLAGS,DEVICES,set(FLAGS))
        with self.assertRaises(ValueError):_configuration_command('owned_sta','owned_net',{},DEVICES,set(FLAGS))

    def test_finally_checks_recovery_after_success_cancel_and_failure(self):
        for error in [None,InterruptedError('cancelled'),RuntimeError('operation failed')]:
            runner,check=self.fixture();verifier=OpenWrtPasswordVerifier(runner)
            def backup(*args):
                verifier._configuration_touched=True;verifier._restore_check=check
                verifier._original_disabled=FLAGS;verifier._original_devices=DEVICES;verifier._modified_ifaces=set(FLAGS)
            verifier._backup_configs=Mock(side_effect=backup)
            entry=PasswordTargetResult('02:11:22:33:44:55','fixture',False,0,0)
            with patch('modules.capture_services.openwrt.password_rounds.run_rounds',return_value=(entry,),side_effect=error):
                if error is None:self.assertEqual(len(verifier.verify_targets((),(),'owned',Path('build'))),1)
                else:
                    with self.assertRaises(type(error)):verifier.verify_targets((),(),'owned',Path('build'),Event())
            self.assertFalse(verifier.last_restore_errors)
            self.assertIsNone(verifier._restore_check)
            self.assertIn('password_restore_network_check',[c.args[0] for c in runner.run.call_args_list])

    @patch("modules.capture_services.openwrt.password_verification.SIGNAL_READY_SECONDS", 0)
    def test_unconfirmed_restore_is_visible_after_success_and_cancellation(self):
        state=copy.deepcopy(STATE);state['radio7']['pending']=True
        for error in [None,InterruptedError('cancelled')]:
            runner,check=self.fixture(state);verifier=OpenWrtPasswordVerifier(runner)
            def backup(*args):
                verifier._configuration_touched=True;verifier._restore_check=check
                verifier._original_disabled=FLAGS;verifier._original_devices=DEVICES;verifier._modified_ifaces=set(FLAGS)
            verifier._backup_configs=Mock(side_effect=backup)
            entry=PasswordTargetResult('02:11:22:33:44:55','fixture',False,0,0)
            with patch('modules.capture_services.openwrt.password_rounds.run_rounds',return_value=(entry,),side_effect=error):
                if error:
                    with self.assertRaises(InterruptedError):verifier.verify_targets((),(),'owned',Path('build'))
                else:
                    result=verifier.verify_targets((),(),'owned',Path('build'))
                    self.assertIn(verifier.last_restore_errors,result[-1].note)
            self.assertTrue(verifier.last_restore_errors)


class SecurityGateTests(unittest.TestCase):
    def test_existing_explicit_wpa2_personal_and_mixed_wpa2_remain_supported(self):
        for value in ['WPA2-PSK','WPA2 CCMP PSK','WPA2 WPA3 CCMP PSK SAE','psk2']:
            self.assertEqual(unsupported_security_note(value),'')

    def test_enterprise_802_dot_1x_cannot_be_admitted_by_a_psk_token(self):
        self.assertTrue(unsupported_security_note('WPA2 PSK 802.1X'))

    def test_unsupported_or_ambiguous_security_never_reaches_radio_or_history(self):
        for value in ['WPA3 CCMP SAE','SAE','WEP','WPA TKIP PSK','WPA2 CCMP MGT','WPA2 EAP','WPA2 PSK 802.1X','WPA2','OWE']:
            runner=Mock();verifier=OpenWrtPasswordVerifier(runner);verifier._pick_radio=Mock()
            target=NS(ssid='fixture',bssid='02:11:22:33:44:55',encryption=value,band='5G')
            result=verifier._verify_single(target,('fixture-password',),0,1,'owned_sta','owned_net',Path('build'),None,None,False)
            self.assertFalse(result['entry'].found);self.assertEqual(result['entry'].tried,0)
            self.assertTrue(result['entry'].note);self.assertNotIn(chr(92)+'u',result['entry'].note)
            runner.run.assert_not_called();verifier._pick_radio.assert_not_called()
