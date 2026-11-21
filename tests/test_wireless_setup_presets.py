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
                                country='CN', txpower='17', beacon_int='300',
                                runtime_complete=True, runtime_pending=False)
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

    def test_auto_channel_requires_confirmed_capability(self):
        env = configured_environment()
        env.data["features"]["ap_acs"] = True
        self.assertEqual(ap_recommendations(env.data, "radio7")["channel"], "auto")
        d = AccessPointSetupDialog("radio7", "5G")
        try:
            configure_ap(d, env)
            self.assertIsNone(d.channel.currentData())
            d.settings_profile.setCurrentIndex(1)
            self.assertEqual(d.channel.currentData(), "auto")
            d.settings_profile.setCurrentIndex(0)
            self.assertIsNone(d.channel.currentData())
        finally:
            d.reject()
        env.data["features"]["ap_acs"] = False
        self.assertNotIn("channel", ap_recommendations(env.data, "radio7"))

    def test_auto_channel_recommendation_preserves_station_radio(self):
        env = configured_environment()
        env.data["features"]["ap_acs"] = True
        env.data["rows"].append(dict(section="upper", device="radio7", mode="sta"))
        self.assertNotIn("channel", ap_recommendations(env.data, "radio7"))
        env.data["rows"][-1]["device"] = "another"
        self.assertEqual(ap_recommendations(env.data, "radio7")["channel"], "auto")

    def test_disabled_station_allows_auto_only_if_not_running(self):
        for disabled, active, expected in (
            ("1", [], True), ("true", [], True),
            ("0", [], False), ("false", [], False), (None, [], False),
            ("yes", [], False), ("1", ["upper"], False), ("1", None, False),
        ):
            with self.subTest(disabled=disabled, active=active):
                env = configured_environment()
                env.data["features"]["ap_acs"] = True
                env.data["active"] = active
                env.data["rows"].append(dict(section="upper", device="radio7",
                                             mode="sta", disabled=disabled))
                values = ap_recommendations(env.data, "radio7")
                self.assertEqual(values.get("channel") == "auto", expected)

    def test_disabled_station_requires_complete_stable_runtime(self):
        for complete, pending, expected in ((True, False, "auto"),
                                             (False, True, None),
                                             (True, True, None),
                                             (True, None, None),
                                             (False, False, None),
                                             (None, False, None)):
            with self.subTest(complete=complete, pending=pending):
                env = configured_environment()
                env.data["features"]["ap_acs"] = True
                env.data["radios"][0].update(runtime_complete=complete, runtime_pending=pending)
                env.data["rows"].append(dict(section="upper", device="radio7", mode="sta", disabled="1"))
                self.assertEqual(ap_recommendations(env.data, "radio7").get("channel"), expected)

    def test_disabled_station_recommendation_updates_editor_and_restores(self):
        env = configured_environment()
        env.data["features"]["ap_acs"] = True
        env.data["rows"].append(dict(section="upper", device="radio7",
                                     mode="sta", disabled="1"))
        d = AccessPointSetupDialog("radio7", "5G")
        try:
            configure_ap(d, env)
            self.assertIsNone(d.channel.currentData())
            d.settings_profile.setCurrentIndex(1)
            self.assertEqual(d.channel.currentData(), "auto")
            d.settings_profile.setCurrentIndex(0)
            self.assertIsNone(d.channel.currentData())
        finally:
            d.reject()

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
            self.assertEqual(len(d._editor_updates), 2)
            configure_ap(d, env)
            self.assertEqual(len(d._editor_updates), 2)
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
    def test_runtime_completeness_and_staging_recheck(self):
        from threading import Event
        from unittest.mock import patch
        from tests.test_wireless_setup import LuaDevice
        from modules.capture_services.openwrt.wireless_setup_manager import WirelessSetupManager
        from modules.capture_services.openwrt.wireless_setup_models import WirelessRequest
        device = LuaDevice()
        device.config["wireless"]["upper"] = {
            ".type": "wifi-iface", "ssid": "Upper", "device": "radio7", "mode": "sta",
            "network": "wan", "encryption": "psk2", "disabled": "1"}
        manager = WirelessSetupManager(device)
        event = Event()
        stable = manager.inspect(event)
        self.assertTrue(stable.data["radios"][0]["runtime_complete"])
        self.assertEqual(ap_recommendations(stable.data, "radio7").get("channel"), "auto")
        original = device.call
        def pending(obj, method, args):
            value, error = original(obj, method, args)
            if obj == "network.wireless" and method == "status" and not error:
                value["radio7"]["pending"] = True
            return value, error
        device.lua.globals().bridge_call = pending
        unstable = manager.inspect(event)
        self.assertFalse(unstable.data["radios"][0]["runtime_complete"])
        self.assertNotIn("channel", ap_recommendations(unstable.data, "radio7"))
        def unavailable(obj, method, args):
            if obj == "network.device" and method == "status":
                return None, 2
            return original(obj, method, args)
        device.lua.globals().bridge_call = unavailable
        incomplete = manager.inspect(event)
        self.assertFalse(incomplete.data["radios"][0]["runtime_complete"])
        self.assertNotIn("channel", ap_recommendations(incomplete.data, "radio7"))
        device.lua.globals().bridge_call = original
        request = WirelessRequest("ap", "radio7", "Home", section="home",
                                  options={"channel": "auto", "recommended_auto": True, "encryption": "psk2"})
        call = manager._call
        def alter_before_stage(action, *args, **kwargs):
            if action == "stage":
                device.lua.globals().bridge_call = pending
            return call(action, *args, **kwargs)
        with patch.object(manager, "_call", side_effect=alter_before_stage):
            with self.assertRaises(Exception):
                manager.save(request, event, lambda *_: None, apply=False)
        self.assertEqual(device.config["wireless"]["radio7"]["channel"], "36")

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
            self.assertEqual((radio['country'], radio['txpower'], radio['channel']), ('CN', '17', 'auto'))
            self.assertEqual((row['wmm'], row['hidden'], row['dtim_period']), ('1', '0', '2'))
            self.assertEqual(row['key'], 'original-password')
            self.assertIn(('uci', 'confirm'), device.calls)
        finally:
            d.reject(); d.deleteLater(); APP.processEvents()
