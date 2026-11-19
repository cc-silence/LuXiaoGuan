"""Offline reports follow global display privacy without mutating analysis."""
import unittest
from PySide6.QtWidgets import QWidget, QTextBrowser
from tests.test_standalone import APP
from modules.display_privacy import DisplayPrivacy
from modules.capture_services.openwrt.auto_capture_log_analyzer import AutoCaptureDiagnosticReport, CapturedTargetSummary
from modules.main_pages.packet_capture.openwrt.auto_capture_report_dialog import AutoCaptureReportDialog

class ReportPrivacyTests(unittest.TestCase):
    def test_open_and_toggle_mask_all_surfaces_and_restore_raw(self):
        for enabled in (False,True):
            with self.subTest(enabled=enabled):
                owner=QWidget();owner.display_privacy=DisplayPrivacy(owner)
                privacy=owner.display_privacy;privacy.set_enabled(enabled)
                raw='SSID: Private-Network\nAA:BB:CC:DD:EE:FF Private-Network\n'
                target=CapturedTargetSummary('AA:BB:CC:DD:EE:FF','Private-Network','6',True,True,'M1+M2')
                report=AutoCaptureDiagnosticReport(targets=[target],raw_summary=raw,hardware_notes=['Private-Network'])
                dialog=AutoCaptureReportDialog(report,'C:/capture',owner)
                browser=dialog.findChild(QTextBrowser,'reportLogBrowser')
                privacy.set_enabled(True);APP.processEvents()
                self.assertNotIn('Private-Network',browser.toPlainText())
                self.assertNotIn('AA:BB:CC:DD:EE:FF',browser.toPlainText())
                self.assertNotIn('Private-Network',dialog.ap_table.item(0,1).text())
                self.assertNotIn('AA:BB:CC:DD:EE:FF',dialog.ap_table.item(0,0).text())
                browser.selectAll()
                self.assertNotIn('Private-Network',browser.textCursor().selectedText())
                self.assertEqual(report.raw_summary,raw)
                self.assertEqual(target.ssid,'Private-Network')
                privacy.set_enabled(False);APP.processEvents()
                self.assertEqual(browser.toPlainText(),raw.strip())
                self.assertEqual(dialog.ap_table.item(0,1).text(),'Private-Network')
                dialog.close();dialog.deleteLater();owner.deleteLater();APP.processEvents()

    def test_live_updates_register_new_identities_without_leaking(self):
        owner=QWidget();owner.display_privacy=DisplayPrivacy(owner)
        privacy=owner.display_privacy;privacy.set_enabled(True)
        report=AutoCaptureDiagnosticReport(targets=[CapturedTargetSummary('AA:BB:CC:DD:EE:FF','First')])
        dialog=AutoCaptureReportDialog(report,'C:/capture',owner,live=True)
        report.targets.append(CapturedTargetSummary('11:22:33:44:55:66','Later'))
        dialog.update_live_report(report);APP.processEvents()
        self.assertNotEqual(dialog.ap_table.item(1,1).text(),'Later')
        privacy.set_enabled(False);APP.processEvents()
        self.assertEqual(dialog.ap_table.item(1,1).text(),'Later')
        dialog.close();dialog.deleteLater();owner.deleteLater();APP.processEvents()
