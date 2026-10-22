"""Immediate settings persistence, failure handling and embedded navigation."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtWidgets import QFrame
from modules.config_manager import ConfigManager, BASE_DIR
from modules.config_services.proxy_config_manager import ProxyConfigManager
from tests import test_standalone as standalone

APP = standalone.APP


class ImmediateSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        standalone.StandaloneTests.setUpClass()
        cls.window = standalone.StandaloneTests.window

    @classmethod
    def tearDownClass(cls):
        standalone.StandaloneTests.tearDownClass()

    def tearDown(self):
        if self.window.settings_window is not None:
            self.window.settings_window.reject()
        APP.processEvents()

    def test_selection_is_saved_before_leaving_and_reopens_unchanged(self):
        self.window.apply_appearance(100, "default", "dark.qss")
        self.window.openSettingsWindow()
        settings = self.window.settings_window
        self.assertIsNone(settings.window_frame.findChild(QFrame, "settingsButtonPanel"))
        self.assertTrue(all(settings.button_layout.itemAt(i).widget() is None
                            for i in range(settings.button_layout.count())))
        settings.ui_scale_combo.setCurrentIndex(settings.ui_scale_combo.findData(125))
        settings.ui_font_combo.setCurrentIndex(settings.ui_font_combo.findData("bold"))
        settings.close_button_action_combo.setCurrentIndex(settings.close_button_action_combo.findData("exit"))
        saved = ConfigManager().load()
        self.assertEqual(saved.get("ui", "scale"), "125")
        self.assertEqual(saved.get("ui", "font_family"), "bold")
        self.assertEqual(saved.get("runtime", "close_button_action"), "exit")
        self.window.navigation.show_router()
        self.assertIsNone(self.window.settings_window)
        self.assertEqual(self.window.scale_manager.global_scale, 125)
        self.window.openSettingsWindow()
        reopened = self.window.settings_window
        self.assertEqual(reopened.ui_font_combo.currentData(), "bold")
        self.assertEqual(reopened.close_button_action_combo.currentData(), "exit")

    def test_failed_save_preserves_live_settings_and_selection(self):
        self.window.openSettingsWindow()
        settings = self.window.settings_window
        before = self.window.scale_manager.global_scale
        after = 150 if before != 150 else 100
        with patch.object(self.window.config_manager, "save", side_effect=OSError("fixture disk failure")), patch("router_app.settings.MessageDialog.exec", return_value=1):
            settings.ui_scale_combo.setCurrentIndex(settings.ui_scale_combo.findData(after))
        self.assertEqual(self.window.scale_manager.global_scale, before)
        self.assertEqual(settings.ui_scale_combo.currentData(), before)

    def test_proxy_commits_completed_edit_and_flushes_on_navigation(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "proxy.json"
            manager = ProxyConfigManager(path)
            with patch("router_app.settings.ProxyConfigManager", return_value=manager):
                self.window.openSettingsWindow()
            settings = self.window.settings_window
            settings.pages.setCurrentIndex(1)
            settings.proxy_inputs["http_host"].setText("127.0.0.1")
            settings.proxy_inputs["http_port"].setText("8123")
            settings.proxy_inputs["http_port"].editingFinished.emit()
            self.assertEqual(ProxyConfigManager(path).get_http_url(), "http://127.0.0.1:8123")
            settings.proxy_inputs["http_port"].setText("8124")
            self.window.navigation.show_router()
            self.assertIsNone(self.window.settings_window)
            self.assertEqual(ProxyConfigManager(path).get_http_url(), "http://127.0.0.1:8124")

    def test_invalid_proxy_is_visible_and_does_not_replace_saved_configuration(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "proxy.json"
            manager = ProxyConfigManager(path)
            manager.set_proxy(dict(http_host="127.0.0.1", http_port="8123"))
            manager.save()
            before = path.read_bytes()
            with patch("router_app.settings.ProxyConfigManager", return_value=manager):
                self.window.openSettingsWindow()
            settings = self.window.settings_window
            settings.pages.setCurrentIndex(1)
            settings.proxy_inputs["http_port"].setText("70000")
            settings.proxy_inputs["http_port"].editingFinished.emit()
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(manager.get_http_url(), "http://127.0.0.1:8123")
            self.window.navigation.show_router()
            self.assertIs(self.window.settings_window, settings)
            self.assertEqual(settings.pages.currentIndex(), 1)
            settings.proxy_inputs["http_port"].setText("8125")
            self.window.navigation.show_router()
            self.assertIsNone(self.window.settings_window)
            self.assertEqual(ProxyConfigManager(path).get_http_url(), "http://127.0.0.1:8125")

    def test_proxy_disk_failure_can_be_retried_without_losing_edit(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = ProxyConfigManager(Path(folder) / "proxy.json")
            with patch("router_app.settings.ProxyConfigManager", return_value=manager):
                self.window.openSettingsWindow()
            settings = self.window.settings_window
            before = manager.to_dict()
            settings.proxy_inputs["http_host"].setText("127.0.0.1")
            with patch.object(manager, "save", side_effect=OSError("fixture")):
                self.assertFalse(settings._proxy_flush_fn())
            self.assertEqual(manager.to_dict(), before)
            self.assertEqual(settings.proxy_inputs["http_host"].text(), "127.0.0.1")
            self.assertTrue(settings._proxy_flush_fn())
            self.assertEqual(manager.get_http_url(), "http://127.0.0.1:1081")

    def test_render_without_footer_in_both_themes_and_scales(self):
        folder = BASE_DIR / "build" / "immediate-settings-preview"
        folder.mkdir(parents=True, exist_ok=True)
        for theme in ("light.qss", "dark.qss"):
            self.window.apply_appearance(100, "default", theme)
            self.window.openSettingsWindow()
            settings = self.window.settings_window
            for scale in (100, 150):
                settings.ui_scale_combo.setCurrentIndex(settings.ui_scale_combo.findData(scale))
                for index in (0, 1):
                    settings.pages.setCurrentIndex(index)
                    APP.processEvents()
                    self.assertIsNone(settings.window_frame.findChild(QFrame, "settingsButtonPanel"))
                    self.window.grab().save(str(folder / (theme+"-"+str(scale)+"-"+str(index)+".png")))
            self.window.navigation.show_router()
