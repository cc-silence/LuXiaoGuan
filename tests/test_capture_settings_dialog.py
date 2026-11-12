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
        self.assertFalse(button.icon().isNull())
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
            self.assertFalse(hasattr(settings, "generate_cap"))
            settings.refresh_seconds.setValue(19)
            settings.active_deauth.setChecked(not settings.active_deauth.isChecked())
            dialog.close_button.click()
            APP.processEvents()
            self.assertFalse(dialog.isVisible())
            self.assertEqual(page.settings_manager.load()["openwrt"]["refresh_seconds"], 19)
            button.click()
            APP.processEvents()
            self.assertIs(host._settings_dialog, dialog)
            self.assertEqual(host.settings_page.refresh_seconds.value(), 19)
            for theme in ("dark.qss", "light.qss"):
                for scale in (100, 150):
                    self.window.apply_appearance(scale, "default", theme)
                    APP.processEvents()
                    self.assertGreaterEqual(button.width(), button.sizeHint().width())
                    self.assertGreater(settings.scroll_area.viewport().width(), 100)
                    box = settings.auto_open
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
            self.assertEqual(host.tabs.count(), 2)
            dialog.close()
        finally:
            if host._settings_dialog is not None:
                host._settings_dialog.hide()
            page.settings_manager = manager

    def test_compact_fields_preserve_original_action_panel_width(self):
        host = self.window.packet_capture_page.openwrt_tab
        host.show_page("capture")
        tools = host.capture_page.tools
        for theme in ("dark.qss", "light.qss"):
            for scale in (100, 125, 150, 175):
                self.window.apply_appearance(scale, "default", theme)
                APP.processEvents()
                group = tools.capture_seconds.parentWidget()
                group.hide()
                APP.processEvents()
                baseline = tools.minimumSizeHint().width()
                group.show()
                APP.processEvents()
                self.assertLessEqual(tools.minimumSizeHint().width(), baseline, (theme, scale))
                for control in (tools.capture_seconds, tools.handshake_rounds):
                    self.assertGreaterEqual(control.lineEdit().width(), control.fontMetrics().horizontalAdvance(control.text()))
                for mode in (0, 1):
                    tools.handshake_rounds_mode.setCurrentIndex(mode)
                    APP.processEvents()
                    self.assertLessEqual(tools.minimumSizeHint().width(), baseline, (theme, scale, mode))
                    self.assertEqual(tools.capture_seconds.x(), tools.handshake_rounds.x())
                    self.assertEqual(tools.capture_seconds.width(), tools.handshake_rounds.width())
                    gap = tools.handshake_rounds.x() - tools.handshake_rounds_mode.geometry().right() - 1
                    scan_gap = tools.scan_seconds.x() - tools.band_combo.geometry().right() - 1
                    self.assertEqual(gap, scan_gap)
                    self.assertLessEqual(tools.handshake_rounds_mode.width(), tools.handshake_rounds_mode.sizeHint().width())
                    self.assertLess(tools.handshake_rounds.y(), tools.capture_seconds.y())
                    self.assertEqual(tools.handshake_rounds_mode.itemText(0), "固定轮数")

    def test_round_controls_persist_and_settings_dialog_cannot_override_them(self):
        from modules.main_pages.packet_capture.openwrt.capture_tool_panel import CaptureToolPanel
        page = self.window.packet_capture_page
        host = page.openwrt_tab
        host.show_page("capture")
        tools = host.capture_page.tools
        manager = page.settings_manager
        page.settings_manager = CaptureSettingsManager(Path(standalone.StandaloneTests.temp.name) / "rounds.json")
        try:
            tools.handshake_rounds_mode.setCurrentIndex(0)
            tools.handshake_rounds.setValue(7)
            APP.processEvents()
            self.assertEqual(tools.handshake_rounds_mode.y(), tools.handshake_rounds.y())
            self.assertEqual(tools.handshake_rounds_mode.height(), tools.handshake_rounds.height())
            self.assertLess(tools.handshake_rounds_mode.x(), tools.handshake_rounds.x())
            self.assertTrue(tools.handshake_rounds.isEnabled())
            self.assertEqual(page.settings_manager.load()["openwrt"]["handshake_rounds"], 7)
            tools.handshake_rounds_mode.setCurrentIndex(1)
            self.assertFalse(tools.handshake_rounds.isEnabled())
            saved = page.settings_manager.load()["openwrt"]
            self.assertTrue(saved["handshake_rounds_infinite"])
            self.assertEqual(saved["handshake_rounds"], 7)
            host.show_capture_settings()
            self.assertFalse(hasattr(host.settings_page, "handshake_rounds_mode"))
            self.assertNotIn("handshake_rounds", host.settings_page.collect_settings())
            host.settings_page.active_deauth.setChecked(not host.settings_page.active_deauth.isChecked())
            host._settings_dialog.reject()
            saved = page.settings_manager.load()["openwrt"]
            self.assertTrue(saved["handshake_rounds_infinite"])
            self.assertEqual(saved["handshake_rounds"], 7)
            rebuilt = CaptureToolPanel(saved)
            try:
                self.assertEqual(rebuilt.handshake_rounds_mode.currentData(), "infinite")
                self.assertEqual(rebuilt.handshake_rounds.value(), 7)
                self.assertFalse(rebuilt.handshake_rounds.isEnabled())
                rebuilt.handshake_rounds_mode.setCurrentIndex(0)
                self.assertTrue(rebuilt.handshake_rounds.isEnabled())
                self.assertEqual(rebuilt.collect_settings()["handshake_rounds"], 7)
            finally:
                rebuilt.deleteLater()
            tools.handshake_rounds_mode.setCurrentIndex(0)
            self.assertFalse(page.settings_manager.load()["openwrt"]["handshake_rounds_infinite"])
        finally:
            page.settings_manager = manager

    def test_real_disabled_dependency_button_routes_to_login_scroll(self):
        from tests.test_wheel_routing import wheel
        host = self.window.packet_capture_page.openwrt_tab
        host.tabs.setCurrentIndex(1)
        dep_page = host.dependency_page
        dep_page.dependencies.card.set_expanded(True, animate=False)
        self.window.resize(1000, 600)
        APP.processEvents()
        bar = dep_page.scroll_area.verticalScrollBar()
        self.assertGreater(bar.maximum(), 0)
        button = dep_page.dependencies.install_button
        self.assertFalse(button.isEnabled())
        bar.setValue(0)
        wheel(button)
        self.assertGreater(bar.value(), 0)
