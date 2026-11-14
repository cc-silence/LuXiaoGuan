"""Fresh startup must use the same outer navigation size as later refreshes."""
import unittest
from PySide6.QtCore import QPoint
from tests import test_standalone as standalone
APP = standalone.APP


class NavigationStartupLayoutTests(unittest.TestCase):
    def test_first_page_switch_keeps_navigation_positions(self):
        standalone.StandaloneTests.setUpClass()
        w = standalone.StandaloneTests.window
        try:
            nav = w.navigation
            def positions():
                return (nav.toggle_button.height(), tuple(
                    button.mapTo(nav, QPoint()).y()
                    for button, _page in nav.page_buttons.values()))
            before = positions()
            nav.page_buttons['signal'][0].click()
            APP.processEvents()
            self.assertEqual(positions(), before)
            for theme in ('dark.qss', 'light.qss'):
                for scale in (100, 125, 150, 175):
                    w.apply_appearance(scale, 'default', theme)
                    APP.processEvents()
                    before = positions()
                    nav.refresh()
                    APP.processEvents()
                    self.assertEqual(positions(), before, (theme, scale))
        finally:
            standalone.StandaloneTests.tearDownClass()
