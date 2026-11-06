"""Nonblocking verified-password notifications, delivery privacy and cleanup."""
import json
import unittest
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from PySide6.QtCore import QObject, Signal, Slot, Qt, QEvent
from PySide6.QtWidgets import QWidget, QPushButton, QVBoxLayout, QApplication
from PySide6.QtTest import QTest
from shiboken6 import isValid
from tests.test_file_selection import APP
from modules.ui_scale_manager import UiScaleManager
from modules.capture_services.capture_service import CaptureService
from modules.capture_services.openwrt.models import OpenWrtSessionSnapshot, CaptureTaskType
from modules.capture_services.openwrt.password_events import PasswordFoundNotice
from modules.capture_services.openwrt.password_verification import OpenWrtPasswordVerifier
from modules.capture_services.openwrt.password_reporting import PasswordVerificationReporter
from modules.main_pages.packet_capture.openwrt.password_found_notices import PasswordFoundNotices
from modules.main_pages.packet_capture_page import PacketCapturePage
from modules.common_widgets.common_dialogs import apply_common_dialog_theme


class Service(QObject):
    passwordFound = Signal(object)
    openwrtSnapshotChanged = Signal(object)
    def __init__(self):
        super().__init__()
        self._mask_ssid = self._mask_bssid = self._shutting_down = False
        self.openwrt_backend = NS(snapshot=OpenWrtSessionSnapshot(task_type=CaptureTaskType.PASSWORD_VERIFY, task_state='capturing'))
        self.task_controller = Mock()
    @Slot(object)
    def on_progress(self, update):
        CaptureService._on_openwrt_password_progress(self, update)


class NotificationsTests(unittest.TestCase):
    def setUp(self):
        self.service = Service()
        self.owner = QWidget()
        self.owner.scale_manager = UiScaleManager(100)
        self.owner.resize(900,600)
        self.button = QPushButton('Still usable', self.owner)
        QVBoxLayout(self.owner).addWidget(self.button)
        self.controller = PasswordFoundNotices.for_window(self.owner, self.service)
        self.owner.show()
        APP.processEvents()
    def tearDown(self):
        if isValid(self.owner):
            self.controller.messages.close_all()
            self.controller.summaries.close_all()
            self.owner.close();self.owner.deleteLater()
        APP.sendPostedEvents(None,QEvent.DeferredDelete);APP.processEvents()
    def notice(self, number=1):
        return PasswordFoundNotice('Target '+str(number),'02:11:22:33:44:55','result-'+str(number)+'.txt','AuditOnly-'+str(number))
    def emit(self, number=1):
        event=self.notice(number);self.service.on_progress(event);APP.processEvents();return event
    def test_new_success_does_not_wait_for_old_dialog_or_disable_owner(self):
        first=self.emit(1);second=self.emit(2)
        records=list(self.controller.messages._windows.values())
        self.assertEqual(len(records),1)
        self.assertEqual((first.password,second.password),('',''))
        self.assertIsNone(QApplication.activeModalWidget())
        clicked=Mock();self.button.clicked.connect(clicked)
        QTest.mouseClick(self.button,Qt.LeftButton)
        clicked.assert_called_once()
        for record in records:
            self.assertFalse(record.dialog.isModal())
            self.assertEqual(record.dialog.windowModality(),Qt.NonModal)
            self.assertTrue(record.dialog.isVisible())
        self.assertIn('AuditOnly-1',records[0].dialog.message_box.toPlainText())
        self.assertIn('AuditOnly-2',records[0].dialog.message_box.toPlainText())
        self.assertNotIn('AuditOnly',json.dumps(asdict(self.service.openwrt_backend.snapshot),default=str))
        records[0].dialog.ok_button.click();APP.processEvents()
        self.assertEqual(len(self.controller.messages._windows),0)
        self.emit(3)
        fresh = next(iter(self.controller.messages._windows.values()))
        self.assertIn('AuditOnly-3', fresh.dialog.message_box.toPlainText())
        self.assertNotIn('AuditOnly-1', fresh.dialog.message_box.toPlainText())
        fresh.dialog.close_btn.click();APP.processEvents()
        APP.sendPostedEvents(None,QEvent.DeferredDelete)
        self.assertFalse(isValid(records[0].dialog))
    def test_worker_queue_delivers_on_gui_without_acknowledgment(self):
        class Source(QObject):
            progress=Signal(object)
        source=Source();source.progress.connect(self.service.on_progress)
        events=[self.notice(i) for i in (1,2,3)]
        thread=Thread(target=lambda:[source.progress.emit(e) for e in events])
        thread.start();thread.join(2)
        self.assertFalse(thread.is_alive())
        APP.processEvents()
        self.assertEqual(len(self.controller.messages._windows),1)
        self.assertTrue(all(e.password=='' for e in events))
    def test_unacknowledged_notifications_and_message_buffers_are_bounded(self):
        for number in range(100):self.emit(number)
        records=list(self.controller.messages._windows.values())
        self.assertEqual(len(records),1)
        self.assertEqual(len(records[-1].messages),100)
        self.assertIn('AuditOnly-99',records[-1].dialog.message_box.toPlainText())
        self.assertIn('AuditOnly-8\n',records[-1].dialog.message_box.toPlainText())
        self.controller.messages.close_all();APP.processEvents()
        self.assertEqual(len(self.controller.messages._windows),0)
        self.assertTrue(all(not r.messages for r in records))
    def test_incremental_buffer_prunes_whole_unicode_messages(self):
        from modules.common_widgets.modeless_messages import ModelessMessageGroup
        group = ModelessMessageGroup(self.owner, max_windows=1, max_messages=2, incremental=True)
        first = group.show_message('Results', 'old\n\U0001f680')
        second = group.show_message('Results', 'middle\nline')
        self.assertIs(first, second)
        group.show_message('Results', 'new\n\U0001f600')
        self.assertEqual(first.message_box.toPlainText(), 'middle\nline\n\nnew\n\U0001f600\n')
        group.close_all()

    def test_shared_owner_does_not_duplicate_connections_on_page_recreation(self):
        self.assertIs(PasswordFoundNotices.for_window(self.owner,self.service),self.controller)
        self.emit()
        self.assertEqual(len(self.controller.messages._windows),1)
    def test_summary_while_success_is_unacknowledged_is_also_modeless(self):
        self.emit()
        page=NS(password_notices=self.controller,info_panel=NS(display_text=lambda text:text))
        PacketCapturePage._show_task_result_notice(page,'\u731c\u5bc6\u7801\u7ed3\u679c','Finished')
        self.assertEqual(len(self.controller.messages._windows),1)
        self.assertEqual(len(self.controller.summaries._windows),1)
        self.assertIsNone(QApplication.activeModalWidget())
    def test_display_is_plain_text_and_identity_mask_does_not_change_password(self):
        self.service._mask_ssid=self.service._mask_bssid=True
        event=self.notice();event.password='<b>AuditOnly</b>'
        self.service.on_progress(event)
        dialog=next(iter(self.controller.messages._windows.values())).dialog
        text=dialog.message_box.toPlainText()
        self.assertIn('<b>AuditOnly</b>',text)
        self.assertNotIn('Target 1',text);self.assertNotIn('02:11:22:33:44:55',text)
        self.assertEqual(event.password,'')
    def test_shutting_down_discards_queued_secret_without_showing_window(self):
        self.service._shutting_down=True
        event=self.emit()
        self.assertEqual(event.password,'')
        self.assertFalse(self.controller.messages._windows)
    def test_owner_destruction_releases_unacknowledged_dialogs_and_buffers(self):
        self.emit(1);self.emit(2)
        records=list(self.controller.messages._windows.values())
        self.owner.deleteLater()
        APP.sendPostedEvents(None,QEvent.DeferredDelete);APP.processEvents()
        self.assertTrue(all(not isValid(r.dialog) for r in records))
        self.assertTrue(all(not r.messages for r in records))
        self.assertFalse(self.controller.messages._windows)

    def test_render_both_themes_and_button_text_fits(self):
        output=Path('build/previews/password-found');output.mkdir(parents=True,exist_ok=True)
        for theme in ('light.qss','dark.qss'):
            self.emit()
            dialog=list(self.controller.messages._windows.values())[-1].dialog
            apply_common_dialog_theme(dialog,self.owner,theme_name=theme)
            APP.processEvents()
            self.assertTrue(dialog.grab().save(str(output/(theme+'.png'))))
            self.assertGreaterEqual(dialog.ok_button.width(),dialog.ok_button.fontMetrics().horizontalAdvance(dialog.ok_button.text()))
            dialog.close();APP.processEvents()


class DeliveryTests(unittest.TestCase):
    def test_verified_result_is_delivered_immediately_without_password_in_logs(self):
        target=NS(ssid='Target',bssid='02:11:22:33:44:55',band='5G',encryption='WPA2-PSK')
        verifier=OpenWrtPasswordVerifier(Mock())
        verifier._pick_radio=Mock(return_value='radio1');verifier._setup_environment=Mock()
        control=Mock();control.attempt.return_value=True
        events=[];messages=Mock()
        reporter=PasswordVerificationReporter((target,),{},messages,events.append)
        with TemporaryDirectory() as folder,patch('modules.capture_services.openwrt.password_control.open_control',return_value=control):
            result=verifier._verify_single(target,['AuditOnly-pass'],0,1,'owned','net',folder,None,reporter.progress,False)
            found=[e for e in events if isinstance(e,PasswordFoundNotice)]
            self.assertEqual(len(found),1)
            self.assertEqual(found[0].password,'AuditOnly-pass')
            self.assertTrue(Path(found[0].password_file).is_file())
            self.assertNotIn('AuditOnly-pass',repr(found[0]))
            self.assertNotIn('AuditOnly-pass',repr(result))
            self.assertNotIn('AuditOnly-pass',repr(messages.call_args_list))
            found[0].clear()

if __name__=='__main__':unittest.main()
