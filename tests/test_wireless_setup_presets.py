"""Preset switching, credentials, capability gates and request round trips."""
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from tests.test_standalone import APP
from tests.test_wireless_ui import environment
from modules.capture_services.openwrt.wireless_setup_models import WirelessNetwork
from modules.capture_services.openwrt.wireless_setup_presets import ap_recommendations, relay_recommendations
from modules.main_pages.packet_capture.openwrt.signal_setup_dialogs import AccessPointSetupDialog, RelaySetupDialog
from modules.main_pages.packet_capture.openwrt.signal_setup_editor import configure_ap, configure_relay, ap_request, relay_request


def configured_environment():
    env = environment()
    env.data['features'].update(ap_11ax=True, ap_11ac=True)
    env.data['radios'][0].update(htmode='HE160', htmodes=['HE20', 'HE80', 'HE160'],
                                country='CN', txpower='17', beacon_int='300')
    env.data['rows'][0].update(hidden='1', isolate='0', wmm='0', dtim_period='7')
    return env


class PresetPolicyTests(unittest.TestCase):
    def test_each_pinned_schema_only_receives_supported_options(self):
        contracts = json.loads(Path('tests/fixtures/official_wireless_setup_contracts.json').read_text(encoding='utf-8'))
        for contract in contracts:
            env = configured_environment()
            schema = contract['get_validate']['radio7']
            env.data['radios'][0]['option_support'] = {
                scope: {key: isinstance(value, str) for key, value in schema[scope].items()}
                for scope in ('device', 'interface')}
            with self.subTest(series=contract['series'], route=contract['route']):
                values = ap_recommendations(env.data, 'radio7')
                self.assertEqual(values['htmode'], 'HE80')
                self.assertNotIn('country', values)
                self.assertNotIn('txpower', values)
                self.assertNotIn('network', values)
        env.data['radios'][0]['option_support'] = {}
        self.assertEqual(ap_recommendations(env.data, 'radio7'), {})

    def test_bands_and_unknown_capabilities_are_not_guessed(self):
        env = configured_environment()
        radio = env.data['radios'][0]
        radio.update(band='2g', channels=[], htmodes=['HE20', 'HE40'])
        self.assertEqual(ap_recommendations(env.data, 'radio7')['htmode'], 'HE20')
        radio.update(band='6g', channels=[], htmodes=['HE80', 'HE160'])
        self.assertEqual(ap_recommendations(env.data, 'radio7')['encryption'], 'sae')
        self.assertEqual(ap_recommendations(env.data, 'radio7')['ieee80211w'], '2')
        env.data['features']['ap_sae'] = False
        self.assertNotIn('encryption', ap_recommendations(env.data, 'radio7'))
        radio.pop('band')
        self.assertNotIn('htmode', ap_recommendations(env.data, 'radio7'))
        self.assertNotIn('encryption', ap_recommendations(env.data, 'radio7'))
        self.assertEqual(ap_recommendations(env.data, 'unknown-radio'), {})
        env.data['zones'] = []
        self.assertNotIn('zone', relay_recommendations(env.data))


class PresetEditorTests(unittest.TestCase):
    def test_ap_switch_changes_visible_fields_and_request_then_restores_exactly(self):
        env = configured_environment()
        row = SimpleNamespace(section='home', ssid='Home')
        d = AccessPointSetupDialog('radio7', '5G', row=row)
        try:
            configure_ap(d, env)
            d.name_edit.setText('User draft')
            d.password.edit.setText('keep-secret-123')
            before = ap_request(d)
            identity = d.ap_identity.value()
            for _ in range(8):
                d.settings_profile.setCurrentIndex(1)
                self.assertEqual(d.bandwidth_combo.currentData(), 'HE80')
                self.assertEqual(d.hidden.value(), '0')
                self.assertEqual(d.wmm.value(), '1')
                self.assertEqual(d.beacon.text(), '100')
                request = ap_request(d)
                self.assertEqual(request.options['htmode'], 'HE80')
                self.assertEqual(request.options['isolate'], '1')
                self.assertEqual(request.password, 'keep-secret-123')
                self.assertNotIn('country', request.options)
                self.assertNotIn('txpower', request.options)
                self.assertNotIn('network', request.options)
                self.assertEqual(d.name_edit.text(), 'User draft')
                self.assertEqual(d.ap_identity.value(), identity)
                d.settings_profile.setCurrentIndex(0)
                self.assertEqual(ap_request(d), before)
            self.assertEqual(len(d._editor_updates), 3)
            configure_ap(d, env)
            self.assertEqual(len(d._editor_updates), 3)
        finally:
            d.reject(); d.deleteLater(); APP.processEvents()

    def test_recommended_country_is_capability_gated_and_restores_existing(self):
        env = configured_environment()
        radio = env.data['radios'][0]
        radio['countries'] = [{'iso3166': 'CN', 'country': 'China'},
                              {'iso3166': 'TW', 'country': 'Taiwan'}]
        d = AccessPointSetupDialog('radio7', '5G')
        try:
            configure_ap(d, env)
            d.name_edit.setText('Country test')
            d.password.edit.setText('password-123')
            self.assertIsNone(d.country.currentData())
            d.settings_profile.setCurrentIndex(1)
            self.assertEqual(d.country.currentData(), 'TW')
            self.assertEqual(ap_request(d).options['country'], 'TW')
            d.settings_profile.setCurrentIndex(0)
            self.assertIsNone(d.country.currentData())
            self.assertNotIn('country', ap_request(d).options)
            radio['option_support']['device']['country'] = False
            self.assertNotIn('country', ap_recommendations(env.data, 'radio7'))
            radio['option_support']['device']['country'] = True
            radio['countries'] = [{'code': 'US'}]
            self.assertNotIn('country', ap_recommendations(env.data, 'radio7'))
            radio['countries'] = [{'code': 'TW'}]
            self.assertEqual(ap_recommendations(env.data, 'radio7')['country'], 'TW')
        finally:
            d.reject(); d.deleteLater(); APP.processEvents()

    def test_relay_static_settings_restore_without_losing_password_or_identity(self):
        env = configured_environment()
        env.data['rows'].append(dict(section='uplink', mode='sta', device='radio7',
                                     network='guest', description='Saved label'))
        env.data['networks'][1].update(proto='static', ipaddr='192.0.2.3',
            netmask='255.255.255.0', gateway='192.0.2.1', dns=['192.0.2.1'])
        row = SimpleNamespace(section='uplink', running='connected')
        d = RelaySetupDialog('Upstream', radio_name='radio7', password='secret-123', row=row)
        target = WirelessNetwork('radio7', '02:11:22:33:44:55', 'Upstream', 36, '5G', -40, 'psk2')
        try:
            configure_relay(d, env)
            d.name_edit.setText('User draft')
            before = relay_request(d, target, 'proof')
            identity = d.identity_fields.values()
            for _ in range(8):
                d.settings_profile.setCurrentIndex(1)
                self.assertEqual(d.protocol.currentData(), 'dhcp')
                self.assertEqual(d.bridge_mode.currentData(), 'routed')
                self.assertEqual(d.dns.values(), [])
                self.assertFalse(d.address.isEnabled())
                recommended = relay_request(d, target, 'proof')
                self.assertEqual(recommended.options['proto'], 'dhcp')
                self.assertNotIn('ipaddr', recommended.options)
                self.assertEqual(recommended.label, 'User draft')
                self.assertEqual(recommended.password, 'secret-123')
                self.assertEqual(d.identity_fields.values(), identity)
                d.settings_profile.setCurrentIndex(0)
                self.assertTrue(d.address.isEnabled())
                self.assertEqual(relay_request(d, target, 'proof'), before)
            self.assertEqual(len(d._editor_updates), 2)
        finally:
            d.reject(); d.deleteLater(); APP.processEvents()


class PresetNativeSaveTests(unittest.TestCase):
    def test_recommended_ap_request_reaches_existing_lua_save_and_keeps_secret(self):
        from dataclasses import replace
        from threading import Event
        from unittest.mock import Mock
        from tests.test_wireless_setup import LuaDevice
        from modules.capture_services.openwrt.wireless_setup_manager import WirelessSetupManager
        device = LuaDevice()
        device.config['wireless']['radio7'].update(htmode='HE160', country='CN', txpower='17')
        device.config['wireless']['home'].update(hidden='1', wmm='0', dtim_period='7')
        runner = SimpleNamespace(run=device.run, ssh_client=SimpleNamespace(host_fingerprint='device-one'))
        manager = WirelessSetupManager(runner)
        event = Event()
        env = manager.inspect(event)
        d = AccessPointSetupDialog('radio7', '5G', row=SimpleNamespace(section='home', ssid='Home'))
        try:
            configure_ap(d, env)
            d.settings_profile.setCurrentIndex(1)
            request = replace(ap_request(d), revision=env.data['revision'])
            manager.save(request, event, Mock())
            radio, row = device.config['wireless']['radio7'], device.config['wireless']['home']
            self.assertEqual(radio['htmode'], 'HE80')
            self.assertEqual((radio['country'], radio['txpower'], radio['channel']), ('CN', '17', '36'))
            self.assertEqual((row['wmm'], row['hidden'], row['dtim_period']), ('1', '0', '2'))
            self.assertEqual(row['key'], 'original-password')
            self.assertIn(('uci', 'confirm'), device.calls)
        finally:
            d.reject(); d.deleteLater(); APP.processEvents()
