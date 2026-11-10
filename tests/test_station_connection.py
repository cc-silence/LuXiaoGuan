"""Authenticated connection, target matching and capability fallback regressions."""
import unittest
from threading import Event
from unittest.mock import Mock
from types import SimpleNamespace as NS
from modules.capture_services.openwrt.station_connection import verdict, status_values, enrich
from modules.capture_services.openwrt.command_runner import RemoteCommandResult
from tests import test_wireless_runtime_status as runtime_fixture
from tests.test_wireless_runtime_status import row, sample

BSSID='02:11:22:33:44:55'


def configured(**kwargs):
    return dict(row(),configured_ssid='Upstream',configured_bssid='',**kwargs)


def completed(**kwargs):
    return dict(dict(wpa_state='COMPLETED',ssid='Upstream',bssid=BSSID),**kwargs)


class StationConnectionTests(unittest.TestCase):
    def test_only_completed_authentication_with_matching_target_succeeds(self):
        for state in ['SCANNING','AUTHENTICATING','ASSOCIATING','ASSOCIATED','4WAY_HANDSHAKE','GROUP_HANDSHAKE']:
            with self.subTest(state=state):
                self.assertEqual(verdict(completed(wpa_state=state),configured()),'connecting')
        for state in ['DISCONNECTED','INTERFACE_DISABLED','INACTIVE']:
            self.assertEqual(verdict(completed(wpa_state=state),configured()),'disconnected')
        self.assertEqual(verdict(completed(),configured()),'connected')
        for value in [None,{},dict(wpa_state=[]),completed(wpa_state='UNKNOWN'),completed(bssid=''),
                      completed(bssid='00:00:00:00:00:00'),completed(bssid='ff:ff:ff:ff:ff:ff'),
                      completed(ssid='Other')]:
            self.assertEqual(verdict(value,configured()),'unknown')
        r=configured();r['configured_bssid']='02:aa:bb:cc:dd:ee'
        self.assertEqual(verdict(completed(),r),'unknown')

    def test_missing_driver_capability_does_not_negate_completed_authentication(self):
        r=configured();r.update(bssid='',associated=False,association_known=False,authentication=completed())
        runner=Mock();result=enrich(dict(networks=[r]),runner,Event())['networks'][0]
        self.assertEqual(result['connection_state'],'connected')
        self.assertTrue(result['associated']);self.assertTrue(result['association_known'])
        self.assertEqual(result['bssid'],BSSID);runner.run.assert_not_called()

    def test_ssid_escaping_and_unicode_are_compared_as_bytes(self):
        r=configured();r['configured_ssid']='Cafe\\room'
        self.assertEqual(verdict(completed(ssid=r'Cafe\\room'),r),'connected')
        r['configured_ssid']=bytes.fromhex('e4b8ade69687').decode('utf-8')
        self.assertEqual(verdict(completed(ssid=r'\xe4\xb8\xad\xe6\x96\x87'),r),'connected')
        self.assertEqual(verdict(completed(ssid=r'\xZZ'),r),'unknown')

    def test_native_partial_or_contradictory_result_never_falls_back_to_driver_success(self):
        for status in [{},completed(ssid='Other'),completed(wpa_state='ASSOCIATED')]:
            runner=Mock();r=configured(authentication=status)
            result=enrich(dict(networks=[r]),runner,Event())['networks'][0]
            self.assertNotEqual(result['connection_state'],'connected');runner.run.assert_not_called()

    def test_old_cli_fallback_is_readonly_bounded_and_authenticates_current_interface(self):
        runner=Mock();runner.run.return_value=RemoteCommandResult('test',0,'wpa_state=COMPLETED\nssid=Upstream\nbssid='+BSSID+'\n')
        r=configured();result=enrich(dict(networks=[r]),runner,Event())['networks'][0]
        self.assertEqual(result['connection_state'],'connected')
        command=runner.run.call_args.args[1]
        self.assertIn('-i phy1-sta0 status',command)
        self.assertNotIn('reconnect',command);self.assertNotIn('set_network',command)
        self.assertLessEqual(runner.run.call_args.kwargs['timeout'],2)
        self.assertEqual(runner.run.call_args.kwargs['output_limit'],8192)
        self.assertNotIn('authentication',result);self.assertNotIn('configured_ssid',result)

    def test_missing_tool_or_timeout_is_unknown_and_skips_remaining_cli_queries(self):
        for result in [RemoteCommandResult('test',127),RemoteCommandResult('test',-1,timed_out=True),
                       RemoteCommandResult('test',0,'wpa_state=COMPLETED\n',failure_stage='execute')]:
            runner=Mock();runner.run.return_value=result
            data=enrich(dict(networks=[configured(),configured()]),runner,Event())
            self.assertTrue(all(r['connection_state']=='unknown' for r in data['networks']))
            if result.exit_code==127 or result.timed_out:self.assertEqual(runner.run.call_count,1)

    def test_total_budget_cancellation_duplicate_status_and_invalid_interface_are_safe(self):
        runner=Mock()
        data=enrich(dict(networks=[configured()]),runner,Event(),clock=Mock(side_effect=[0,5]))
        runner.run.assert_not_called();self.assertEqual(data['networks'][0]['connection_state'],'unknown')
        cancel=Event();cancel.set()
        with self.assertRaises(InterruptedError):enrich(dict(networks=[configured()]),runner,cancel)
        self.assertFalse(status_values('wpa_state=COMPLETED\nwpa_state=DISCONNECTED\n'))
        self.assertFalse(status_values('x'*8193))
        r=configured();r['interface']='bad; reboot'
        enrich(dict(networks=[r]),runner,Event());runner.run.assert_not_called()

    def test_model_without_authentication_is_unknown_and_drops_cached_connection_identity(self):
        r=row();r.pop('connection_state')
        self.assertEqual(sample(r).networks[0].connection_state,'unknown')
        self.assertNotEqual(sample(r).networks[0].identity,sample(row()).networks[0].identity)

    def test_real_lua_native_completed_connecting_disconnected_and_mismatch(self):
        fixture=runtime_fixture.RuntimeCollectorLuaTests()
        for state,expected in [('COMPLETED','connected'),('4WAY_HANDSHAKE','connecting'),('DISCONNECTED','disconnected')]:
            result=fixture.collect(authentication=completed(wpa_state=state),configured_ssid='Upstream',enriched=True)
            sta=next(n for n in result.networks if n.mode=='sta')
            self.assertEqual(sta.connection_state,expected)
            self.assertNotEqual(next(n for n in result.networks if n.mode=='ap').mode,'sta')
        for options in [dict(authentication=completed(ssid='Other')),
                        dict(authentication=completed(),locked_bssid='02:aa:bb:cc:dd:ee'),
                        dict(authentication=None)]:
            result=fixture.collect(configured_ssid='Upstream',enriched=True,**options)
            self.assertEqual(next(n for n in result.networks if n.mode=='sta').connection_state,'unknown')

    def test_manager_sample_enriches_native_or_legacy_data_before_publishing(self):
        import json
        from modules.capture_services.openwrt.wireless_status_manager import WirelessStatusManager
        for native in (False,True):
            r=configured()
            if native:r['authentication']=completed()
            runner=Mock()
            result=RemoteCommandResult('wireless_status',0,json.dumps(dict(ok=True,result=dict(schema=1,networks=[r]))))
            runner.run.side_effect=[result,RemoteCommandResult('auth',0,'wpa_state=COMPLETED\nssid=Upstream\nbssid='+BSSID+'\n')]
            manager=WirelessStatusManager(NS(runner=runner,_lua_bin='lua'))
            state=manager._sample(Event()).networks[0]
            self.assertEqual(state.connection_state,'connected')
            self.assertEqual(runner.run.call_count,1 if native else 2)

    def test_incomplete_authentication_never_probes_internet_or_reuses_old_online_result(self):
        from dataclasses import replace
        from modules.capture_services.openwrt.wireless_status_models import InternetStatus
        from modules.capture_services.openwrt.wireless_status_manager import WirelessStatusManager
        for state in ('unknown','connecting','disconnected'):
            runner=Mock();manager=WirelessStatusManager(NS(runner=runner,_lua_bin='lua'))
            r=row();r['connection_state']=state
            manager._sample=Mock(return_value=sample(r))
            cancel=Mock();cancel.is_set.return_value=False;cancel.wait.return_value=False
            from datetime import datetime,timezone
            old=sample(row())
            old=replace(old,networks=(replace(old.networks[0],internet=InternetStatus('online','https_verified',datetime.now(timezone.utc).isoformat(),4,1)),))
            result=manager.read(cancel,previous=old,force_internet=False)
            self.assertNotEqual(result.networks[0].internet.state,'online')
            runner.run.assert_not_called()

    def test_each_supported_release_uses_same_evidence_rules_and_actual_interface_names(self):
        import json
        from pathlib import Path
        contracts=json.loads(Path('tests/fixtures/official_station_connection_contracts.json').read_text(encoding='utf-8'))
        self.assertEqual(len(contracts['releases']),50)
        for contract in contracts['releases']:
            with self.subTest(release=contract['release']):
                r=configured();r['interface']='phy7-sta0' if contract['native_status_verified'] else 'wlan0-1'
                runner=Mock();runner.run.return_value=RemoteCommandResult('auth',0,'wpa_state=COMPLETED\nssid=Upstream\nbssid='+BSSID+'\n')
                if contract['native_status_verified']:r['authentication']=completed()
                result=enrich(dict(networks=[r]),runner,Event())['networks'][0]
                self.assertEqual(result['connection_state'],'connected')
                self.assertTrue(contract['wpa_cli_package_available'])

    def test_driver_self_mac_does_not_veto_verified_supplicant_peer(self):
        for native in (True,False):
            r=configured();r['bssid']='02:aa:bb:cc:dd:ee';r['configured_bssid']=BSSID
            runner=Mock();runner.run.return_value=RemoteCommandResult('auth',0,'wpa_state=COMPLETED\nssid=Upstream\nbssid='+BSSID+'\n')
            if native:r['authentication']=completed()
            result=enrich(dict(networks=[r]),runner,Event())['networks'][0]
            self.assertEqual(result['connection_state'],'connected')
            self.assertEqual(result['bssid'],BSSID)
            if native:runner.run.assert_not_called()

    def test_unlocked_profile_uses_authenticated_peer_but_locked_target_remains_mandatory(self):
        r=configured();other=completed(bssid='02:aa:bb:cc:dd:ee')
        self.assertEqual(verdict(other,r),'connected')
        r['configured_bssid']=BSSID
        self.assertEqual(verdict(other,r),'unknown')
        self.assertEqual(verdict(completed(wpa_state='4WAY_HANDSHAKE'),r),'connecting')
