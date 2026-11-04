"""Nonblocking update credentials, safe shutdown handoff and common progress UI."""
from configparser import ConfigParser
import unittest
from unittest.mock import Mock,patch
from PySide6.QtCore import QObject,QThread
from PySide6.QtTest import QSignalSpy
from tests.test_standalone import APP
from modules.desktop_shell.app_update_access import UpdateAccess
from modules.desktop_shell.app_update_presenter import AppUpdatePresenter
from modules.app_update_services.linux_update_ui import InstallDialog,InstallThread
from modules.platform_services import app_update_access as access
APP.setQuitOnLastWindowClosed(False)


class UpdateAccessTests(unittest.TestCase):
    def test_linux_load_and_save_only_publish_after_async_completion(self):
        parent=QObject();self.addCleanup(parent.deleteLater)
        with patch('modules.desktop_shell.app_update_access.is_linux',return_value=True),patch('modules.platform_services.credential_controller.CredentialController') as controller:
            controller.return_value.active=False;service=UpdateAccess(parent);done=Mock()
            service.load(done);self.assertFalse(service.loaded);done.assert_not_called()
            callback=controller.return_value.submit.call_args.args[-1];callback('valid_code',None)
            self.assertTrue(service.loaded);self.assertEqual(service.code,'valid_code');done.assert_called_once_with(None)
            done.reset_mock();service.save('new_code',done);self.assertEqual(service.code,'valid_code')
            controller.return_value.submit.call_args.args[-1](True,None)
            self.assertEqual(service.code,'new_code');done.assert_called_once_with(None)
            service.save('',done);self.assertEqual(controller.return_value.submit.call_args.args[0],'delete')
    def test_linux_storage_failure_does_not_replace_verified_memory_code(self):
        parent=QObject();self.addCleanup(parent.deleteLater)
        with patch('modules.desktop_shell.app_update_access.is_linux',return_value=True),patch('modules.platform_services.credential_controller.CredentialController') as controller:
            service=UpdateAccess(parent);service.code='old_code';done=Mock();service.save('new_code',done)
            error=OSError('locked');controller.return_value.submit.call_args.args[-1](None,error)
            self.assertEqual(service.code,'old_code');done.assert_called_once_with(error)
    def test_cli_linux_credential_uses_common_encrypted_backend(self):
        with patch('modules.platform_services.runtime.is_linux',return_value=True),patch('modules.platform_services.credentials.read_generic_password',return_value='test_code') as read:
            self.assertEqual(access.read_code(),'test_code');read.assert_called_once_with(access.TARGET)
        with patch('modules.platform_services.runtime.is_linux',return_value=True),patch('modules.platform_services.credentials.delete_generic_password') as delete:
            access.save_code('');delete.assert_called_once_with(access.TARGET)


class UpdateDesktopTests(unittest.TestCase):
    def presenter(self):
        runtime=QObject();runtime._exiting=False;runtime.main_window=None;runtime.config=ConfigParser()
        runtime.idle_blockers=Mock(return_value=[]);runtime.exit_application=Mock()
        with patch('modules.platform_services.app_update_access.read_code',return_value=''):
            presenter=AppUpdatePresenter('1.0.22_alpha',runtime)
        self.addCleanup(lambda:presenter.stop());self.addCleanup(runtime.deleteLater)
        presenter.state='ready';presenter.plan={'stage':'stage'}
        return presenter,runtime
    def test_new_task_during_confirmation_prevents_exit_and_helper_launch(self):
        presenter,runtime=self.presenter();runtime.idle_blockers.side_effect=[[],['new task']]
        with patch('modules.common_widgets.common_dialogs.ConfirmDialog') as dialog,patch('modules.platform_services.update_dispatch.begin_install') as launch:
            dialog.return_value.exec.return_value=1;presenter.install_update(None);launch.assert_not_called()
        runtime.exit_application.assert_not_called()
    def test_helper_launch_failure_does_not_exit_application(self):
        presenter,runtime=self.presenter()
        with patch('modules.common_widgets.common_dialogs.ConfirmDialog') as dialog,patch('modules.platform_services.update_dispatch.begin_install',side_effect=OSError('failure')):
            dialog.return_value.exec.return_value=1;presenter.install_update(None)
        runtime.exit_application.assert_not_called();self.assertEqual(presenter.error,'failure')
    def test_initial_theme_matches_selected_theme_without_switching(self):
        for theme,color in [('dark.qss','#181b1f'),('light.qss','#f5f6fa')]:
            with self.subTest(theme=theme):
                dialog=InstallDialog('fixture',show_immediately=False,theme_name=theme)
                dialog.show();APP.processEvents()
                self.assertEqual(dialog.window_frame.palette().window().color().name(),color)
                dialog.running=False;dialog.close();dialog.deleteLater();APP.processEvents()

    def test_install_window_cannot_cancel_transaction_but_error_can_be_closed(self):
        dialog=InstallDialog('fixture',show_immediately=False);dialog.request_cancel()
        self.assertFalse(dialog.cancelling);self.assertTrue(dialog.running)
        dialog.finish(False,'fixture failure');self.assertFalse(dialog.running)
        self.assertEqual(dialog.message.text(),'fixture failure');self.assertTrue(dialog.cancel_button.isEnabled())
        dialog.request_cancel()
    def test_independent_ui_event_loop_finishes_and_can_close_failure(self):
        import subprocess,sys
        script=r"""
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch
from modules.app_update_services import linux_update_ui as ui
import sys
success=sys.argv[1]=='success'
class Store:
    identity=[1,2]
    def __init__(self,*a):pass
    def __enter__(self):return self
    def __exit__(self,*a):pass
    def state(self,*a):return None
class Dialog(ui.InstallDialog):
    def finish(self,ok,message=''):
        super().finish(ok,message)
        if not ok:self.request_cancel()
def run(*a,**kw):
    kw['notify'](dict(phase='waiting',completed=0,total=0))
    return dict(status='applied' if success else 'failed',safe_to_restart=success,error_type='fixture')
with patch.object(ui,'stage_location',return_value=(Path.cwd(),Path.cwd())),patch.object(ui,'RootedFiles',Store),patch.object(ui,'worker_lease',return_value=nullcontext()),patch('modules.app_update_services.linux_update_jobs.private_job',return_value={}),patch.object(ui,'run_job',side_effect=run),patch.object(ui,'InstallDialog',Dialog):
    value=ui.main(['--linux-update-ui','fixture'])
print('UI_RESULT='+str(value))
"""
        for status,expected in [('success',0),('failure',1)]:
            with self.subTest(status=status):
                result=subprocess.run([sys.executable,'-c',script,status],capture_output=True,timeout=15)
                self.assertEqual(result.returncode,0,result.stderr.decode('utf-8',errors='replace'))
                self.assertIn(('UI_RESULT='+str(expected)).encode(),result.stdout)
                self.assertIn(b'ROUTERMANAGER_UPDATE_READY',result.stdout)

    def test_install_work_runs_off_ui_thread_and_finishes_before_result_use(self):
        threads=[]
        def run(*args,**kwargs):
            threads.append(QThread.currentThread()==APP.thread())
            kwargs['notify'](dict(phase='waiting',completed=0,total=0))
            return dict(status='applied',safe_to_restart=True)
        thread=InstallThread('fixture');spy=QSignalSpy(thread.finished)
        self.addCleanup(lambda:thread.wait(3000))
        with patch('modules.app_update_services.linux_update_ui.run_job',side_effect=run):
            thread.start();self.assertTrue(thread.wait(3000));APP.processEvents()
            self.assertEqual(spy.count(),1)
        self.assertEqual(threads,[False]);self.assertFalse(thread.isRunning())
        self.assertEqual(thread.outcome['status'],'applied');thread.deleteLater()


if __name__=='__main__':unittest.main()
