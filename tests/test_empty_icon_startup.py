"""A placeholder icon must not cause Qt to open an empty filename at startup."""
import unittest
from unittest.mock import patch
from PySide6.QtCore import qInstallMessageHandler
from tests import test_standalone as standalone
from modules.config_manager import ConfigManager
from modules.icon_utils import get_theme_icon, _icon_cache


class EmptyIconStartupTests(unittest.TestCase):
    def test_empty_placeholder_is_null_without_file_warning_in_both_themes(self):
        messages = []
        previous = qInstallMessageHandler(lambda kind, context, message: messages.append(message))
        try:
            _icon_cache.clear()
            for light in (True, False):
                self.assertTrue(get_theme_icon('', light).isNull())
                self.assertFalse(get_theme_icon(':/icons/images/icons/cil-folder.png', light).isNull())
        finally:
            qInstallMessageHandler(previous)
        self.assertFalse([text for text in messages if 'QFSFileEngine::open' in text], messages)

    def test_cold_light_startup_has_valid_dock_icon_and_no_empty_file_warning(self):
        messages = []
        original = ConfigManager.load
        def light_config(manager):
            config = original(manager)
            config.set('ui', 'theme', 'light.qss')
            return config
        _icon_cache.clear()
        previous = qInstallMessageHandler(lambda kind, context, message: messages.append(message))
        ready = False
        try:
            with patch.object(ConfigManager, 'load', light_config):
                standalone.StandaloneTests.setUpClass()
                ready = True
                window = standalone.StandaloneTests.window
                standalone.APP.processEvents()
                self.assertEqual(window.theme_name, 'light.qss')
                dock = window.packet_capture_page.openwrt_tab.dependency_page.info_dock
                self.assertFalse(dock.toggle_button.icon().isNull())
        finally:
            if ready:
                standalone.StandaloneTests.tearDownClass()
            qInstallMessageHandler(previous)
        self.assertFalse([text for text in messages if 'QFSFileEngine::open' in text], messages)
