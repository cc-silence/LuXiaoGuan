"""UI removal boundaries and nested compatibility tools; no router operations."""
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

    def test_compatibility_card_follows_dependency_collapse(self):
        login = self.container.login_page
        section = login.dependencies.card
        card = login.compatible_tools.card
        self.container.tabs.setCurrentIndex(0)
        self.assertTrue(section.isAncestorOf(card))
        self.assertIn("pmkid", login.dependencies.checks)
        section.set_expanded(False, animate=False)
        APP.processEvents()
        self.assertFalse(card.isVisible())
        section.set_expanded(True, animate=False)
        APP.processEvents()
        self.assertTrue(card.isVisible())

    def test_render_themes_and_scales(self):
        output = Path("build/review/manual-pmkid-removal")
        output.mkdir(parents=True, exist_ok=True)
        self.container.login_page.dependencies.card.set_expanded(True, animate=False)
        for theme in ("dark.qss", "light.qss"):
            for scale in (100, 150):
                self.window.apply_appearance(scale, "default", theme)
                self.window.resize(1200, 850)
                for name, index in (("login", 0), ("capture", 6), ("settings", 7)):
                    self.container.tabs.setCurrentIndex(index)
                    APP.processEvents()
                    page = self.container.tabs.currentWidget()
                    if name == "login":
                        card = self.container.login_page.compatible_tools.card
                        self.assertTrue(card.grab().save(str(output / f"compatibility-{theme}-{scale}.png")))
                    self.assertTrue(page.grab().save(str(output / f"{name}-{theme}-{scale}.png")))
