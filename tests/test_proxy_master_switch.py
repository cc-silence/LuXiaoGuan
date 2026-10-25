"""Saved proxy master switch gates endpoints, system discovery and real UI state."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch, Mock
import unittest
from modules.config_services.proxy_config_manager import ProxyConfigManager, normalize_proxy_data


class ProxyMasterConfigTests(unittest.TestCase):
    def test_disabled_endpoints_are_preserved_and_restored(self):
        with TemporaryDirectory() as folder:
            path = Path(folder)/'proxy.json'
            manager = ProxyConfigManager(path)
            manager.set_proxy(dict(enabled=False, http_host='127.0.0.1', http_port='8123',
                                   socks_host='127.0.0.1', socks_port='8124'))
            manager.save()
            reopened = ProxyConfigManager(path)
            self.assertIs(reopened.to_dict()['enabled'], False)
            self.assertEqual(reopened.get_http_url(), '')
            self.assertEqual(reopened.get_socks_url(), '')
            self.assertFalse(reopened.has_any_proxy())
            data = reopened.to_dict()
            self.assertEqual(data['http_host'], '127.0.0.1')
            reopened.set_proxy(dict(data, enabled=True))
            self.assertEqual(reopened.get_http_url(), 'http://127.0.0.1:8123')

    def test_disabled_switch_also_blocks_environment_and_windows_proxy(self):
        from modules import network_policy as policy
        from modules.capture_services.openwrt.signal_package_feed import _proxy_candidates
        manager = ProxyConfigManager(Path('unused-proxy-switch-fixture.json'))
        manager.from_dict(dict(enabled=False, http_host='127.0.0.1', http_port='8123'))
        with patch.object(policy, '_load_proxy_manager', return_value=manager), \
             patch.object(policy, '_windows_system_proxy_url', return_value='http://windows:123') as windows, \
             patch.dict(os.environ, {'HTTPS_PROXY':'http://environment:123'}):
            self.assertEqual(policy.winbox_http_url(), '')
            self.assertEqual(policy.system_proxy_url(), '')
            self.assertEqual(policy.configured_proxy_channels(), [])
            self.assertEqual([c.name for c in policy.allowed_channels(None, 'proxy')], ['direct'])
            self.assertEqual(_proxy_candidates(), [])
            windows.assert_not_called()

    def test_old_single_and_legacy_dual_proxy_config_keep_behavior(self):
        old = normalize_proxy_data(dict(http_host='127.0.0.1', http_port='8123'))
        self.assertTrue(old['enabled'])
        dual = normalize_proxy_data(dict(enabled=False, primary=dict(http_host='old', http_port='1')))
        self.assertFalse(dual['enabled'])
        self.assertEqual(dual['http_host'], 'old')

    def test_invalid_saved_port_cannot_prevent_turning_proxy_off(self):
        manager = ProxyConfigManager(Path('unused-proxy-switch-fixture.json'))
        manager.set_proxy(dict(enabled=False, http_host='127.0.0.1', http_port='70000'))
        self.assertEqual(manager.get_http_url(), '')
        with self.assertRaises(ValueError):
            manager.set_proxy(dict(manager.to_dict(), enabled=True))
        self.assertFalse(manager.is_enabled())


class ProxyMasterUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests import test_standalone
        cls.standalone = test_standalone
        test_standalone.StandaloneTests.setUpClass()
        cls.window = test_standalone.StandaloneTests.window

    @classmethod
    def tearDownClass(cls):
        cls.standalone.StandaloneTests.tearDownClass()

    def open_proxy(self, manager):
        with patch('router_app.settings.ProxyConfigManager', return_value=manager):
            self.window.openSettingsWindow()
        settings = self.window.settings_window
        settings.pages.setCurrentIndex(1)
        self.standalone.APP.processEvents()
        return settings

    def test_toggle_auto_save_reopen_import_and_input_availability(self):
        with TemporaryDirectory() as folder:
            path = Path(folder)/'proxy.json'
            manager = ProxyConfigManager(path)
            manager.set_proxy(dict(http_host='127.0.0.1', http_port='8123'))
            manager.save()
            settings = self.open_proxy(manager)
            settings.proxy_enabled_check.setChecked(False)
            self.assertTrue(all(not w.isEnabled() for w in settings.proxy_inputs.values()))
            self.assertEqual(ProxyConfigManager(path).get_http_url(), '')
            self.window.navigation.show_router()
            reopened = self.open_proxy(ProxyConfigManager(path))
            self.assertFalse(reopened.proxy_enabled_check.isChecked())
            self.assertEqual(reopened.proxy_inputs['http_host'].text(), '127.0.0.1')
            reopened.proxy_enabled_check.setChecked(True)
            self.assertTrue(all(w.isEnabled() for w in reopened.proxy_inputs.values()))
            self.assertEqual(ProxyConfigManager(path).get_http_url(), 'http://127.0.0.1:8123')
            reopened._proxy_replace_imported_data(dict(enabled=False, http_host='saved-host', http_port='8124'))
            self.assertFalse(reopened.proxy_enabled_check.isChecked())
            self.assertFalse(reopened.proxy_inputs['http_host'].isEnabled())
            self.assertEqual(ProxyConfigManager(path).to_dict()['http_host'], 'saved-host')
            self.window.navigation.show_router()

    def test_actual_render_both_themes_scales_and_switch_states(self):
        output = Path('build/proxy-master-switch-review')
        output.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory() as folder:
            for percent in (100, 125, 150, 175):
                for theme in ('dark.qss', 'light.qss'):
                    self.window.apply_appearance(percent, 'default', theme)
                    manager = ProxyConfigManager(Path(folder)/'proxy.json')
                    settings = self.open_proxy(manager)
                    for enabled in (False, True):
                        settings.proxy_enabled_check.setChecked(enabled)
                        self.standalone.APP.processEvents()
                        check = settings.proxy_enabled_check
                        self.assertGreaterEqual(check.width(), check.fontMetrics().horizontalAdvance(check.text()) + 20)
                        settings.grab().save(str(output/(theme+'-'+str(percent)+'-'+str(enabled)+'.png')))
                    self.window.navigation.show_router()
