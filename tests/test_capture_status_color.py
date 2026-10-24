"""Inspect actual capture-row painting, selection and completion in both themes."""
import unittest
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout
from tests.test_file_selection import APP
from modules.ui_scale_manager import UiScaleManager
from modules.main_pages.packet_capture.capture_page_styles import CapturePageStyleManager
from modules.main_pages.base_page_styles import light_base_page_stylesheet, dark_base_page_stylesheet
from modules.main_pages.packet_capture.openwrt.access_point_table import AccessPointTable
from modules.capture_services.openwrt.models import AccessPointInfo, AccessPointCaptureState


class CaptureStatusColorTests(unittest.TestCase):
    def test_active_row_is_yellow_in_both_themes_and_completion_stays_green(self):
        parent = QWidget()
        parent.scale_manager = UiScaleManager(100)
        parent.setObjectName('packetCapturePage')
        table = AccessPointTable(parent)
        QVBoxLayout(parent).addWidget(table)
        points = (AccessPointInfo('00:11:22:33:44:55', ssid='Capturing', encryption='WPA2', channel=1),
                  AccessPointInfo('00:11:22:33:44:66', ssid='Captured', encryption='WPA2', channel=6,
                                  capture_state=AccessPointCaptureState.CAPTURED))
        table.set_access_points(points)
        table.set_capturing_bssids((points[0].bssid,))
        parent.resize(1100, 240)
        parent.show()
        try:
            output = Path('build/previews/capture-status')
            output.mkdir(parents=True, exist_ok=True)
            for light in (True, False):
                with self.subTest(light=light):
                    parent.light_theme = light
                    styles = CapturePageStyleManager(parent)
                    base = light_base_page_stylesheet('', parent.scale_manager) if light else dark_base_page_stylesheet(parent.scale_manager)
                    parent.setStyleSheet(base + styles._capture_stylesheet(light))
                    table.set_light_theme(light)
                    table.selectRow(0)
                    APP.processEvents()
                    image = table.viewport().grab().toImage()
                    rect = table.visualItemRect(table.item(0, table.SSID_COLUMN))
                    color = image.pixelColor(rect.right()-8, rect.center().y()).name()
                    self.assertEqual(color, '#fff0b3' if light else '#574817')
                    self.assertEqual(table.item(1, table.STATUS_COLUMN).foreground().color(), table._highlight_green())
                    self.assertTrue(parent.grab().save(str(output/('light.png' if light else 'dark.png'))))
            table.set_capturing_bssids((points[1].bssid,))
            self.assertIsNone(table.item(1, table.STATUS_COLUMN).data(Qt.ForegroundRole))
            table.set_capturing_bssids(())
            self.assertIsNone(table.item(1, table.STATUS_COLUMN).data(Qt.BackgroundRole))
            self.assertEqual(table.item(1, table.STATUS_COLUMN).foreground().color(), table._highlight_green())
        finally:
            parent.close()
            parent.deleteLater()
            APP.processEvents()
