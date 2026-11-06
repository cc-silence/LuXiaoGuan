"""Transient password display and active-target row integration."""
import unittest
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout
from tests.test_file_selection import APP
from modules.ui_scale_manager import UiScaleManager
from modules.capture_services.capture_service import CaptureService
from modules.capture_services.openwrt.models import AccessPointInfo, OpenWrtSessionSnapshot, CaptureTaskType
from modules.capture_services.openwrt.password_verification import PasswordVerifyUpdate
from modules.capture_services.openwrt.password_reporting import PasswordVerificationReporter
from modules.main_pages.packet_capture.openwrt.openwrt_page import OpenWrtCapturePage
from modules.main_pages.packet_capture.openwrt.access_point_table import AccessPointTable
from modules.main_pages.packet_capture.openwrt.task_progress import CaptureTaskProgress
from modules.main_pages.packet_capture.capture_page_styles import CapturePageStyleManager
from modules.main_pages.base_page_styles import light_base_page_stylesheet, dark_base_page_stylesheet

class PasswordPresentationTests(unittest.TestCase):
    def test_target_switch_completion_and_private_candidate_rendering(self):
        points=tuple(AccessPointInfo(mac,ssid=name,encryption="WPA2",channel=channel)
                     for mac,name,channel in (("02:11:22:33:44:55","Target A",1),
                                              ("02:11:22:33:44:66","Target B",6)))
        snapshot=OpenWrtSessionSnapshot(task_type=CaptureTaskType.PASSWORD_VERIFY,
                                       task_state="capturing",access_points=points)
        service=NS(openwrt_backend=NS(snapshot=snapshot),openwrtSnapshotChanged=Mock(),
                   password_verification_candidate="")
        parent=QWidget();parent.scale_manager=UiScaleManager(100)
        parent.setObjectName("packetCapturePage")
        table=AccessPointTable(parent);progress=CaptureTaskProgress(parent)
        layout=QVBoxLayout(parent);layout.addWidget(table);layout.addWidget(progress)
        page=NS(service=service,access_points=table,progress=progress,
                signal_export=Mock(),tools=Mock(),_sync_display_privacy=Mock())
        candidate="AuditOnly123"
        parent.resize(1220,260);parent.show()
        try:
            for light in (True,False):
                parent.light_theme=light
                styles=CapturePageStyleManager(parent)
                base=light_base_page_stylesheet("",parent.scale_manager) if light else dark_base_page_stylesheet(parent.scale_manager)
                parent.setStyleSheet(base+styles._capture_stylesheet(light));table.set_light_theme(light)
                for index,target in enumerate(points):
                    update=PasswordVerifyUpdate(1,3,target.ssid,index,2,
                                               target_bssid=target.bssid,candidate=candidate)
                    self.assertNotIn(candidate,repr(update))
                    CaptureService._on_openwrt_password_progress(service,update)
                    snap=service.openwrt_backend.snapshot
                    self.assertNotIn(candidate,repr(asdict(snap)))
                    OpenWrtCapturePage.apply_snapshot(page,snap);APP.processEvents()
                    self.assertIn(candidate,progress.title_label.text())
                    active=next(row for row in range(table.rowCount())
                                if table.item(row,table.STATUS_COLUMN).data(Qt.UserRole)==target.bssid)
                    self.assertEqual(table.item(active,table.STATUS_COLUMN).text(),"\u9a8c\u8bc1\u4e2d")
                    for row in range(table.rowCount()):
                        for column in range(table.columnCount()):
                            bg=table.item(row,column).data(Qt.BackgroundRole)
                            if row==active:self.assertEqual(bg.color(),table._capturing_bg())
                            else:self.assertIsNone(bg)
                output=Path("build/previews/password-progress");output.mkdir(parents=True,exist_ok=True)
                self.assertTrue(parent.grab().save(str(output/("light.png" if light else "dark.png"))))
            service.openwrt_backend.snapshot=replace(snap,task_state="idle")
            OpenWrtCapturePage.apply_snapshot(page,service.openwrt_backend.snapshot)
            self.assertFalse(table._capturing);self.assertEqual(progress.title_label.text(),"")
        finally:
            parent.close();parent.deleteLater();APP.processEvents()

    def test_reporter_publishes_start_and_finish_without_candidate_messages(self):
        target=AccessPointInfo("02:11:22:33:44:55",ssid="Target")
        progress=Mock();message=Mock()
        reporter=PasswordVerificationReporter((target,),{},message,progress)
        reporter.target_event(0)
        self.assertEqual(progress.call_args.args[0].target_bssid,target.bssid)
        update=PasswordVerifyUpdate(1,2,target_label="Target",target_total=1,
                                   target_bssid=target.bssid,candidate="AuditOnly123")
        reporter.progress(update)
        self.assertNotIn("AuditOnly123",repr(message.call_args_list))
        entry=NS(tried=1,total=2,skipped=0,found=False,final=False)
        reporter.target_event(0,entry)
        self.assertEqual(progress.call_args.args[0].target_bssid,"")
        self.assertEqual(progress.call_args.args[0].candidate,"")

if __name__=="__main__":
    unittest.main()
