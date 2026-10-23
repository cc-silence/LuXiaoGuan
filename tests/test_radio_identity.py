"""Identity regression matrix uses synthetic hardware; no live router actions."""
import json
from pathlib import Path
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from modules.capture_services.openwrt.radio_identity import (IDENTITY_MARKER, STATUS_MARKER,
    radio_phy_mapping, snapshot_radio_mapping)
from modules.capture_services.openwrt.radio_identity_probe import enrich_radio_identity
from modules.capture_services.openwrt.radio_takeover import OpenWrtRadioTakeover, RadioTakeoverRecord, _parse_status_ifnames
from modules.capture_services.openwrt.command_runner import RemoteCommandResult
from modules.capture_services.openwrt.operation_diagnostics import OperationDiagnostics, sanitized_operation_evidence
from modules.capture_services.openwrt.password_verification import OpenWrtPasswordVerifier
from modules.capture_services.openwrt.verification_diagnostics import verification_phase
from modules.capture_services.openwrt.device_probe import OpenWrtDeviceProbe
from modules.display_privacy import masker_for


def snapshot(status=None, identities=None, bands=None, phys=None):
    bands = bands or {"radio7": "2g"}
    uci = "\n".join("wireless.%s=wifi-device\nwireless.%s.band='%s'" % (r, r, b) for r, b in bands.items())
    block = "" if identities is None else IDENTITY_MARKER + json.dumps(
        {"radios": identities, "phys": phys if phys is not None else sorted(set(identities.values()))}) + "\n"
    return uci + "\n__WINBOX_SNAP_IWDEV__\nphy#2\n Interface wlan2\n type managed\n" + block + STATUS_MARKER + "\n" + json.dumps(status or {})


class IdentityTests(unittest.TestCase):
    def test_all_six_official_native_lookup_contracts(self):
        rows=json.loads((Path(__file__).parent/'fixtures/official_radio_identity_contracts.json').read_text(encoding='utf-8'))
        self.assertEqual({r['series'] for r in rows}, {'19.07','21.02','22.03','23.05','24.10','25.12'})
        for row in rows:
            with self.subTest(series=row['series']):
                runner=Mock()
                runner.run.return_value=RemoteCommandResult('probe',0,
                    '__RM_PHYS__\nphy7\n__RM_PHYS_OK__\n__RM_RADIO__wifi_a\n'+json.dumps(row['reply'])+'\n__RM_END__\n')
                text=enrich_radio_identity(runner,snapshot(bands={'wifi_a':'5g'}))
                self.assertEqual(snapshot_radio_mapping(text,{}), {'wifi_a':'phy7'})

    def test_runtime_interfaces_support_many_modes_and_names_not_numbering(self):
        for names in (("wlan2",), ("phy2-ap0", "phy2-sta0", "mesh0", "guest0")):
            self.assertEqual(radio_phy_mapping({"unrelated7": set(names)}, {"phy2": names}), {"unrelated7": "phy2"})

    def test_native_identity_resolves_no_configured_or_running_interfaces(self):
        for status in ({}, {"radio7": {"up": False, "interfaces": []}}, {"radio7": {"interfaces": None}}):
            text = snapshot(status, {"radio7": "phy2"})
            self.assertEqual(snapshot_radio_mapping(text, {}), {"radio7": "phy2"})
            takeover = OpenWrtRadioTakeover(Mock())
            self.assertEqual(takeover.resolve_radio("phy2", text, expected_band="2.4G"), "radio7")
            self.assertEqual(takeover.resolve_phy("radio7", text), ("phy2", ("wlan2",)))

    def test_zero_kernel_interfaces_still_maps_only_with_verified_native_hardware(self):
        text=snapshot(identities={'radio7':'phy2'}).replace('phy#2\n Interface wlan2\n type managed\n','')
        takeover=OpenWrtRadioTakeover(Mock())
        self.assertEqual(takeover.resolve_radio('phy2',text,expected_band='2.4G'),'radio7')
        self.assertEqual(takeover.resolve_phy('radio7',text),('phy2',()))
        without_hardware=text.replace('"phys": ["phy2"]','"phys": []')
        with self.assertRaises(RuntimeError):takeover.resolve_radio('phy2',without_hardware)

    def test_legacy_hwmode_and_both_dual_band_cards_use_physical_identity(self):
        text=snapshot(identities={'radio7':'phy2','radio0':'phy8'},bands={'radio7':'2g','radio0':'5g'})
        text=text.replace(".band='2g'", ".hwmode='11g'").replace(".band='5g'", ".hwmode='11a'")
        self.assertEqual(snapshot_radio_mapping(text,{}),{'radio7':'phy2','radio0':'phy8'})
        takeover=OpenWrtRadioTakeover(Mock())
        self.assertEqual(takeover.resolve_radio('phy8',text,expected_band='5g'),'radio0')

    def test_unknown_state_and_matching_band_or_suffix_never_prove_identity(self):
        for text in (snapshot(), snapshot({"radio7": {"interfaces": []}}), snapshot(identities={"radio7": "phy99"}, phys=["phy2"])):
            self.assertEqual(snapshot_radio_mapping(text, {"phy2": ("wlan2",)}), {})
            with self.assertRaises(RuntimeError):
                OpenWrtRadioTakeover(Mock()).resolve_radio("phy2", text, expected_band="2g")

    def test_conflicting_sources_and_duplicate_claims_block_all_involved_devices(self):
        cases = [({"a": {"x"}}, {"phy2": ("x",)}, {"a": "phy3"}),
                 ({"a": {"x"}, "b": {"x"}}, {"phy2": ("x",)}, {}),
                 ({}, {}, {"a": "phy2", "b": "phy2"}),
                 ({"a": {"x", "y"}}, {"phy2": ("x",), "phy3": ("y",)}, {})]
        for status, phys, ids in cases:
            self.assertEqual(radio_phy_mapping(status, phys, ids), {})

    def test_native_and_runtime_agreement_and_unrelated_card_survives_conflict(self):
        self.assertEqual(radio_phy_mapping({"a": {"x"}, "b": {"y"}},
            {"phy2": ("x",), "phy3": ("y",)}, {"a": "phy3", "c": "phy8"}), {"c": "phy8"})
        self.assertEqual(radio_phy_mapping({"a": {"x"}}, {"phy2": ("x",)}, {"a": "phy2"}), {"a": "phy2"})

    def test_probe_has_bounded_readonly_command_and_preserves_final_status_json(self):
        runner = Mock()
        runner.run.return_value = RemoteCommandResult("radio_identity_probe", 0,
            '__RM_PHYS__\nphy2\n__RM_PHYS_OK__\n__RM_RADIO__radio7\n{"phyname":"phy2"}\n__RM_END__\n')
        text = enrich_radio_identity(runner, snapshot({"radio7": {"interfaces": [{"section": "home", "ifname": "wlan2"}]}}))
        self.assertEqual(snapshot_radio_mapping(text, {}), {"radio7": "phy2"})
        self.assertEqual(_parse_status_ifnames(text), {"home": "wlan2"})
        command = runner.run.call_args.args[1]
        self.assertIn('call iwinfo phyname', command)
        for forbidden in ('uci set', 'wifi up', 'wifi down', 'reload', 'scan', 'install'):
            self.assertNotIn(forbidden, command)
        self.assertEqual(runner.run.call_args.kwargs['timeout'], 20)
        self.assertEqual(runner.run.call_args.kwargs['output_limit'], 32768)

    def test_generated_probe_runs_in_posix_shell_without_touching_router(self):
        import os, subprocess
        bash=Path('C:/Program Files/Git/bin/bash.exe')
        if not bash.is_file():self.skipTest('Git Bash unavailable')
        runner=Mock()
        def execute(name, command, **kwargs):
            stubs = r'''ls(){ printf 'phy2\n'; }
ubus(){ test "$1" = '-S' && test "$2" = '-t' && test "$4" = 'call' && test "$5" = 'iwinfo' && test "$6" = 'phyname' || return 1
 test "$7" = '{"section":"radio7"}' || return 1
 printf '{"phyname":"phy2"}\n'
}
'''
            result=subprocess.run([str(bash),'--noprofile','--norc'],input=stubs+command,
                text=True,encoding='utf-8',capture_output=True,timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            self.assertEqual(result.returncode,0,result.stderr)
            return RemoteCommandResult(name,result.returncode,result.stdout,result.stderr)
        runner.run.side_effect=execute
        self.assertEqual(snapshot_radio_mapping(enrich_radio_identity(runner,snapshot()),{}), {'radio7':'phy2'})

    def test_incomplete_or_duplicate_native_frames_are_never_accepted(self):
        valid='__RM_PHYS__\nphy2\n__RM_PHYS_OK__\n__RM_RADIO__radio7\n{"phyname":"phy2"}\n__RM_END__\n'
        for raw in (valid.replace('__RM_END__',''), valid.replace('__RM_PHYS__','bad'),
                    valid.replace('__RM_END__','__RM_RADIO__radio7\n{"phyname":"phy8"}\n__RM_END__')):
            runner=Mock();runner.run.return_value=RemoteCommandResult('probe',0,raw)
            self.assertEqual(snapshot_radio_mapping(enrich_radio_identity(runner,snapshot()),{}),{})
        runner=Mock();runner.run.return_value=RemoteCommandResult('probe',0,valid)
        text=snapshot(bands={'radio7':'2g','radio8':'5g'})
        self.assertEqual(snapshot_radio_mapping(enrich_radio_identity(runner,text),{}),{})

    def test_current_kernel_inventory_overrules_stale_runtime_dump(self):
        text=snapshot(identities={'radio7':'phy2'},phys=['phy3']).replace('"phys": ["phy3"]','"phys": ["phy3"], "inventory_complete": true')
        self.assertEqual(snapshot_radio_mapping(text,{'phy2':('wlan2',)}),{})

    def test_refresh_replaces_previous_native_identity_instead_of_using_stale_block(self):
        runner=Mock();runner.run.return_value=RemoteCommandResult('probe',0,
            '__RM_PHYS__\nphy8\n__RM_PHYS_OK__\n__RM_RADIO__radio7\n{"phyname":"phy8"}\n__RM_END__\n')
        text=enrich_radio_identity(runner,snapshot(identities={'radio7':'phy2'}))
        self.assertEqual(text.count(IDENTITY_MARKER),1)
        self.assertEqual(snapshot_radio_mapping(text,{}),{'radio7':'phy8'})

    def test_optional_identity_timeout_does_not_fail_authenticated_device_probe(self):
        runner=Mock()
        def execute(name,*args,**kwargs):
            outputs={'system_info':"DISTRIB_RELEASE='21.02.1'\nDISTRIB_TARGET='ramips/mt7621'\n",
                     'wireless_info':snapshot(), 'storage_info':'', 'wireless_tools':'MISSING'}
            if name=='radio_identity_probe':return RemoteCommandResult(name,-1,timed_out=True)
            return RemoteCommandResult(name,0,outputs.get(name,''))
        runner.run.side_effect=execute
        device=OpenWrtDeviceProbe(runner).probe()
        self.assertEqual(device.version,'21.02.1')
        self.assertEqual(device.radios[0].name,'radio7')
        self.assertIsNone(device.radios[0].free)

    def test_oversized_probe_never_keeps_previous_native_identity(self):
        runner=Mock();runner.run.side_effect=ValueError('output limit')
        text=enrich_radio_identity(runner,snapshot(identities={'radio7':'phy2'}))
        self.assertNotIn(IDENTITY_MARKER,text)
        self.assertEqual(snapshot_radio_mapping(text,{}),{})

    def test_restore_audit_does_not_report_unknown_or_conflicting_identity_as_clean(self):
        radio=OpenWrtRadioTakeover(Mock())
        record=RadioTakeoverRecord('session','radio7','phy2',('wlan2',))
        for text in (snapshot(), snapshot({'radio7':{'interfaces':[{'ifname':'wlan2'}]}},{'radio7':'phy3'})):
            text=text.replace('__WINBOX_SNAP_IWDEV__',"wireless.home=wifi-iface\nwireless.home.device='radio7'\nwireless.home.disabled='1'\n__WINBOX_SNAP_IWDEV__")
            with patch.object(radio,'snapshot_wireless',return_value=text):
                self.assertTrue(radio.audit_restore(record))
        with patch.object(radio,'snapshot_wireless',side_effect=RuntimeError('private-secret')):
            note=radio.audit_restore(record)
            self.assertTrue(note)
            self.assertNotIn('private-secret',note)

    def test_restore_audit_still_distinguishes_clean_and_unexpected_active_interface(self):
        radio=OpenWrtRadioTakeover(Mock())
        record=RadioTakeoverRecord('session','radio7','phy2',('wlan2',))
        for disabled,has_note in (('0',False),('1',True)):
            text=snapshot({'radio7':{'interfaces':[{'section':'home','ifname':'wlan2'}]}},{'radio7':'phy2'})
            text=text.replace('__WINBOX_SNAP_IWDEV__',"wireless.home=wifi-iface\nwireless.home.device='radio7'\nwireless.home.disabled='"+disabled+"'\n__WINBOX_SNAP_IWDEV__")
            with patch.object(radio,'snapshot_wireless',return_value=text):
                self.assertEqual(bool(radio.audit_restore(record)),has_note)

    def test_cancelled_probe_never_falls_back_to_an_old_identity(self):
        runner=Mock(); event=threading.Event();event.set()
        with self.assertRaises(InterruptedError):enrich_radio_identity(runner,snapshot(),event)
        runner.run.assert_not_called()
        event.clear()
        def oversized(*args,**kwargs):
            event.set()
            raise ValueError('output limit')
        runner.run.side_effect=oversized
        with self.assertRaises(InterruptedError):enrich_radio_identity(runner,snapshot(identities={'radio7':'phy2'}),event)

    def test_probe_capacity_guard_also_removes_previous_identity(self):
        runner=Mock()
        bands={'wifi'+str(i):'2g' for i in range(65)}
        text=enrich_radio_identity(runner,snapshot(identities={'wifi0':'phy2'},bands=bands))
        self.assertNotIn(IDENTITY_MARKER,text)
        self.assertEqual(snapshot_radio_mapping(text,{}),{})
        runner.run.assert_not_called()

    def test_malformed_missing_plugin_or_overlong_response_is_not_guessed(self):
        for raw in ('', '__RM_RADIO__radio7\n{}', '__RM_RADIO__radio7\n{"phyname":"phy2;reboot"}', 'x' * 32769):
            runner = Mock(); runner.run.return_value = RemoteCommandResult('probe', 0, raw)
            text = enrich_radio_identity(runner, snapshot())
            self.assertEqual(snapshot_radio_mapping(text, {}), {})

    def test_cancel_and_timeout_do_not_use_partial_identity_response(self):
        for flags in ({'cancelled': True}, {'timed_out': True}):
            runner = Mock(); runner.run.return_value = RemoteCommandResult('probe', 0, '{}', **flags)
            with self.assertRaises((InterruptedError, TimeoutError)):
                enrich_radio_identity(runner, snapshot())

    def test_device_inventory_uses_same_identity_and_keeps_unobserved_idle_unknown(self):
        device = OpenWrtDeviceProbe._parse('', snapshot(identities={"radio7": "phy2"}) + '\n__WINBOX_IWDEV__\nphy#2\n Interface wlan2\n', '')
        self.assertEqual(device.radios[0].interfaces, ('wlan2',))
        self.assertFalse(device.radios[0].free)

    def test_band_mismatch_cannot_be_overridden_by_identity(self):
        with self.assertRaises(RuntimeError):
            OpenWrtRadioTakeover(Mock()).resolve_radio('phy2', snapshot(identities={'radio7': 'phy2'}), expected_band='5g')

    def test_verification_selection_uses_identity_and_rejects_wrong_band_fallback(self):
        verifier = OpenWrtPasswordVerifier(Mock())
        for ids, bands, desired, expected in [({'radio7':'phy2'}, {'radio7':'2g'}, '2.4G', 'radio7'),
                ({'radio7':'phy2'}, {'radio7':'5g'}, '2.4G', None),
                ({}, {'radio7':'2g'}, '2.4G', None),
                ({'radio7':'phy2','radio8':'phy3'}, {'radio7':'2g','radio8':'2g'}, '2.4G', None)]:
            with patch.object(OpenWrtRadioTakeover, 'snapshot_wireless', return_value=snapshot(identities=ids, bands=bands)):
                if expected:
                    self.assertEqual(verifier._pick_radio(desired, None), expected)
                else:
                    with self.assertRaises(RuntimeError):verifier._pick_radio(desired, None)


class PreparationDiagnosticsTests(unittest.TestCase):
    def test_primary_prepare_failure_and_restore_failure_survive_without_secret(self):
        runner = SimpleNamespace(operation_diagnostics=OperationDiagnostics())
        class Task:
            command_runner = runner
            @verification_phase('configure')
            def prepare(self):raise RuntimeError('password=private-secret SSID private-name')
            @verification_phase('restore')
            def restore(self):return 'password=private-secret'
        task=Task()
        with self.assertRaises(RuntimeError):task.prepare()
        task.restore()
        journal=runner.operation_diagnostics
        for _ in range(100):journal.record('command', status='completed')
        data=sanitized_operation_evidence(journal.snapshot(), masker_for([]))
        self.assertEqual([r['stage'] for r in data['failures']], ['configure','restore'])
        self.assertEqual(data['failures'][0]['operation'], 'verification_prepare')
        self.assertNotIn('private-secret', json.dumps(data))
        self.assertNotIn('private-name', json.dumps(data))

    def test_selection_failure_does_not_modify_or_restart_network(self):
        runner=SimpleNamespace(operation_diagnostics=OperationDiagnostics(), run=Mock())
        verifier=OpenWrtPasswordVerifier(runner)
        target=SimpleNamespace(bssid='02:11:22:33:44:55',ssid='private-network',encryption='WPA2-PSK',band='2.4G')
        with patch.object(verifier,'_backup_configs'), patch.object(verifier,'_pick_radio',side_effect=RuntimeError('identity unavailable')):
            with self.assertRaises(RuntimeError):
                verifier.verify_targets([target], ['private-password'], 'test', '.')
        runner.run.assert_not_called()
        rows=runner.operation_diagnostics.snapshot()['workflows']
        self.assertTrue(any(r.get('reason')=='configuration_untouched' for r in rows))

    def test_partial_setup_failure_still_attempts_restore(self):
        runner=Mock();runner.run.side_effect=RuntimeError('setup execution failed')
        verifier=OpenWrtPasswordVerifier(runner)
        target=SimpleNamespace(bssid='02:11:22:33:44:55',ssid='private-network',encryption='WPA2-PSK',band='2.4G')
        with patch.object(verifier,'_backup_configs'), patch.object(verifier,'_pick_radio',return_value='radio7'), \
             patch.object(verifier,'_restore',return_value='') as restore:
            with self.assertRaises(RuntimeError):verifier.verify_targets([target], ['private-password'], 'test', '.')
        restore.assert_called_once()
        self.assertTrue(verifier._configuration_touched)
        self.assertEqual(runner.run.call_args.args[0],'password_setup_env')

    def test_failed_verification_command_does_not_expose_router_output(self):
        runner=Mock();runner.run.return_value=RemoteCommandResult('password_setup_env',1,'ssid=private-name','key=private-password')
        verifier=OpenWrtPasswordVerifier(runner)
        with self.assertRaises(RuntimeError) as caught:verifier._run('password_setup_env','private-command',1,None)
        self.assertNotIn('private-',str(caught.exception))
        self.assertIn('1',str(caught.exception))

    def test_verification_command_timeout_is_classified_as_timeout(self):
        runner=SimpleNamespace(operation_diagnostics=OperationDiagnostics(),run=Mock(return_value=RemoteCommandResult('backup',-1,timed_out=True)))
        verifier=OpenWrtPasswordVerifier(runner)
        with self.assertRaises(TimeoutError):verifier._backup_configs('/tmp/test',None)
        self.assertEqual(runner.operation_diagnostics.snapshot()['failures'][-1]['status'],'timeout')

    def test_export_only_bypasses_redaction_for_exact_constant(self):
        journal=OperationDiagnostics()
        journal.record('command', operation='password_set_cred', status='completed')
        journal.record('command', operation='password=private-secret', status='failed')
        data=sanitized_operation_evidence(journal.snapshot(), masker_for([]))
        self.assertEqual(data['events'][0]['operation'], 'password_set_cred')
        self.assertNotIn('private-secret', json.dumps(data))

if __name__ == '__main__':unittest.main()
