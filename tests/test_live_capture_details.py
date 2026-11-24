"""Regression coverage for live capture details and the reused report window."""
import unittest
from pathlib import Path
from unittest.mock import MagicMock
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QWidget
from modules.capture_services.openwrt.live_capture_details import LiveCaptureDetails
from modules.capture_services.openwrt.auto_capture_service import AutoCaptureRadioSession
from modules.capture_services.openwrt.models import OpenWrtRadioInfo
from modules.main_pages.packet_capture.openwrt.auto_capture_radio_card import AutoCaptureRadioCard
from modules.main_pages.packet_capture.openwrt.auto_capture_report_dialog import AutoCaptureReportDialog
from modules.display_privacy import DisplayPrivacy, MASKED_TEXT

AP = "   1 08:52:54 + +     + 54a7035d5758 Test Wifi\n"
STA = "  08:52:56 + + 54a7035d5758 3ac62034d21b Test Wifi\n"

class LiveCaptureDetailsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_chunks_merge_and_cached_snapshot(self):
        tracker = LiveCaptureDetails()
        tracker.feed(AP[:30])
        self.assertEqual(tracker.snapshot()["targets"], ())
        tracker.feed(AP[30:])
        tracker.feed(STA)
        target = tracker.snapshot()["targets"][0]
        self.assertEqual(target["ssid"], "Test Wifi")
        self.assertEqual(target["handshake_frames"], "M1+M2")
        previous = tracker.snapshot()
        tracker.feed(STA)
        self.assertIs(previous, tracker.snapshot())
        tracker.feed(AP.replace("54a7035d5758", "64bf6be17c64"))
        self.assertEqual(len(tracker.snapshot()["targets"]), 2)

    def test_capacity_and_long_line_are_bounded(self):
        tracker = LiveCaptureDetails(limit=1, line_limit=100)
        tracker.feed(AP)
        tracker.feed(AP.replace("54a7035d5758", "64bf6be17c64"))
        self.assertTrue(tracker.snapshot()["truncated"])
        self.assertEqual(len(tracker.snapshot()["targets"]), 1)
        tracker.feed("x" * 500)
        self.assertEqual(tracker._pending, "")
        tracker.feed("\n" + AP)
        self.assertEqual(tracker.snapshot()["targets"][0]["ssid"], "Test Wifi")

    def test_session_split_utf8_and_count_consistency(self):
        radio = OpenWrtRadioInfo(name="radio0", interface="phy0", bands=("2.4G",), free=True)
        session = AutoCaptureRadioSession(radio, MagicMock(), MagicMock(), Path("."), MagicMock())
        raw = AP.replace("Test Wifi", "\u4e2d\u6587WiFi").encode("utf-8")
        for byte in raw:
            session._parse_stream_stats(session._stream_decoder.decode(bytes([byte])))
        stats = session._build_stats_dict()
        self.assertEqual(stats["capture_details"]["targets"][0]["ssid"], "\u4e2d\u6587WiFi")
        self.assertEqual(stats["eapol_count"], 1)

    def test_real_hcx_flag_three_upgrades_open_dialog_without_file_analysis(self):
        from modules.capture_services.openwrt.auto_capture_log_analyzer import AutoCaptureLogAnalyzer
        radio = OpenWrtRadioInfo(name="radio0", interface="phy0", bands=("2.4G",), free=True)
        owner = QWidget()
        card = AutoCaptureRadioCard(radio, owner)
        tracker = LiveCaptureDetails()
        first = "   1 12:00:00 + +     + a660283d033d TP-514-B\n"
        completed = "   1 12:00:01 + + +   + a660283d033d TP-514-B\n"
        tracker.feed(first)
        card.apply_stats(dict(state="capturing",capture_details=tracker.snapshot(),pmkid_count=0,eapol_count=1))
        card.capture_details.show()
        dialog=card.capture_details.dialog
        self.assertIn("\u4e0d\u53ef\u7528\u5305",dialog.ap_table.item(0,4).text())
        tracker.feed(completed)
        row=tracker.snapshot()["targets"][0]
        self.assertTrue(row["tool_handshake"])
        self.assertEqual(row["tool_handshake_kind"],"m123")
        card.apply_stats(dict(state="capturing",capture_details=tracker.snapshot(),pmkid_count=0,eapol_count=1))
        self.assertIn("\u53ef\u7528\u63e1\u624b\u5305",dialog.ap_table.item(0,4).text())
        self.assertEqual(dialog.ap_table.item(0,3).text(),"\u5de5\u5177\u62a5\u544a M1+M2+M3")
        self.assertTrue(AutoCaptureLogAnalyzer.analyze_log_text(completed).targets[0].tool_handshake)
        dialog.close();owner.deleteLater()

    def test_eap_start_is_not_m2(self):
        from modules.capture_services.openwrt.auto_capture_log_analyzer import AutoCaptureLogAnalyzer
        started=" 08:52:54 +   54a7035d5758 3ac62034d21b Test\n"
        m2=" 08:52:54   + 54a7035d5758 3ac62034d21b Test\n"
        row=AutoCaptureLogAnalyzer.analyze_log_text(started).targets[0]
        self.assertFalse(row.has_eapol);self.assertFalse(row.tool_handshake)
        row=AutoCaptureLogAnalyzer.analyze_log_text(m2).targets[0]
        self.assertTrue(row.has_eapol);self.assertTrue(row.tool_handshake)
        self.assertEqual(row.tool_handshake_kind,"m12")

    def test_window_reuse_updates_privacy_and_capture_lifecycle(self):
        owner = QWidget()
        owner.display_privacy = DisplayPrivacy(owner)
        radio = OpenWrtRadioInfo(name="radio0", interface="phy0", bands=("2.4G",), free=True)
        card = AutoCaptureRadioCard(radio, owner)
        tracker = LiveCaptureDetails()
        tracker.feed(AP)
        stats = dict(state="capturing", capture_details=tracker.snapshot(), pmkid_count=0, eapol_count=1)
        card.apply_stats(stats)
        presenter = card.capture_details
        presenter.show()
        dialog = presenter.dialog
        self.assertIs(type(dialog), AutoCaptureReportDialog)
        self.assertEqual(dialog.ap_table.rowCount(), 1)
        self.assertEqual(dialog.ap_table.item(0, 1).text(), "Test Wifi")
        presenter.show()
        self.assertIs(presenter.dialog, dialog)
        tracker.feed(AP.replace("54a7035d5758", "64bf6be17c64"))
        card.apply_stats(dict(stats, capture_details=tracker.snapshot(), eapol_count=2))
        self.assertEqual(dialog.ap_table.rowCount(), 2)
        owner.display_privacy.set_enabled(True)
        self.assertEqual(dialog.ap_table.item(0, 1).text(), MASKED_TEXT)
        self.assertEqual(dialog.ap_table.item(0, 0).text(), MASKED_TEXT)
        owner.display_privacy.set_enabled(False)
        self.assertEqual(dialog.ap_table.item(0, 1).text(), "Test Wifi")
        card.apply_state("idle")
        self.assertEqual(dialog.ap_table.rowCount(), 2)
        card.apply_state("starting")
        self.assertEqual(dialog.ap_table.rowCount(), 0)
        dialog.close()
        self.assertIsNone(presenter.dialog)
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        presenter.show()
        self.assertIsNot(presenter.dialog, dialog)
        presenter.dialog.close()
        owner.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

if __name__ == "__main__":
    unittest.main()
