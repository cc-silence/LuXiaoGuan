"""Theme transactions avoid redundant geometry work and retain update state."""
import unittest
from unittest.mock import patch
from tests import test_standalone as standalone
APP = standalone.APP
from tests.test_wireless_table import network
from modules.main_pages.packet_capture.openwrt.wireless_network_table import WirelessNetworkTable


class ThemeSwitchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        standalone.StandaloneTests.setUpClass()
        cls.window = standalone.StandaloneTests.window

    @classmethod
    def tearDownClass(cls):
        standalone.StandaloneTests.tearDownClass()

    def test_color_switch_reuses_metrics_and_cached_styles(self):
        window = self.window
        page = window.packet_capture_page
        for theme in ("light.qss", "dark.qss"):
            window.apply_appearance(100, "default", theme)
        manager = window.scale_manager
        with patch.object(manager, "apply_bound_metrics", wraps=manager.apply_bound_metrics) as metrics, \
             patch.object(page.style_mgr, "_build_stylesheet", wraps=page.style_mgr._build_stylesheet) as build:
            for theme in ("light.qss", "dark.qss"):
                window.apply_appearance(100, "default", theme)
                APP.processEvents()
                self.assertIs(window.scale_manager, manager)
                self.assertTrue(window.updatesEnabled())
            metrics.assert_not_called()
            build.assert_not_called()
        window.apply_appearance(125, "default", "dark.qss")
        self.assertEqual(window.scale_manager.global_scale, 125)
        window.apply_appearance(100, "default", "dark.qss")

    def test_table_measures_once_and_exception_restores_updates(self):
        window = self.window
        manager = window.packet_capture_page.style_mgr
        table = WirelessNetworkTable(window.packet_capture_page)
        try:
            table.set_networks(tuple(network(i, "Network" + str(i), -40-i) for i in range(64)))
            with patch.object(table, "resizeColumnToContents", wraps=table.resizeColumnToContents) as measure:
                window.apply_appearance(100, "default", "light.qss")
                self.assertEqual(measure.call_count, 3)
                self.assertEqual(table._appearance_depth, 0)
            with patch.object(window, "_apply_appearance", side_effect=RuntimeError("fixture")):
                with self.assertRaises(RuntimeError):
                    window.apply_appearance(100, "default", "dark.qss")
                self.assertTrue(window.updatesEnabled())
                self.assertEqual(table._appearance_depth, 0)
            window.setUpdatesEnabled(False)
            window.apply_appearance(100, "default", "dark.qss")
            self.assertFalse(window.updatesEnabled())
        finally:
            window.setUpdatesEnabled(True)
            table.setParent(None)
            table.deleteLater()
            APP.processEvents()


if __name__ == "__main__":
    unittest.main()
