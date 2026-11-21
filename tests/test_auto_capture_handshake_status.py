"""Live and offline reports classify observed frame combinations alike."""
import unittest
from PySide6.QtWidgets import QApplication
from modules.capture_services.openwrt.auto_capture_log_analyzer import AutoCaptureDiagnosticReport, CapturedTargetSummary
from modules.main_pages.packet_capture.openwrt.auto_capture_report_dialog import AutoCaptureReportDialog

class HandshakeStatusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def test_live_and_offline_use_same_frame_combinations(self):
        for live in (True,False):
            for frames,usable in (("M1+M2",True),("M2+M3",True),("M1+M2+M3+M4",True),("M1+M3",False),("M1+M4",False),("M3+M4",False),("M2",False),("",False)):
                with self.subTest(live=live,frames=frames):
                    row=CapturedTargetSummary('00:11:22:33:44:55',has_eapol=bool(frames),handshake_frames=frames)
                    dialog=AutoCaptureReportDialog(AutoCaptureDiagnosticReport(targets=[row]),'',live=live)
                    try:
                        expected='\u53ef\u7528\u63e1\u624b\u5305' if usable else '\u4e0d\u53ef\u7528\u5305'
                        self.assertEqual(dialog.ap_table.item(0,4).text(),expected)
                    finally:
                        dialog.close();dialog.deleteLater()

    def test_pmkid_alone_is_not_marked_as_usable_handshake(self):
        row=CapturedTargetSummary('00:11:22:33:44:55',has_pmkid=True)
        dialog=AutoCaptureReportDialog(AutoCaptureDiagnosticReport(targets=[row]),'',live=True)
        try:
            self.assertIn('\u4e0d\u53ef\u7528\u5305',dialog.ap_table.item(0,4).text())
        finally:
            dialog.close();dialog.deleteLater()

    def test_sorting_and_live_updates_keep_rows_together(self):
        from dataclasses import replace
        from PySide6.QtCore import Qt
        rows=[CapturedTargetSummary('00:11:22:33:44:01','Alpha','11',has_eapol=True,handshake_frames='M1'),
              CapturedTargetSummary('00:11:22:33:44:02','Beta','2',has_eapol=True,handshake_frames='M2'),
              CapturedTargetSummary('00:11:22:33:44:03','Gamma','36',has_eapol=True,handshake_frames='M1')]
        for live in (False,True):
            with self.subTest(live=live):
                dialog=AutoCaptureReportDialog(AutoCaptureDiagnosticReport(targets=rows),'',live=live)
                try:
                    table=dialog.ap_table
                    self.assertTrue(table.isSortingEnabled())
                    table.sortItems(2,Qt.AscendingOrder)
                    self.assertEqual([table.item(i,2).text() for i in range(3)],['2','11','36'])
                    changed=[replace(rows[0],handshake_frames='M1+M2'),rows[1],rows[2]]
                    dialog._set_targets(changed)
                    self.assertEqual([table.item(i,1).text() for i in range(3)],['Beta','Alpha','Gamma'])
                    self.assertEqual(table.item(1,4).text(),'\u53ef\u7528\u63e1\u624b\u5305')
                    self.assertEqual(table.item(0,4).text(),'\u4e0d\u53ef\u7528\u5305')
                    dialog._set_targets(changed+[CapturedTargetSummary('00:11:22:33:44:04','Delta','6')])
                    self.assertEqual([table.item(i,2).text() for i in range(4)],['2','6','11','36'])
                finally:
                    dialog.close();dialog.deleteLater()

    def test_sorting_after_privacy_change_preserves_raw_rows(self):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QWidget
        from modules.display_privacy import DisplayPrivacy
        owner=QWidget();owner.display_privacy=DisplayPrivacy(owner)
        rows=[CapturedTargetSummary('00:11:22:33:44:01','Alpha','11'),CapturedTargetSummary('00:11:22:33:44:02','Beta','2')]
        dialog=AutoCaptureReportDialog(AutoCaptureDiagnosticReport(targets=rows),'',owner)
        try:
            table=dialog.ap_table;table.sortItems(2,Qt.AscendingOrder)
            owner.display_privacy.set_enabled(True)
            owner.display_privacy.set_enabled(False)
            self.assertEqual([(table.item(i,1).text(),table.item(i,2).text()) for i in range(2)],[('Beta','2'),('Alpha','11')])
        finally:
            dialog.close();dialog.deleteLater();owner.deleteLater()
