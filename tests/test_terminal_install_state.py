"""Terminal dependency UI follows task state even while its page is hidden."""
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock,patch
from PySide6.QtCore import QObject,Signal
from tests.test_terminal_runtime import Owner
from modules.capture_services.router_terminal_service import RouterTerminalService
from modules.main_pages.packet_capture.openwrt.router_terminal_page import RouterTerminalPage,ConfirmDialog

class Dependency(QObject):
    taskChanged=Signal(object)
    taskFinished=Signal(str,bool,str)
    def __init__(self,parent):
        super().__init__(parent);self.task=None;self.last_rejection_reason='another task is busy'
        self.install_async=Mock(return_value=False)
    def cancel(self,session): pass

def task(session='install-one'):
    return NS(session_id=session,group='terminal',action='install',message='installing tmux')

class TerminalInstallStateTests(unittest.TestCase):
    def page(self,current=None):
        owner=Owner();owner.dependency_service=Dependency(owner)
        owner.dependency_service.task=current
        service=RouterTerminalService(owner);service.session_mode='plain_missing'
        page=RouterTerminalPage(service)
        self.addCleanup(owner.deleteLater);self.addCleanup(page.deleteLater);self.addCleanup(page.dispose)
        return owner,service,page
    def finish(self,owner,session,success,message):
        dependency=owner.dependency_service;dependency.task=None;owner.is_busy=False
        dependency.taskChanged.emit(None);dependency.taskFinished.emit(session,success,message)
    def test_hidden_failure_and_cancel_restore_button_and_report_reason(self):
        for reason in ('installation failed','installation cancelled'):
            with self.subTest(reason=reason):
                owner,service,page=self.page();dependency=owner.dependency_service
                dependency.task=task();owner.is_busy=True;dependency.taskChanged.emit(dependency.task)
                self.assertFalse(page.isVisible());self.assertEqual(page._install_session,'install-one')
                self.assertIsNone(page._install_dialog);self.assertFalse(page.install_button.isEnabled())
                self.finish(owner,'install-one',False,reason)
                self.assertTrue(page.install_button.isEnabled())
                self.assertEqual(page.install_button.text(),'\u5b89\u88c5\u4f1a\u8bdd\u4fdd\u7559\u652f\u6301')
                self.assertIn(reason,page.notice.text());self.assertFalse(page._session_support_installed)
    def test_recreated_page_adopts_running_task_without_popup(self):
        owner,service,page=self.page(task())
        self.assertEqual(page._install_session,'install-one');self.assertIsNone(page._install_dialog)
        self.finish(owner,'install-one',False,'failed after recreation')
        self.assertIn('failed after recreation',page.notice.text())
        self.assertTrue(page.install_button.isEnabled())
    def test_removed_task_cannot_leave_installing_label(self):
        owner,service,page=self.page(task())
        owner.dependency_service.task=None;page._status(service.state)
        self.assertEqual(page.install_button.text(),'\u5b89\u88c5\u4f1a\u8bdd\u4fdd\u7559\u652f\u6301')
    def test_success_enables_session_support_only_after_completion(self):
        owner,service,page=self.page(task())
        with patch.object(page,'enter') as enter:
            self.assertFalse(page._session_support_installed)
            self.finish(owner,'install-one',True,'verified ready')
            self.assertTrue(page._session_support_installed);self.assertTrue(page.install_button.isHidden())
            self.assertEqual(page._install_failure,'');enter.assert_called_once_with()
    def test_old_result_does_not_finish_new_task(self):
        owner,service,page=self.page(task('new-task'))
        owner.dependency_service.taskFinished.emit('old-task',False,'old failure')
        self.assertEqual(page._install_session,'new-task')
        self.assertFalse(page.install_button.isEnabled());self.assertEqual(page._install_failure,'')
    def test_rejected_install_is_reported_without_installing_label(self):
        owner,service,page=self.page()
        with patch.object(ConfirmDialog,'exec',return_value=ConfirmDialog.Accepted):
            page.install_session_support()
        self.assertIn(owner.dependency_service.last_rejection_reason,page.notice.text())
        self.assertEqual(page.install_button.text(),'\u5b89\u88c5\u4f1a\u8bdd\u4fdd\u7559\u652f\u6301')

    def test_real_dependency_completion_restores_hidden_page_after_failed_verification(self):
        from dataclasses import replace
        from tests.test_dependency_diagnostic_paths import Owner as DependencyOwner
        from modules.capture_services.dependency_service import OpenWrtDependencyService
        from modules.capture_services.openwrt.models import DependencyInfo,DependencyState
        class UiOwner(DependencyOwner):
            @property
            def openwrt_snapshot(self):return self.openwrt_backend.snapshot
        owner=UiOwner();dependency=OpenWrtDependencyService(owner);owner.dependency_service=dependency
        missing=DependencyInfo('terminal','terminal',DependencyState.MISSING,detail='tmux not available')
        owner.openwrt_backend.snapshot=replace(owner.openwrt_backend.snapshot,dependencies=(missing,))
        service=RouterTerminalService(owner);service.session_mode='plain_missing'
        page=RouterTerminalPage(service)
        try:
            dependency.manager.install_all=Mock(return_value=(missing,))
            self.assertTrue(dependency.install_async({'terminal':True},scope='terminal'))
            session=dependency.task.session_id;operation,event=owner.pending
            result=operation(event,Mock())
            self.assertTrue(result['error'])
            dependency._complete(session,result)
            self.assertIsNone(dependency.task);self.assertFalse(owner.is_busy)
            self.assertEqual(page.install_button.text(),'\u5b89\u88c5\u4f1a\u8bdd\u4fdd\u7559\u652f\u6301')
            self.assertTrue(page.install_button.isEnabled())
            self.assertIn(result['error'],page.notice.text())
            self.assertFalse(page._session_support_installed)
        finally:
            page.dispose();page.deleteLater();owner.deleteLater()
