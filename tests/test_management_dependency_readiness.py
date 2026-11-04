"""Shared page readiness, unified installation and connection-safe completion."""
from types import SimpleNamespace as NS
from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import Mock,patch
from tests.test_standalone import APP
from tests.test_feedback_regressions import Owner
from modules.capture_services.router_signal_service import RouterSignalService
from modules.capture_services.dependency_service import OpenWrtDependencyService,DependencyTask
from modules.capture_services.management_readiness import READY_NOTICE

GOOD=dict(lua=True,uci=True,ubus=True,jsonc=True,root=True,sha256sum=True,opkg=True,apk=False,overlay_kb=1024,lua_bin='lua')


class ManagementReadinessTests(unittest.TestCase):
    def create(self):
        owner=Owner();signals=RouterSignalService(owner);owner.router_signals=signals
        owner.dependency_service=OpenWrtDependencyService(owner)
        signals._auto_refresh_blocked=True
        signals.manager.probe_dependencies=Mock(return_value=dict(GOOD))
        self.addCleanup(owner.deleteLater)
        return owner,signals,signals.management_readiness

    def complete(self,owner):
        operation,event,success,failure=owner.workers.pop(0)
        try:result=operation(event)
        except Exception as exc:failure(str(exc),'')
        else:success(result)
        APP.processEvents()

    def test_page_request_is_deferred_and_missing_modules_are_reported(self):
        owner,signals,state=self.create()
        signals.manager.probe_dependencies.return_value=dict(GOOD,uci=False)
        self.assertTrue(state.request())
        signals.manager.probe_dependencies.assert_not_called()
        APP.processEvents();self.assertTrue(state.checking)
        self.complete(owner)
        self.assertEqual(state.missing,('libuci-lua',));self.assertFalse(state.ready)
        self.assertTrue(signals.dependencies_missing)
        self.assertFalse(any(t==READY_NOTICE for t,l in signals.messages))
        self.assertFalse(owner.is_busy)

    def test_verified_readiness_is_announced_once_and_reappears_after_new_install(self):
        owner,signals,state=self.create()
        state.request();APP.processEvents();self.complete(owner)
        state.request();APP.processEvents();self.complete(owner)
        self.assertEqual(signals.messages.count((READY_NOTICE,'success')),1)
        task=DependencyTask('new','login_all',('signal',),'install','install')
        owner.dependency_service.taskChanged.emit(task)
        owner.dependency_service.taskChanged.emit(None)
        owner.dependency_service.taskFinished.emit('new',True,'done')
        APP.processEvents();self.complete(owner)
        self.assertEqual(signals.messages.count((READY_NOTICE,'success')),2)

    def test_partial_or_malformed_probe_does_not_claim_readiness(self):
        owner,signals,state=self.create()
        for value in (dict(GOOD,jsonc=False),dict(GOOD,sha256sum=False),dict(GOOD,root=False),{'lua':True}):
            state.accept(value)
            self.assertFalse(state.ready)
        self.assertFalse(any(t==READY_NOTICE for t,l in signals.messages))

    def test_failed_check_does_not_announce_success_or_loop(self):
        owner,signals,state=self.create()
        signals.manager.probe_dependencies.side_effect=RuntimeError('offline')
        state.request();APP.processEvents();self.complete(owner)
        self.assertEqual(state.error,'offline');self.assertFalse(state.ready)
        self.assertEqual(owner.workers,[]);self.assertFalse(owner.is_busy)

    def test_cancel_and_replaced_connections_discard_old_ready_result(self):
        for mode in ('cancel','replacement','disconnect'):
            owner,signals,state=self.create()
            state.request();APP.processEvents()
            operation,event,success,failure=owner.workers.pop(0)
            result=operation(event)
            if mode=='cancel':event.set()
            elif mode=='replacement':owner.ssh._client=object()
            else:owner.ssh.connected=False
            success(result);APP.processEvents()
            self.assertFalse(state.ready)
            self.assertFalse(any(t==READY_NOTICE for t,l in signals.messages))
            self.assertFalse(owner.is_busy)

    def test_requests_wait_for_shared_task_slot_and_coalesce(self):
        owner,signals,state=self.create()
        owner.task_controller.begin('other','foreign')
        state.request();state.request();APP.processEvents()
        self.assertEqual(owner.workers,[])
        owner.task_controller.finish();APP.processEvents()
        self.assertEqual(len(owner.workers),1)
        self.complete(owner)
        self.assertEqual(signals.manager.probe_dependencies.call_count,1)

    def test_independent_signal_button_uses_existing_dependency_service(self):
        owner,signals,state=self.create()
        owner.dependency_service.install_async=Mock(return_value=True)
        signals.manager.install_auto=Mock()
        self.assertTrue(signals.install_dependencies())
        owner.dependency_service.install_async.assert_called_once_with({},scope='signal')
        signals.manager.install_auto.assert_not_called()

    def test_unified_signal_install_retains_cancel_and_success_recheck(self):
        owner,signals,state=self.create()
        task=DependencyTask('selected','signal',('signal',),'install','install')
        owner.dependency_service.cancel=Mock(return_value=True)
        owner.dependency_service.taskChanged.emit(task)
        self.assertTrue(signals.snapshot.installing)
        self.assertTrue(signals.cancel())
        owner.dependency_service.cancel.assert_called_once_with('selected')
        owner.dependency_service.taskChanged.emit(None)
        self.assertFalse(signals.snapshot.installing)
        owner.dependency_service.taskFinished.emit('selected',True,'done')
        APP.processEvents();self.complete(owner)
        self.assertIn((READY_NOTICE,'success'),signals.messages)

    def test_common_banner_and_signal_ready_message_render_in_both_themes(self):
        from tests.common_settings_fixture import create_page
        from tests.test_signal_actions_ui import Service
        from modules.main_pages.packet_capture.openwrt.router_signal_page import RouterSignalPage
        output=Path('build/management-dependencies-ui');output.mkdir(parents=True,exist_ok=True)
        for light in (False,True):
            owner,signals,state=self.create()
            fake=Service();fake.management_readiness=state
            fake.install_dependencies=Mock(return_value=True)
            with patch('tests.common_settings_fixture.Service',return_value=fake):
                page,_=create_page(self,light=light,with_info=True)
            if owner.workers:self.complete(owner)
            state.accept(dict(GOOD,uci=False,jsonc=False));APP.processEvents()
            self.assertTrue(page.install_button.isVisible())
            self.assertTrue(page.install_button.isEnabled())
            self.assertFalse(page.lan_button.isEnabled());self.assertTrue(page.password_button.isEnabled())
            with patch('modules.main_pages.packet_capture.openwrt.management_dependency_prompt.ConfirmDialog') as dialog:
                dialog.Accepted=1;dialog.return_value.exec.return_value=1
                page.install_button.click()
                fake.install_dependencies.assert_called_once()
                dialog.return_value.deleteLater.assert_called_once()
            state.accept(dict(GOOD));APP.processEvents()
            self.assertFalse(page.install_button.isVisible());self.assertTrue(page.lan_button.isEnabled())
            target=NS(info_panel=Mock())
            RouterSignalPage._show_problem_message(target,READY_NOTICE,'success')
            target.info_panel.append_info.assert_called_once_with(READY_NOTICE,'success')
            RouterSignalPage._show_problem_message(NS(info_panel=page.info_panel),READY_NOTICE,'success')
            self.assertIn(READY_NOTICE,page.info_panel.info_box.toPlainText())
            state.accept(dict(GOOD,uci=False,jsonc=False));APP.processEvents()
            page.grab().save(str(output/('common-'+str(light)+'.png')))

    def test_dialog_connection_change_cannot_install_on_new_router(self):
        from modules.main_pages.packet_capture.openwrt.management_dependency_prompt import install_management_dependencies
        owner,signals,state=self.create();signals.install_dependencies=Mock()
        report=Mock()
        def accept():owner.ssh._client=object();return 1
        with patch('modules.main_pages.packet_capture.openwrt.management_dependency_prompt.ConfirmDialog') as dialog:
            dialog.Accepted=1;dialog.return_value.exec.side_effect=accept
            install_management_dependencies(None,signals,report)
        signals.install_dependencies.assert_not_called();report.assert_called_once()

    def test_unified_install_can_yield_background_signal_read(self):
        owner,signals,state=self.create()
        signals.defer_foreground=Mock(return_value=True)
        owner.dependency_service.install_async=Mock()
        self.assertTrue(signals.install_dependencies())
        owner.dependency_service.install_async.assert_not_called()
        signals.defer_foreground.assert_called_once()
