"""Login dependency consent, identity binding, cancellation and shared installation."""
from dataclasses import replace
from types import SimpleNamespace as NS
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from PySide6.QtWidgets import QWidget
from tests.test_standalone import APP
from tests.test_dependency_diagnostic_paths import Owner
from modules.capture_services.dependency_service import OpenWrtDependencyService
from modules.capture_services.openwrt.models import DependencyInfo, DependencyState
from modules.capture_services.openwrt.dependency_groups import COMMON_ITEMS, REQUIRED_COMMON_ITEMS, CAPTURE_KEYS
from modules.main_pages.packet_capture.openwrt.login_dependency_prompt import LoginDependencyPrompt


def rows(missing=True):
    return tuple(DependencyInfo(k,k,DependencyState.MISSING if missing and k in {'sftp','scan','iw'} else
                 DependencyState.DISABLED if k=='pmkid' else DependencyState.READY,
                 ('openssh-sftp-server',) if k=='sftp' and missing else ('aircrack-ng',) if k=='scan' and missing else ('iw',) if k=='iw' and missing else ())
                 for k in (*COMMON_ITEMS,*sorted(CAPTURE_KEYS)))


class LoginOwner(Owner):
    @property
    def openwrt_snapshot(self):return self.openwrt_backend.snapshot


class LoginDependencyPromptTests(unittest.TestCase):
    def create(self, missing=True):
        owner=LoginOwner();owner.dependency_service=OpenWrtDependencyService(owner)
        owner.openwrt_backend.snapshot=replace(owner.openwrt_backend.snapshot,dependencies=rows(missing))
        page=QWidget();page.service=owner;page.show();APP.processEvents()
        self.addCleanup(page.deleteLater);self.addCleanup(owner.deleteLater)
        return owner,page

    def settle(self):
        for _ in range(5):APP.processEvents()

    def factory(self, accepted):
        patcher=patch('modules.main_pages.packet_capture.openwrt.login_dependency_prompt.ConfirmDialog')
        factory=patcher.start();self.addCleanup(patcher.stop)
        factory.Accepted=1;factory.return_value.exec.return_value=1 if accepted else 0
        return factory

    def test_confirmation_lists_both_groups_and_runs_one_combined_install(self):
        owner,page=self.create();factory=self.factory(True)
        owner.dependency_service.install_async=Mock(return_value=True)
        prompt=LoginDependencyPrompt(page);self.addCleanup(prompt.dispose);self.settle()
        factory.assert_called_once()
        self.assertEqual(factory.call_args.args[0],'\u5b89\u88c5\u7f3a\u5931\u4f9d\u8d56')
        for package in ('openssh-sftp-server','aircrack-ng','iw'):
            self.assertIn(package,factory.call_args.args[1])
        call=owner.dependency_service.install_async.call_args
        self.assertEqual(call.kwargs,dict(scope='login_all'))
        self.assertFalse(call.args[0]['pmkid'])
        self.assertTrue(call.args[0]['scan'])
        for _ in range(4):owner.openwrtSnapshotChanged.emit(owner.openwrt_snapshot)
        self.settle();owner.dependency_service.install_async.assert_called_once()

    def test_cancel_is_remembered_across_snapshots_and_page_recreation(self):
        owner,page=self.create();factory=self.factory(False)
        owner.dependency_service.install_async=Mock()
        prompt=LoginDependencyPrompt(page);self.settle();prompt.dispose()
        rebuilt=LoginDependencyPrompt(page);self.addCleanup(rebuilt.dispose)
        owner.activityChanged.emit();self.settle()
        factory.assert_called_once();owner.dependency_service.install_async.assert_not_called()
        owner.openwrt_backend.ssh_client._client=object()
        owner.openwrtSnapshotChanged.emit(owner.openwrt_snapshot);self.settle()
        self.assertEqual(factory.call_count,2)

    def test_all_ready_consumes_login_without_prompt_or_later_uninstall_popup(self):
        owner,page=self.create(False);factory=self.factory(True)
        prompt=LoginDependencyPrompt(page);self.addCleanup(prompt.dispose);self.settle()
        owner.openwrt_backend.snapshot=replace(owner.openwrt_snapshot,dependencies=rows())
        owner.openwrtSnapshotChanged.emit(owner.openwrt_snapshot);self.settle()
        factory.assert_not_called()

    def test_partial_results_and_busy_connection_wait_for_completion(self):
        owner,page=self.create();factory=self.factory(False)
        complete=owner.openwrt_snapshot
        owner.openwrt_backend.snapshot=replace(complete,dependencies=complete.dependencies[:2])
        prompt=LoginDependencyPrompt(page);self.addCleanup(prompt.dispose);self.settle();factory.assert_not_called()
        owner.task_controller.begin('connect','login')
        owner.openwrt_backend.snapshot=complete;owner.openwrtSnapshotChanged.emit(complete);self.settle();factory.assert_not_called()
        owner.task_controller.finish();owner.activityChanged.emit();self.settle();factory.assert_called_once()

    def test_connection_changed_during_confirmation_cannot_install_into_new_device(self):
        owner,page=self.create();factory=self.factory(True)
        owner.dependency_service.install_async=Mock()
        def answer():owner.openwrt_backend.ssh_client._client=object();return 1
        factory.return_value.exec.side_effect=answer
        prompt=LoginDependencyPrompt(page);self.addCleanup(prompt.dispose);self.settle()
        owner.dependency_service.install_async.assert_not_called()

    def test_consent_waits_for_a_task_started_inside_the_modal_dialog(self):
        owner,page=self.create();factory=self.factory(True)
        owner.dependency_service.install_async=Mock(return_value=True)
        def answer():owner.task_controller.begin('signal_management','read');return 1
        factory.return_value.exec.side_effect=answer
        prompt=LoginDependencyPrompt(page);self.addCleanup(prompt.dispose);self.settle()
        owner.dependency_service.install_async.assert_not_called()
        owner.task_controller.finish();owner.activityChanged.emit();self.settle()
        owner.dependency_service.install_async.assert_called_once()
        factory.assert_called_once()

    def test_combined_scope_uses_common_tools_and_respects_disabled_capture_features(self):
        owner,page=self.create();service=owner.dependency_service
        ready=rows(False);service.manager.install_all=Mock(return_value=ready)
        self.assertTrue(service.install_async(dict(scan=True,handshake=True,wps=True,password=True,pmkid=False),scope='login_all'))
        operation,event=owner.pending;result=operation(event,Mock())
        self.assertFalse(result['error'],result['error'])
        call=service.manager.install_all.call_args
        self.assertTrue(call.kwargs['include_sftp'])
        self.assertFalse(call.args[0]['pmkid'])
        self.assertTrue(all(call.args[0][k] for k in REQUIRED_COMMON_ITEMS if k!='sftp'))
        service._complete(service.task.session_id,result)
        self.assertIsNone(service.task);self.assertFalse(owner.task_controller.busy)
        self.assertEqual({r.key for r in owner.openwrt_snapshot.dependencies},{r.key for r in ready})

    def test_incomplete_install_result_is_a_failure_and_clears_task(self):
        owner,page=self.create();service=owner.dependency_service
        service.manager.install_all=Mock(return_value=())
        service.install_async({},scope='login_all')
        operation,event=owner.pending;result=operation(event,Mock())
        self.assertTrue(result['error'])
        service._complete(service.task.session_id,result)
        self.assertIsNone(service.task);self.assertFalse(owner.task_controller.busy)
        self.assertTrue(any(r.state==DependencyState.UNKNOWN for r in owner.openwrt_snapshot.dependencies))

    def test_login_scope_cannot_uninstall_or_accept_foreign_selection(self):
        owner,page=self.create();service=owner.dependency_service
        with self.assertRaises(ValueError):service.uninstall_async({},scope='login_all')
        with self.assertRaises(ValueError):service.install_async({},scope='login_all',items=('scan',))
        self.assertFalse(owner.task_controller.busy)

    def test_cancelled_install_rechecks_once_and_releases_task_without_reinstalling(self):
        owner,page=self.create();service=owner.dependency_service
        service.manager.install_all=Mock(side_effect=InterruptedError('cancelled'))
        service.manager.check=Mock(return_value=rows())
        service.manager._probe_sftp=Mock(return_value=False)
        service.install_async(dict(scan=True,handshake=True,wps=True,password=True,pmkid=False),scope='login_all')
        operation,event=owner.pending;result=operation(event,Mock())
        self.assertIn('cancelled',result['error'])
        service.manager.install_all.assert_called_once();service.manager.check.assert_called_once()
        service._complete(service.task.session_id,result)
        self.assertFalse(service.active);self.assertFalse(owner.task_controller.busy)
        self.assertEqual(next(r for r in owner.openwrt_snapshot.dependencies if r.key=='scan').state,DependencyState.MISSING)

    def test_progress_cancel_targets_its_own_installation_and_finish_releases_dialog(self):
        from modules.capture_services.dependency_service import DependencyTask
        owner,page=self.create();factory=self.factory(False)
        service=owner.dependency_service;service.cancel=Mock()
        prompt=LoginDependencyPrompt(page);self.addCleanup(prompt.dispose);self.settle()
        with patch('modules.main_pages.packet_capture.openwrt.login_dependency_prompt.TaskProgressDialog') as factory:
            prompt._task_changed(DependencyTask('session','login_all',('sftp','scan'),'install','installing'))
            callback=factory.return_value.cancelRequested.connect.call_args.args[0]
            callback();service.cancel.assert_called_once_with('session')
            prompt._task_finished('different',False,'failure')
            self.assertIsNotNone(prompt.progress)
            prompt._task_finished('session',False,'cancelled')
            factory.return_value.finish.assert_called_once_with(False,'cancelled')
            self.assertIsNone(prompt.progress)


class GreenDependencyButtonsTests(unittest.TestCase):
    def test_both_install_buttons_render_green_in_both_themes(self):
        from PySide6.QtWidgets import QFrame,QVBoxLayout
        from PySide6.QtGui import QPalette
        from tests.test_dependency_group_controls import ViewOwner
        from modules.main_pages.packet_capture.openwrt.dependency_section import DependencySection
        from modules.main_pages.packet_capture.openwrt.common_tool_section import CommonToolSection
        from modules.main_pages.packet_capture.capture_page_styles import CapturePageStyleManager
        from modules.ui_scale_manager import UiScaleManager
        from modules.config_manager import ConfigManager
        from router_app.appearance import build_main_stylesheet
        from modules.common_widgets.common_dialogs import ConfirmDialog
        out=Path('build/review/login-dependencies');out.mkdir(parents=True,exist_ok=True)
        for light in (False,True):
            parent=QFrame();parent.setObjectName('packetCapturePage')
            parent.light_theme=light;parent.scale_manager=UiScaleManager(100)
            parent.setStyleSheet(build_main_stylesheet(ConfigManager(),'light.qss' if light else 'dark.qss',parent.scale_manager)
                                +CapturePageStyleManager(parent)._capture_stylesheet(light))
            owner=ViewOwner();layout=QVBoxLayout(parent)
            capture=DependencySection(parent,{},Mock(),owner.dependency_service)
            common=CommonToolSection(parent,owner)
            self.addCleanup(parent.deleteLater);self.addCleanup(common.dispose);self.addCleanup(capture.dispose)
            layout.addWidget(capture.card);layout.addWidget(common.card)
            for section in (capture,common):section.card.set_expanded(True,animate=False)
            capture.set_connected(True);capture.set_busy(False)
            parent.resize(850,900);parent.show();APP.processEvents()
            for name,button in (('capture',capture.install_button),('common',common.install_all_button)):
                self.assertTrue(button.property('dependencyInstallPrimary'))
                self.assertEqual(button.palette().color(QPalette.ButtonText).name(),'#116d37' if light else '#8ce5ae')
                self.assertTrue(button.grab().save(str(out/(('light' if light else 'dark')+'-'+name+'.png'))))
            parent.hide()
