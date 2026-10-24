"""Random AP addresses stay stable, persist, and must match the running interface."""
import copy
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from tests.test_standalone import APP
from tests.test_wireless_ui import environment
from tests.test_wireless_setup import LuaDevice, LuaRuntime
from tests.test_save_only import PersistenceDevice
from modules.capture_services.openwrt.wireless_identity import generate_unique_mac, valid_mac
from modules.capture_services.openwrt.wireless_setup_models import WirelessRequest, validate_request
from modules.capture_services.openwrt.wireless_setup_manager import WirelessSetupManager
from modules.capture_services.openwrt.signal_manager import SignalError
from modules.main_pages.packet_capture.openwrt.signal_setup_dialogs import AccessPointSetupDialog
from modules.main_pages.packet_capture.openwrt.signal_setup_editor import configure_ap, ap_request
from modules.ui_scale_manager import UiScaleManager
from PySide6.QtWidgets import QWidget


class ApAddressUiTests(unittest.TestCase):
    def make_dialog(self, *, old=None, light=True, scale=100):
        parent = QWidget()
        parent.scale_manager = UiScaleManager(scale)
        parent.light_theme = light
        parent.setProperty('settingsLightTheme', light)
        parent.setStyleSheet('QWidget { background: #f5f6fa; }' if light else 'QWidget { background: #181b1f; }')
        row = NS(section='home', ssid='Home') if old is not None else None
        dialog = AccessPointSetupDialog('radio7', '5G', parent, row=row)
        env = environment()
        if old is not None:env.data['rows'][0].update(old)
        configure_ap(dialog, env)
        dialog.name_edit.setText('Home')
        dialog.password.edit.setText('test-password')
        self.addCleanup(parent.deleteLater)
        self.addCleanup(dialog.reject)
        return dialog, env

    def test_new_default_is_random_stable_and_regenerate_changes_only_address(self):
        dialog, env = self.make_dialog()
        first = ap_request(dialog)
        address = first.options['macaddr']
        self.assertTrue(valid_mac(address))
        self.assertEqual(int(address[:2], 16) & 3, 2)
        configure_ap(dialog, env)
        self.assertEqual(ap_request(dialog).options['macaddr'], address)
        dialog.ap_identity.regenerate.click()
        second = ap_request(dialog)
        self.assertNotEqual(second.options['macaddr'], address)
        self.assertEqual(first.ssid, second.ssid)
        self.assertEqual(first.password, second.password)

    def test_edit_keeps_explicit_or_system_address_until_regenerated(self):
        for old in ({'macaddr': '02:11:22:33:44:55'}, {}):
            dialog, _ = self.make_dialog(old=old)
            self.assertNotIn('macaddr', ap_request(dialog).options)
            self.assertEqual(dialog.ap_identity.address.text(), old.get('macaddr', ''))
            dialog.ap_identity.regenerate.click()
            self.assertIn('macaddr', ap_request(dialog).options)

    def test_unsupported_new_is_blocked_but_existing_settings_are_editable(self):
        for old in (None, {}):
            dialog, env = self.make_dialog(old=old)
            env.data['radios'][0]['option_support']['interface']['macaddr'] = False
            configure_ap(dialog, env)
            self.assertFalse(dialog.ap_identity.regenerate.isEnabled())
            if old is None:
                self.assertFalse(dialog.save_button.isEnabled())
                self.assertFalse(dialog.save_only_button.isEnabled())
                with self.assertRaises(ValueError):ap_request(dialog)
            else:
                self.assertTrue(dialog.save_button.isEnabled())
                self.assertNotIn('macaddr', ap_request(dialog).options)

    def test_generation_skips_known_addresses_and_is_bounded(self):
        with patch('modules.capture_services.openwrt.wireless_identity.generate_mac', side_effect=['02:11:22:33:44:55', '06:11:22:33:44:55']):
            self.assertEqual(generate_unique_mac(['02:11:22:33:44:55']), '06:11:22:33:44:55')
        with patch('modules.capture_services.openwrt.wireless_identity.generate_mac', return_value='02:11:22:33:44:55') as generate:
            with self.assertRaises(ValueError):generate_unique_mac(['02:11:22:33:44:55'])
            self.assertEqual(generate.call_count, 32)

    def test_real_paint_both_themes_and_scales(self):
        output = Path('build/ap-identity-preview')
        output.mkdir(parents=True, exist_ok=True)
        for light in (False, True):
            for scale in (100, 150):
                dialog, _ = self.make_dialog(light=light, scale=scale)
                dialog.resize(UiScaleManager.for_widget(dialog).px(780), UiScaleManager.for_widget(dialog).px(650))
                dialog.show()
                APP.processEvents()
                field = dialog.ap_identity
                self.assertFalse(field.address.isVisible())
                dialog.common_section.set_expanded(True, animate=False)
                APP.processEvents()
                self.assertTrue(field.address.isVisible())
                self.assertTrue(field.regenerate.isVisible())
                self.assertGreaterEqual(field.address.width(), field.address.fontMetrics().horizontalAdvance(field.address.text())+16)
                self.assertGreaterEqual(field.regenerate.width(), field.regenerate.fontMetrics().horizontalAdvance(field.regenerate.text()))
                self.assertTrue(dialog.grab().save(str(output/f'{"light" if light else "dark"}-{scale}.png')))


class ApDevice(LuaDevice):
    def __init__(self):
        super().__init__()
        self.wrong_mac = False

    def call(self, obj, method, arguments):
        args = self.unwrap(arguments)
        if obj == 'network.device' and method == 'status':
            section = args.get('name', '').removeprefix('iface-')
            mac = self.config['wireless'].get(section, {}).get('macaddr', '00:11:22:33:44:66')
            return self.wrap(dict(up=True, macaddr='02:00:00:00:00:99' if self.wrong_mac else mac)), None
        return super().call(obj, method, arguments)


@unittest.skipUnless(LuaRuntime, 'Lua runtime unavailable')
class ApAddressTransactionTests(unittest.TestCase):
    def setUp(self):
        self.device = ApDevice()
        self.manager = WirelessSetupManager(NS(run=self.device.run))
        self.cancel = threading.Event()
        self.request = WirelessRequest('ap', 'radio7', 'NewWiFi', 'test-password',
            options=dict(encryption='psk2', network='lan', macaddr='02:11:22:33:44:55'))

    def test_address_persists_and_is_checked_on_later_start(self):
        result = self.manager.save(self.request, self.cancel, Mock())
        section = result['section']
        self.assertEqual(self.device.config['wireless'][section]['macaddr'], self.request.options['macaddr'])
        status = self.manager._call('status', self.cancel, radio='radio7', section=section, mode='ap')
        self.assertTrue(status['ready'])
        self.device.wrong_mac = True
        status = self.manager._call('status', self.cancel, radio='radio7', section=section, mode='ap')
        self.assertFalse(status['ready'])
        self.assertTrue(status['identity_pending'])

    def test_duplicate_and_invalid_addresses_never_apply(self):
        self.device.config['wireless']['home']['macaddr'] = self.request.options['macaddr']
        original = copy.deepcopy(self.device.config)
        with self.assertRaises(SignalError) as error:self.manager.save(self.request, self.cancel, Mock())
        self.assertEqual(error.exception.code, 'identity_duplicate')
        self.assertEqual(self.device.config, original)
        self.assertNotIn(('uci', 'apply'), self.device.calls)
        for mac in ('01:11:22:33:44:55', '00:00:00:00:00:00', 'bad', None):
            with self.assertRaises(ValueError):validate_request(replace(self.request, options=dict(self.request.options, macaddr=mac)))

    def test_runtime_mismatch_rolls_back_to_original_configuration(self):
        original = copy.deepcopy(self.device.config)
        self.device.wrong_mac = True
        def check(request, section, cancel, *args, **kwargs):
            status = self.manager._call('status', cancel, radio='radio7', section=section, mode='ap', expected_mac=kwargs['expected_mac'])
            self.assertFalse(status['ready'])
            raise SignalError('unconfirmed', 'Address did not take effect')
        with patch.object(self.manager, '_wait_ready', side_effect=check), self.assertRaises(SignalError):
            self.manager.save(self.request, self.cancel, Mock())
        self.assertEqual(self.device.config, original)
        self.assertFalse(self.device.sessions)

    def test_existing_system_random_policy_can_still_be_edited(self):
        self.device.config['wireless']['home']['macaddr'] = 'random'
        request = WirelessRequest('ap', 'radio7', 'Renamed', section='home', options={'encryption': 'psk2'})
        result = self.manager.save(request, self.cancel, Mock())
        self.assertEqual(self.device.config['wireless'][result['section']]['macaddr'], 'random')

    def test_inventory_exports_runtime_address_for_old_address_exclusion(self):
        env = self.manager.inspect(self.cancel)
        row = next(row for row in env.data['rows'] if row['section']=='home')
        self.assertEqual(row['runtime_mac'], '00:11:22:33:44:66')

    def test_random_address_on_all_official_version_status_routes(self):
        from tests.test_wireless_version_contracts import VersionMixin, PROFILES
        class VersionApDevice(VersionMixin, ApDevice):
            pass
        for profile in PROFILES:
            with self.subTest(series=profile['series'], route=profile['route']):
                device = VersionApDevice(profile)
                manager = WirelessSetupManager(NS(run=device.run))
                result = manager.save(self.request, self.cancel, Mock())
                section = result['section']
                self.assertEqual(device.config['wireless'][section]['macaddr'], self.request.options['macaddr'])
                device.wrong_mac = True
                self.assertFalse(manager._call('status', self.cancel, radio='radio7', section=section, mode='ap')['ready'])

    def test_save_only_persists_address_without_applying_wireless(self):
        device = PersistenceDevice()
        manager = WirelessSetupManager(NS(run=device.run))
        runtime = copy.deepcopy(device.runtime)
        result = manager.save(self.request, self.cancel, Mock(), apply=False)
        row = device.config['wireless'][result['section']]
        self.assertEqual(row['macaddr'], self.request.options['macaddr'])
        self.assertEqual(row['disabled'], '1')
        self.assertEqual(device.runtime, runtime)
        self.assertNotIn(('uci', 'apply'), device.calls)
