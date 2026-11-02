"""Removed manual and alternate tool UI; no router operations."""
import unittest
from pathlib import Path
from PySide6.QtWidgets import QAbstractButton, QLabel, QGroupBox
from tests import test_standalone as fixture

APP = fixture.APP

class ManualPmkidUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.StandaloneTests.setUpClass.__func__(cls)
        cls.container = cls.window.packet_capture_page.openwrt_tab

    @classmethod
    def tearDownClass(cls):
        fixture.StandaloneTests.tearDownClass.__func__(cls)

    def test_removed_widgets_are_not_constructed_and_collection_still_works(self):
        tools = self.container.capture_page.tools
        settings = self.container.settings_page
        for name in ("pmkid_button", "pmkid_group", "mode_combo"):
            self.assertFalse(hasattr(tools, name), name)
        for name in ("pmkid_card", "pmkid_beacon", "pmkid_duration_seconds", "pmkid_idle_seconds"):
            self.assertFalse(hasattr(settings, name), name)
        for page in (tools, settings):
            for widget in page.findChildren(QAbstractButton) + page.findChildren(QLabel):
                self.assertNotIn("PMKID", widget.text())
            self.assertFalse(any(key.startswith("pmkid_") for key in page.collect_settings()))
        tools.set_action_availability(True, False, {"scan": True, "handshake": True})
        self.assertTrue(tools.handshake_button.isEnabled())
        tools.set_action_availability(True, True, {"handshake": True}, "handshake")
        self.assertEqual(tools.handshake_button.property("captureAction"), "stop")
        self.assertIsNotNone(self.container.auto_capture_page.upgrade_hcx_btn)

    def test_alternate_card_removed_and_dependency_install_retained(self):
        login = self.container.login_page
        self.assertFalse(hasattr(login, "compatible_tools"))
        self.assertIn("pmkid", login.dependencies.checks)
        manager = self.window.runtime.capture_service.router_management
        self.assertFalse(hasattr(manager, "tools"))
        for action in ("tool_check", "tool_install", "tool_reset"):
            with self.assertRaises(ValueError):
                manager.submit(action)
        for widget in login.findChildren(QAbstractButton) + login.findChildren(QLabel):
            self.assertNotIn("\u517c\u5bb9\u5de5\u5177", widget.text())

    def test_render_themes_and_scales(self):
        output = Path("build/review/manual-pmkid-removal")
        output.mkdir(parents=True, exist_ok=True)
        self.container.login_page.dependencies.card.set_expanded(True, animate=False)
        for theme in ("dark.qss", "light.qss"):
            for scale in (100, 150):
                self.window.apply_appearance(scale, "default", theme)
                self.window.resize(1200, 850)
                for name, index in (("login", 0), ("capture", 6)):
                    self.container.tabs.setCurrentIndex(index)
                    APP.processEvents()
                    page = self.container.tabs.currentWidget()
                    self.assertTrue(page.grab().save(str(output / f"{name}-{theme}-{scale}.png")))
