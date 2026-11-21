"""Temporary radio enablement shares the existing owned recovery lifecycle."""
import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
from threading import Event
import unittest
from unittest.mock import Mock, patch

from modules.capture_services.openwrt.password_environment import radio_disabled_snapshot
from modules.capture_services.openwrt.password_verification import OpenWrtPasswordVerifier, PasswordVerifyPreparationError
from modules.capture_services.openwrt.password_restore import PasswordRestoreCheck, runtime_snapshot
from modules.capture_services.openwrt.password_recovery import PasswordRecoveryJournal, recover_pending
from modules.capture_services.openwrt.password_recovery_store import RecoveryStore, validate
from modules.capture_services.openwrt.command_runner import RemoteCommandResult as Result
from tests.test_password_recovery import SID, FP

CLOSED = {radio: {'up':False, 'pending':False, 'disabled':True,
    'interfaces':[{'section':iface}]} for radio, iface in [('radio0','ap2'),('radio1','ap5')]}
CONFIG = """wireless.radio0=wifi-device
wireless.radio0.disabled='1'
wireless.radio1=wifi-device
wireless.radio1.disabled='true'
wireless.ap2=wifi-iface
wireless.ap2.device='radio0'
wireless.ap5=wifi-iface
wireless.ap5.device='radio1'
"""


class DisabledRadioTests(unittest.TestCase):
    def fixture(self, store=None):
        runner = NS(run=Mock(), ssh_client=NS(host_fingerprint=FP), operation_diagnostics=None)
        def run(name, command, **kwargs):
            values = {'password_read_ifaces':CONFIG, 'password_recovery_changes':'\0',
                'password_recovery_apply':'PASSWORD_RECOVERY_APPLIED',
                'password_restore_config_check':'RESTORE_CONFIG_OK',
                'password_restore_wireless_check':json.dumps(CLOSED),
                'password_restore_network_check':'{"interface":[]}'}
            return Result(name,0,values.get(name,''))
        runner.run.side_effect = run
        verifier = OpenWrtPasswordVerifier(runner)
        verifier._original_disabled = verifier._read_disabled_map(None)
        verifier._restore_check = PasswordRestoreCheck(runner, runtime_snapshot(json.dumps(CLOSED)))
        verifier._sleep = Mock()
        if store is not None:
            journal = PasswordRecoveryJournal(store,SID,FP)
            journal.capture(verifier._original_disabled,verifier._original_devices,
                verifier._restore_check.baseline,radio_disabled=verifier._original_radio_disabled)
            verifier.recovery_journal = journal
        return verifier,runner

    def test_selection_prefers_existing_enabled_radio_and_requires_unique_hardware(self):
        from tests.test_radio_identity import snapshot
        from modules.capture_services.openwrt.radio_takeover import OpenWrtRadioTakeover
        verifier = OpenWrtPasswordVerifier(Mock())
        for band, target_band in [('2g','2.4G'),('5g','5G')]:
            text = snapshot(identities={'radio7':'phy2','radio8':'phy3'},
                bands={'radio7':band,'radio8':band}) + "\nwireless.radio7.disabled='true'"
            with patch.object(OpenWrtRadioTakeover,'snapshot_wireless',return_value=text):
                self.assertEqual(verifier._pick_radio(target_band,None),'radio8')
            text += "\nwireless.radio8.disabled='1'"
            with patch.object(OpenWrtRadioTakeover,'snapshot_wireless',return_value=text):
                with self.assertRaises(PasswordVerifyPreparationError):verifier._pick_radio(target_band,None)
            text = snapshot(identities={},bands={'radio7':band}) + "\nwireless.radio7.disabled='1'"
            with patch.object(OpenWrtRadioTakeover,'snapshot_wireless',return_value=text):
                with self.assertRaises(PasswordVerifyPreparationError):verifier._pick_radio(target_band,None)

    def test_partial_setup_failure_still_has_recovery_record(self):
        with TemporaryDirectory() as folder:
            store=RecoveryStore(Path(folder)); verifier,runner=self.fixture(store)
            original=runner.run.side_effect
            def run(name, command, **kwargs):
                if name=='password_setup_env':return Result(name,1)
                return original(name,command,**kwargs)
            runner.run.side_effect=run
            with self.assertRaises(RuntimeError):
                verifier._setup_environment('wifinet_pw'+SID[:8],'pwnet_'+SID[:8],'radio1',None)
            self.assertEqual(store.records(FP)[0]['enabled_radios'],['radio1'])
            runner.run.side_effect=original
            self.assertEqual(verifier._restore('wifinet_pw'+SID[:8],'pwnet_'+SID[:8],'/tmp/owned',None),'')
            self.assertEqual(store.records(FP),[])

    def test_snapshot_retains_exact_flags_and_absent_option(self):
        for flag in ('1','true','yes','on','0','false','no','off'):
            self.assertEqual(radio_disabled_snapshot("wireless.radioA=wifi-device\nwireless.radioA.disabled='"+flag+"'"),{'radioA':flag})
        self.assertEqual(radio_disabled_snapshot('wireless.radioA=wifi-device'),{'radioA':None})
        for text in ('wireless.radioA=wifi-device\nwireless.radioA.disabled=invalid',
                     'wireless.radioA=wifi-device\nwireless.radioA=wifi-device'):
            with self.assertRaises(ValueError): radio_disabled_snapshot(text)

    def test_enable_is_scoped_and_existing_hotspot_stays_disabled(self):
        for radio, iface, other in [('radio0','ap2','radio1'),('radio1','ap5','radio0')]:
            with self.subTest(radio=radio):
                verifier,runner = self.fixture()
                verifier._setup_environment('owned_sta','owned_net',radio,None)
                command = next(c.args[1] for c in runner.run.call_args_list if c.args[0]=='password_setup_env')
                self.assertIn('wireless.'+radio+'.disabled=0',command)
                self.assertIn('wireless.'+iface+'.disabled=1',command)
                self.assertLess(command.index('wireless.'+iface+'.disabled=1'),command.index('wireless.'+radio+'.disabled=0'))
                self.assertNotIn('wireless.'+other,command)
                self.assertIn(".mode='sta'",command)
                self.assertNotIn(".mode='ap'",command)
                self.assertEqual(verifier._enabled_radios,{radio})

    def test_switch_restores_previous_radio_before_reloading(self):
        verifier,runner = self.fixture()
        verifier._setup_environment('owned_sta','owned_net','radio0',None)
        runner.run.reset_mock()
        verifier._setup_environment('owned_sta','owned_net','radio1',None)
        command = next(c.args[1] for c in runner.run.call_args_list if c.args[0]=='password_setup_env')
        self.assertIn('wireless.radio0.disabled=1',command)
        self.assertIn('wireless.radio1.disabled=0',command)

    def test_normal_restore_writes_original_values_and_reads_them_back(self):
        verifier,runner = self.fixture()
        verifier._setup_environment('owned_sta','owned_net','radio1',None)
        runner.run.reset_mock()
        self.assertEqual(verifier._restore('owned_sta','owned_net','/tmp/owned',None),'')
        commands = '\n'.join(c.args[1] for c in runner.run.call_args_list)
        self.assertIn('wireless.radio1.disabled=true',commands)
        self.assertIn('"$(uci -q get wireless.radio1)" = wifi-device',commands)
        self.assertNotIn('wireless.radio0',commands)

    def test_enabled_radio_keeps_original_path_and_no_radio_flag_write(self):
        verifier,runner = self.fixture()
        verifier._original_radio_disabled['radio1'] = None
        verifier._setup_environment('owned_sta','owned_net','radio1',None)
        command = next(c.args[1] for c in runner.run.call_args_list if c.args[0]=='password_setup_env')
        self.assertNotIn('wireless.radio1.disabled=',command)
        self.assertEqual(verifier._enabled_radios,set())

    def test_unsettled_original_state_stops_before_any_write(self):
        verifier,runner = self.fixture()
        verifier._restore_check.baseline['radio1']['pending'] = True
        runner.run.reset_mock()
        with self.assertRaises(PasswordVerifyPreparationError):
            verifier._setup_environment('owned_sta','owned_net','radio1',None)
        runner.run.assert_not_called()

    def test_durable_record_precedes_setup_and_restart_restores_disabled_radio(self):
        with TemporaryDirectory() as folder:
            store = RecoveryStore(Path(folder))
            verifier,runner = self.fixture(store)
            original = runner.run.side_effect
            def run(name, command, **kwargs):
                if name=='password_setup_env':
                    data = store.records(FP)[0]
                    self.assertEqual(data['enabled_radios'],['radio1'])
                    self.assertEqual(data['radio_disabled']['radio1'],'true')
                return original(name,command,**kwargs)
            runner.run.side_effect=run
            verifier._setup_environment('wifinet_pw'+SID[:8],'pwnet_'+SID[:8],'radio1',None)
            restarted = RecoveryStore(Path(folder))
            self.assertEqual(recover_pending(restarted,runner,FP),1)
            self.assertEqual(restarted.records(FP),[])
            command = next(c.args[1] for c in runner.run.call_args_list if c.args[0]=='password_recovery_apply')
            self.assertIn('wireless.radio1.disabled=true',command)
            self.assertNotIn('wireless.radio0.disabled',command)

    def test_failed_recovery_retains_disabled_radio_record(self):
        with TemporaryDirectory() as folder:
            store=RecoveryStore(Path(folder)); verifier,runner=self.fixture(store)
            verifier._setup_environment('wifinet_pw'+SID[:8],'pwnet_'+SID[:8],'radio0',None)
            runner.run.return_value=Result('failure',1);runner.run.side_effect=None
            with self.assertRaises(RuntimeError): recover_pending(store,runner,FP)
            self.assertEqual(store.records(FP)[0]['enabled_radios'],['radio0'])

    def test_cancelled_task_still_restores_disabled_radio(self):
        with TemporaryDirectory() as folder:
            store=RecoveryStore(Path(folder)); verifier,runner=self.fixture(store)
            verifier._setup_environment('wifinet_pw'+SID[:8],'pwnet_'+SID[:8],'radio0',None)
            cancel=Event();cancel.set()
            self.assertEqual(verifier._restore('wifinet_pw'+SID[:8],'pwnet_'+SID[:8],'/tmp/owned',cancel),'')
            self.assertEqual(store.records(FP),[])

    def test_invalid_enabled_radio_or_baseline_is_rejected(self):
        with TemporaryDirectory() as folder:
            store=RecoveryStore(Path(folder)); verifier,_=self.fixture(store)
            data=copy.deepcopy(verifier.recovery_journal.data)
            data.update(radios=['radio1'],enabled_radios=['radio1'])
            validate(data)
            for changes in ({'enabled_radios':['radio0']},{'enabled_radios':['bad']},
                            {'radio_disabled':{'radio1':None}}):
                invalid=dict(data,**changes)
                with self.assertRaises(ValueError):validate(invalid)
