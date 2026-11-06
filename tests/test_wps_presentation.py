"""WPS native-stage, current-candidate and shared yellow-row contracts."""
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QVBoxLayout
from tests.test_file_selection import APP
from modules.ui_scale_manager import UiScaleManager
from modules.capture_services.capture_service import CaptureService
from modules.capture_services.openwrt.models import AccessPointInfo, OpenWrtSessionSnapshot, CaptureTaskType
from modules.capture_services.openwrt.wps_capture import OpenWrtWpsCapture, WpsUpdate
from modules.capture_services.openwrt.wps_observation import analyze
from modules.main_pages.packet_capture.openwrt.openwrt_page import OpenWrtCapturePage
from modules.main_pages.packet_capture.openwrt.access_point_table import AccessPointTable
from modules.main_pages.packet_capture.openwrt.task_progress import CaptureTaskProgress
from modules.main_pages.packet_capture.capture_page_styles import CapturePageStyleManager
from modules.main_pages.base_page_styles import light_base_page_stylesheet, dark_base_page_stylesheet

MAC = "02:11:22:33:44:55"

class WpsObservationTests(unittest.TestCase):
    def test_native_progress_supersedes_waiting_and_keeps_credentials_private(self):
        lines = ["[+] Waiting for beacon from fixture", "[+] Received beacon from fixture",
                 "[+] Associated with fixture", "[+] Received M1 message",
                 "executing pixiewps -e SECRET", "[-] Pixiewps fail, sending WPS NACK"]
        for end, stage in enumerate(("beacon", "association", "exchange", "exchange", "pixie", "pixie_failed"), 1):
            result = analyze("\n".join(lines[:end]))
            self.assertEqual(result.stage, stage)
            self.assertNotIn("SECRET", result.status)
        self.assertEqual(analyze("[+] WPS PIN: 12345670\nWPA PSK: secret").current_pin, "")
        self.assertEqual(analyze('\x1b[33m[+] Trying pin "00000000"\x1b[0m').current_pin, "00000000")
        self.assertEqual(analyze('[+] Trying pin "123456789"').current_pin, "")
        self.assertEqual(analyze('/bin/sh: pixiewps: not found').stage, "tool_error")

    def test_pixie_timeout_and_completed_failure_are_reported_differently(self):
        for text, timed, expected in (
            ('[+] Waiting for beacon from fixture', True, "未确认完成"),
            ('executing pixiewps -e SECRET\n[-] Pixiewps fail, sending WPS NACK', False, "计算未找到"),
        ):
            with TemporaryDirectory() as folder:
                manager = Mock(); manager.is_running.return_value = timed
                manager.read_log_tail.return_value = text
                capture = OpenWrtWpsCapture(Mock(), manager)
                clock = [0.0]
                with patch.object(capture, '_base_args', return_value='reaver'), patch.object(capture, '_download_wpc', return_value=''), patch('modules.capture_services.openwrt.wps_capture.time.monotonic', side_effect=lambda: clock[0]), patch('modules.capture_services.openwrt.wps_capture.time.sleep', side_effect=lambda seconds: clock.__setitem__(0, clock[0]+60)):
                    result = capture.run_targets(NS(local_dir=Path(folder), remote_dir='/tmp/owned'), 'ownedmon', 'owned', (AccessPointInfo(MAC, channel=1, wps_supported=True),), 'offline', {'attack_seconds':60}, folder, Mock())[0]
                self.assertIn(expected, result.note)
                self.assertNotIn("穷尽全部候选", result.note)
                self.assertNotIn("SECRET", result.note)
                if not timed:
                    manager.start.assert_called_once()

class WpsPresentationTests(unittest.TestCase):
    def test_target_switch_current_pin_and_full_row_render_in_both_themes(self):
        points = (AccessPointInfo(MAC, ssid='Target A', channel=1, encryption='WPA2'),
                  AccessPointInfo('02:11:22:33:44:66', ssid='Target B', channel=6, encryption='WPA2'))
        snapshot = OpenWrtSessionSnapshot(task_type=CaptureTaskType.WPS, task_state='capturing', access_points=points)
        service = NS(openwrt_backend=NS(snapshot=snapshot), openwrtSnapshotChanged=Mock(), password_verification_candidate='')
        parent = QFrame(); parent.scale_manager = UiScaleManager(100); parent.setObjectName('packetCapturePage')
        table = AccessPointTable(parent); progress = CaptureTaskProgress(parent)
        layout = QVBoxLayout(parent); layout.addWidget(table); layout.addWidget(progress)
        page = NS(service=service, access_points=table, progress=progress, signal_export=Mock(), tools=Mock(), _sync_display_privacy=Mock())
        parent.resize(1220, 260); parent.show()
        output = Path('build/previews/wps-progress'); output.mkdir(parents=True, exist_ok=True)
        try:
            for light in (True, False):
                styles = CapturePageStyleManager(parent)
                base = light_base_page_stylesheet('', parent.scale_manager) if light else dark_base_page_stylesheet(parent.scale_manager)
                parent.setStyleSheet(base+styles._capture_stylesheet(light)); table.set_light_theme(light)
                for index, point in enumerate(points):
                    update = WpsUpdate(2, 60, point.ssid, index, 2, '常规 Pin', '00000000', target_bssid=point.bssid, candidate_elapsed=12.5, average_per_minute=1.25)
                    CaptureService._on_openwrt_wps_progress(service, update)
                    snap = service.openwrt_backend.snapshot
                    OpenWrtCapturePage.apply_snapshot(page, snap); APP.processEvents()
                    self.assertTrue(progress.title_label.text().startswith('当前 PIN：00000000'))
                    self.assertEqual(snap.capturing_bssids, (point.bssid,))
                    active = next(row for row in range(table.rowCount()) if table.item(row, table.STATUS_COLUMN).data(Qt.UserRole)==point.bssid)
                    table.selectRow(active)
                    self.assertEqual(table.item(active, table.STATUS_COLUMN).text(), 'Pin 中')
                    for row in range(table.rowCount()):
                        for column in range(table.columnCount()):
                            bg = table.item(row, column).data(Qt.BackgroundRole)
                            if row==active: self.assertEqual(bg.color(), table._capturing_bg())
                            else: self.assertIsNone(bg)
                self.assertTrue(parent.grab().save(str(output/('light.png' if light else 'dark.png'))))
                CaptureService._on_openwrt_wps_progress(service, replace(update, current_pin='', phase='准备'))
                self.assertIn('等待工具报告', service.openwrt_backend.snapshot.progress.title)
                CaptureService._on_openwrt_wps_progress(service, replace(update, phase='结束'))
                self.assertEqual(service.openwrt_backend.snapshot.capturing_bssids, ())
                OpenWrtCapturePage.apply_snapshot(page, replace(snap, task_state='idle'))
                self.assertFalse(table._capturing)
        finally:
            parent.close(); parent.deleteLater(); APP.processEvents()

    def test_failure_clears_active_target_and_stale_stopping_updates_are_ignored(self):
        snap = OpenWrtSessionSnapshot(task_type=CaptureTaskType.WPS, task_state='stopping', capturing_bssids=(MAC,))
        service = Mock(); service.openwrt_backend.snapshot = snap
        CaptureService._on_openwrt_wps_progress(service, WpsUpdate(2, 60, target_bssid=MAC))
        service.openwrtSnapshotChanged.emit.assert_not_called()
        service._task_failure_snapshot.return_value = snap
        CaptureService._on_openwrt_wps_failed(service, '已取消', '')
        self.assertEqual(service.openwrt_backend.snapshot.capturing_bssids, ())

    def test_backend_marks_preparation_and_clears_after_preparation_failure(self):
        from modules.capture_services.backends.openwrt_backend import OpenWrtCaptureBackend
        with TemporaryDirectory() as folder:
            backend = object.__new__(OpenWrtCaptureBackend)
            backend.ssh_client = NS(connected=True)
            backend.reconnect_cleanup = Mock(); backend.workspace_manager = Mock()
            backend.wps_capture = OpenWrtWpsCapture(Mock(), Mock())
            backend._find_scan_radio = Mock(side_effect=RuntimeError('controlled preparation failure'))
            backend._restore_task_wireless = Mock(return_value=[])
            backend._finish_capture_task = Mock()
            updates = []
            with patch('modules.capture_services.backends.openwrt_backend.CaptureWorkspaceFactory.create', return_value=NS(local_dir=Path(folder), remote_dir='/tmp/owned')):
                backend.run_wps('owned', {'resolved_output_dir': folder}, (AccessPointInfo(MAC, channel=1, wps_supported=True),), 'normal', progress_callback=updates.append)
            self.assertEqual([update.phase for update in updates], ['准备', '结束'])
            self.assertTrue(all(update.target_bssid==MAC for update in updates))

    def test_success_handler_clears_active_target(self):
        service = Mock()
        service.openwrt_backend.snapshot = OpenWrtSessionSnapshot(task_type=CaptureTaskType.WPS, task_state='capturing', capturing_bssids=(MAC,))
        CaptureService._on_openwrt_wps_succeeded(service, ())
        self.assertEqual(service.openwrt_backend.snapshot.capturing_bssids, ())
