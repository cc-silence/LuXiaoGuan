"""Actual focus/blur pixels, keyboard activation and default dictionary validation."""
import unittest
from tests import test_standalone as standalone
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest, QSignalSpy
from modules.common_widgets.navigation_button import NavigationButton
from modules.capture_services.openwrt.password_verification import dictionary_paths, OpenWrtPasswordVerifier
from modules.config_services.capture_settings_manager import CaptureSettingsManager
APP=standalone.APP


class NavigationFocusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        standalone.StandaloneTests.setUpClass()
        cls.window=standalone.StandaloneTests.window

    @classmethod
    def tearDownClass(cls):
        standalone.StandaloneTests.tearDownClass()

    def assert_no_focus_frame(self,button):
        self.assertIsInstance(button,NavigationButton)
        self.assertNotEqual(button.focusPolicy(),Qt.NoFocus)
        self.window.activateWindow()
        button.setFocus(Qt.TabFocusReason)
        APP.processEvents()
        self.assertTrue(button.hasFocus())
        focused=button.grab().toImage()
        button.clearFocus()
        APP.processEvents()
        self.assertEqual(focused,button.grab().toImage(),button.objectName())

    def test_main_and_settings_navigation_focus_pixels(self):
        w=self.window
        for theme in ('dark.qss','light.qss'):
            w.apply_appearance(100,'default',theme)
            for button in (w.navigation.toggle_button,w.navigation.router_button,w.navigation.settings_button):
                self.assert_no_focus_frame(button)
            w.navigation.show_settings()
            APP.processEvents()
            for button in w.settings_window.nav_panel.buttons:
                self.assert_no_focus_frame(button)
            w.settings_window.reject()
        button=w.navigation.toggle_button
        spy=QSignalSpy(button.clicked)
        button.setFocus(Qt.TabFocusReason)
        QTest.keyClick(button,Qt.Key_Space)
        self.assertEqual(spy.count(),1)
        if w.navigation.expanded:button.click()

    def test_shipped_default_dictionary_is_readable(self):
        paths=dictionary_paths(CaptureSettingsManager.DEFAULTS['openwrt'])
        self.assertEqual(len(paths),1)
        candidates,skipped=OpenWrtPasswordVerifier.load_candidates(paths[0])
        self.assertGreater(len(candidates),0)
        self.assertGreaterEqual(skipped,0)

if __name__=='__main__':unittest.main()
