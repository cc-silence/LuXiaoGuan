"""Scan countdown follows real progress without retaining task state."""
import unittest
from PySide6.QtWidgets import QWidget
from tests.test_standalone import APP
from modules.capture_services.openwrt.models import CaptureProgress
from modules.main_pages.packet_capture.openwrt.capture_tool_panel import CaptureToolPanel


class ScanCountdownTests(unittest.TestCase):
    def test_progress_and_reset(self):
        owner = QWidget()
        panel = CaptureToolPanel({}, owner)
        try:
            for total in (24, 60, 120):
                for current in (0, 1, total, total + 1):
                    panel.set_action_availability(True, True, {}, "wifi_scan", "capturing",
                        scan_progress=CaptureProgress(current=current, total=total))
                    self.assertEqual(panel.scan_button.text(),
                        f"\u505c\u6b62\u626b\u63cf\uff08{max(0, total-current)}\u79d2\uff09")
                    self.assertEqual(panel.scan_button.property("captureAction"), "stop")
            for state in ("starting", "stopping"):
                panel.set_action_availability(True, True, {}, "wifi_scan", state,
                    scan_progress=CaptureProgress(current=1, total=60))
                self.assertEqual(panel.scan_button.text(), "\u505c\u6b62\u626b\u63cf")
            for state in ("idle", "failed"):
                panel.set_action_availability(True, False, {}, "wifi_scan", state)
                self.assertEqual(panel.scan_button.text(), "\u5f00\u59cb\u626b\u63cf")
            panel.set_action_availability(True, True, {}, "wifi_scan", "capturing",
                scan_progress=CaptureProgress(total=60, indeterminate=True))
            self.assertEqual(panel.scan_button.text(), "\u505c\u6b62\u626b\u63cf")
        finally:
            owner.close()
            owner.deleteLater()
            APP.processEvents()
