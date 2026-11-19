"""Invalid scan records must not stop other selected capture targets."""
import unittest
from threading import Event
from types import SimpleNamespace as NS
from unittest.mock import Mock
from modules.capture_services.capture_service import CaptureService
from modules.capture_services.openwrt.models import (
    AccessPointInfo, OpenWrtSessionSnapshot, OpenWrtConnectionState)
from modules.capture_services.openwrt.handshake_targets import has_valid_channel


class InvalidChannelTests(unittest.TestCase):
    def points(self):
        return tuple(AccessPointInfo(f"02:11:22:33:44:{i:02x}", channel=channel,
                    band="5G", selected=True, encryption="WPA2")
                     for i, channel in enumerate((-1, 0, 36, 161)))

    def owner(self, points):
        backend = NS(snapshot=OpenWrtSessionSnapshot(
            connection_state=OpenWrtConnectionState.CONNECTED, access_points=points),
            capture_handshakes_passive=Mock(), probe_handshake_takeover=Mock())
        return NS(_shutting_down=False,
            task_controller=NS(busy=False, begin=Mock(return_value=Event())),
            openwrt_backend=backend, messageGenerated=NS(emit=Mock()),
            openwrtSnapshotChanged=NS(emit=Mock()), _stash_privacy_mask=Mock(),
            _stash_result_prefs=Mock(), _masked_target=lambda ssid, bssid: bssid,
            _start_openwrt_worker=Mock(), _on_openwrt_handshakes_succeeded=Mock(),
            _on_openwrt_handshakes_failed=Mock(), _on_openwrt_handshakes_progress=Mock(),
            _on_openwrt_handshakes_message=Mock(), _on_takeover_probe_succeeded=Mock(),
            _on_takeover_probe_failed=Mock())

    def test_mixed_capture_only_launches_valid_targets(self):
        points = self.points()
        owner = self.owner(points)
        self.assertTrue(CaptureService.capture_openwrt_handshakes_async(
            owner, {}, [p.bssid for p in points]))
        owner._start_openwrt_worker.call_args.args[1](Event(), Mock(), Mock())
        self.assertEqual(owner.openwrt_backend.capture_handshakes_passive.call_args.args[2], points[2:])

    def test_mixed_preflight_only_probes_valid_targets(self):
        points = self.points()
        owner = self.owner(points)
        self.assertTrue(CaptureService.probe_openwrt_takeover_async(
            owner, {}, [p.bssid for p in points]))
        owner._start_openwrt_worker.call_args.args[1](Event())
        self.assertEqual(owner.openwrt_backend.probe_handshake_takeover.call_args.args[0], points[2:])

    def test_all_invalid_never_starts_remote_task(self):
        for method in (CaptureService.probe_openwrt_takeover_async,
                       CaptureService.capture_openwrt_handshakes_async):
            points = self.points()[:2]
            owner = self.owner(points)
            self.assertFalse(method(owner, {}, [p.bssid for p in points]))
            owner.task_controller.begin.assert_not_called()
            owner._start_openwrt_worker.assert_not_called()

    def test_page_preflight_ignores_invalid_records_without_missing_result_error(self):
        from modules.main_pages.packet_capture_page import PacketCapturePage
        points = self.points()
        snapshot = self.owner(points).openwrt_backend.snapshot
        selected = tuple(p.bssid for p in points)
        owner = NS(_pending_takeover_probe=("probe", {}, selected),
            capture_service=NS(openwrt_snapshot=snapshot),
            info_panel=NS(append_info=Mock()), _launch_handshake_with_probe_result=Mock())
        entries = tuple(NS(bssid=p.bssid, error="", need_takeover=False) for p in points[2:])
        PacketCapturePage._on_takeover_probe_finished(owner, "probe", entries, "", False)
        self.assertEqual(owner._launch_handshake_with_probe_result.call_args.args[2], list(points[2:]))

    def test_status_recovers_on_fresh_valid_scan(self):
        from tests.test_file_selection import APP
        from modules.main_pages.packet_capture.openwrt.access_point_table import AccessPointTable
        from dataclasses import replace
        table = AccessPointTable()
        try:
            point = self.points()[0]
            table.set_access_points((point,))
            self.assertEqual(table.item(0, table.STATUS_COLUMN).text(), "\u4fe1\u9053\u9519\u8bef")
            table.set_access_points((replace(point, channel=36),))
            self.assertNotEqual(table.item(0, table.STATUS_COLUMN).text(), "\u4fe1\u9053\u9519\u8bef")
        finally:
            table.deleteLater()
            APP.processEvents()

    def test_open_unknown_channel_still_reports_channel_error(self):
        from tests.test_file_selection import APP
        from modules.main_pages.packet_capture.openwrt.access_point_table import AccessPointTable
        from dataclasses import replace
        table = AccessPointTable()
        try:
            table.set_access_points((replace(self.points()[0], encryption="OPN"),))
            self.assertEqual(table.item(0, table.STATUS_COLUMN).text(), "\u4fe1\u9053\u9519\u8bef")
        finally:
            table.deleteLater()
            APP.processEvents()

    def test_band_mismatch_is_rejected(self):
        self.assertFalse(has_valid_channel(AccessPointInfo("x", channel=1, band="5G")))
        self.assertFalse(has_valid_channel(AccessPointInfo("x", channel=36, band="2.4G")))

    def test_invalid_channels_cannot_be_checked_by_any_selection_entry(self):
        from tests.test_file_selection import APP
        from PySide6.QtCore import Qt
        from dataclasses import replace
        from modules.main_pages.packet_capture.openwrt.access_point_table import AccessPointTable
        table = AccessPointTable()
        try:
            points = tuple(replace(p, ssid="target") for p in self.points())
            table.set_access_points(points)
            for action in (table.select_all, table.invert_selection,
                           lambda: table._select_matching(lambda point: True)):
                action()
                for row in (0, 1):
                    item = table.item(row, table.STATUS_COLUMN)
                    self.assertFalse(item.flags() & Qt.ItemIsEnabled)
                    self.assertEqual(item.checkState(), Qt.Unchecked)
                    self.assertFalse(table._can_paint(table.model().index(row, table.STATUS_COLUMN)))
                self.assertTrue(set(table.selected_bssids()).issubset({p.bssid for p in points[2:]}))
            item = table.item(0, table.STATUS_COLUMN)
            item.setCheckState(Qt.Checked)
            self.assertEqual(item.checkState(), Qt.Unchecked)
            table.set_access_points((replace(points[0], channel=36),))
            table.select_all()
            self.assertTrue(table.item(0, table.STATUS_COLUMN).flags() & Qt.ItemIsEnabled)
            self.assertEqual(table.selected_bssids(), (points[0].bssid,))
        finally:
            table.deleteLater()
            APP.processEvents()
