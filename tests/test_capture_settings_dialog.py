"""Offline interaction and rendered checks for the relocated capture settings."""
import unittest
from pathlib import Path
from PySide6.QtCore import Qt
from tests import test_standalone as standalone
from modules.config_services.capture_settings_manager import CaptureSettingsManager

APP = standalone.APP


class CaptureSettingsDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from modules.interaction_guards import install_interaction_guards
        install_interaction_guards(APP)
        standalone.StandaloneTests.setUpClass()
        cls.window = standalone.StandaloneTests.window

    @classmethod
    def tearDownClass(cls):
        standalone.StandaloneTests.tearDownClass()

    def test_button_dialog_save_reopen_and_appearance(self):
        page = self.window.packet_capture_page
        host = page.openwrt_tab
        self.assertNotIn("settings", host._page_indices)
        self.assertIsNone(host._settings_dialog)
        host.tabs.setCurrentIndex(host._page_indices["capture"])
        button = host.capture_page.tools.settings_button
        self.assertTrue(button.text())
        self.assertTrue(button.icon().isNull())
        manager = page.settings_manager
        page.settings_manager = CaptureSettingsManager(Path(standalone.StandaloneTests.temp.name) / "capture.json")
        output = Path("build/capture-settings-dialog-review")
        output.mkdir(parents=True, exist_ok=True)
        try:
            button.click()
            APP.processEvents()
            dialog = host._settings_dialog
            self.assertTrue(dialog.isVisible())
            self.assertEqual(dialog.windowModality(), Qt.WindowModal)
            settings = host.settings_page
            self.assertFalse(hasattr(settings, "keep_raw"))
            self.assertTrue(settings.collect_settings()["keep_raw_capture"])
            settings.handshake_rounds.setValue(7)
            settings.active_deauth.setChecked(not settings.active_deauth.isChecked())
            dialog.close_button.click()
            APP.processEvents()
            self.assertFalse(dialog.isVisible())
            self.assertEqual(page.settings_manager.load()["openwrt"]["handshake_rounds"], 7)
            button.click()
            APP.processEvents()
            self.assertIs(host._settings_dialog, dialog)
            self.assertEqual(host.settings_page.handshake_rounds.value(), 7)
            for theme in ("dark.qss", "light.qss"):
                for scale in (100, 150):
                    self.window.apply_appearance(scale, "default", theme)
                    APP.processEvents()
                    self.assertGreaterEqual(button.width(), button.sizeHint().width())
                    self.assertGreater(settings.scroll_area.viewport().width(), 100)
                    box = settings.generate_cap
                    image = box.parentWidget().grab().toImage()
                    x = box.x() + box.width() - 4
                    self.assertEqual(image.pixelColor(x, box.y() + box.height() // 2),
                                     image.pixelColor(x, box.y() - 2), (theme, scale))
                    self.assertTrue(dialog.grab().save(str(output / f"dialog-{theme}-{scale}.png")))
                    self.assertTrue(host.capture_page.grab().save(str(output / f"capture-{theme}-{scale}.png")))
            dialog.reject()
            button.click()
            APP.processEvents()
            self.assertIs(host.settings_page, settings)
            self.assertEqual(host.tabs.count(), 7)
            dialog.close()
        finally:
            if host._settings_dialog is not None:
                host._settings_dialog.hide()
            page.settings_manager = manager

    def test_real_disabled_dependency_button_routes_to_login_scroll(self):
        from tests.test_wheel_routing import wheel
        host = self.window.packet_capture_page.openwrt_tab
        host.tabs.setCurrentIndex(0)
        login = host.login_page
        login.dependencies.card.set_expanded(True, animate=False)
        self.window.resize(1000, 600)
        APP.processEvents()
        bar = login.scroll_area.verticalScrollBar()
        self.assertGreater(bar.maximum(), 0)
        button = login.dependencies.install_button
        self.assertFalse(button.isEnabled())
        bar.setValue(0)
        wheel(button)
        self.assertGreater(bar.value(), 0)
