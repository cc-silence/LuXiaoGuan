"""Exercise real login-page collapse animation without frame-to-frame compression."""
import time
import unittest
from pathlib import Path
from PySide6.QtCore import QPoint
from tests import test_standalone as standalone
APP = standalone.APP


class LoginCollapseLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        standalone.StandaloneTests.setUpClass()
        cls.window = standalone.StandaloneTests.window
        cls.page = cls.window.packet_capture_page.openwrt_tab.login_page
        cls.window.packet_capture_page.openwrt_tab.tabs.setCurrentIndex(0)

    @classmethod
    def tearDownClass(cls):
        standalone.StandaloneTests.tearDownClass()

    @staticmethod
    def settle(ms=220):
        deadline = time.monotonic() + ms / 1000
        while time.monotonic() < deadline:
            APP.processEvents()
            time.sleep(.002)
        APP.processEvents()

    def check_expansion(self, section):
        section.set_expanded(False, animate=False)
        self.settle()
        bar = self.page.scroll_area.verticalScrollBar()
        bar.setValue(bar.maximum())
        section.header_button.setFocus()
        self.settle(35)
        def state():
            return (bar.value(), self.page.connection.card.height(), section.y(),
                    section.header_button.mapTo(self.page.scroll_area.viewport(), QPoint()).y())
        before = state()
        frames = []
        section.header_button.click()
        deadline = time.monotonic() + .3
        while time.monotonic() < deadline:
            APP.processEvents()
            frames.append(state())
            self.settle(8)
        self.assertGreater(len(frames), 1)
        self.assertEqual(set(frames), {before}, f"Animated layout compressed or scrolled: {before} -> {frames}")
        self.assertTrue(section._expanded)
        self.assertTrue(section.content_frame.isVisible())
        self.assertGreater(section.content_frame.height(), 0)

    def test_common_dependencies_expand_without_jumping_in_both_themes_and_scales(self):
        output = Path(__file__).parent / "signal-actions-preview"
        output.mkdir(exist_ok=True)
        for theme in ("dark.qss", "light.qss"):
            for scale in (100, 175):
                with self.subTest(theme=theme, scale=scale):
                    self.window.apply_appearance(scale, "default", theme)
                    self.window.resize(1100, 760)
                    self.settle()
                    self.check_expansion(self.page.common_tools.card)
                    if scale == 100:
                        self.window.grab().save(str(output / ("login-dependencies-"+theme+".png")))

    def test_capture_dependencies_use_same_stable_scroll_layout(self):
        self.window.apply_appearance(100, "default", "dark.qss")
        self.window.resize(1100, 760)
        self.page.common_tools.card.set_expanded(False, animate=False)
        self.settle()
        self.check_expansion(self.page.dependencies.card)

    def test_quick_reversal_and_snapshot_during_expansion(self):
        section = self.page.common_tools.card
        section.set_expanded(False, animate=False)
        self.settle()
        bar = self.page.scroll_area.verticalScrollBar()
        bar.setValue(bar.maximum())
        self.settle(30)
        height = self.page.connection.card.height()
        section.header_button.click()
        self.settle(40)
        section.header_button.click()
        self.settle(40)
        section.header_button.click()
        self.page.apply_snapshot(standalone.StandaloneTests.runtime.capture_service.openwrt_snapshot)
        self.settle()
        self.assertEqual(self.page.connection.card.height(), height)
        self.assertTrue(section._expanded)
        self.assertEqual(section.content_frame.maximumHeight(), 16777215)
