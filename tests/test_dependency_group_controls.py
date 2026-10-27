"""Group boundaries and new controls share the existing install/remove pipeline."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from dataclasses import replace
from types import SimpleNamespace as NS
from threading import Event
import unittest
from unittest.mock import Mock, patch
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout
from modules.capture_services.dependency_service import OpenWrtDependencyService, DependencyTask
from modules.capture_services.openwrt.models import DependencyInfo, DependencyState, OpenWrtSessionSnapshot, OpenWrtConnectionState
from modules.capture_services.openwrt.dependency_groups import CAPTURE_KEYS, CAPTURE_PACKAGES, COMMON_ITEMS, missing_packages
from modules.main_pages.packet_capture.openwrt.dependency_actions import install_capture, operate_capture
from modules.main_pages.packet_capture.openwrt.common_dependency_actions import install_common
from tests.test_dependency_diagnostic_paths import Owner
from tests import test_safe_tool_uninstall as uninstall_fixtures
from tests.test_feedback_ui import BundleService

APP=QApplication.instance() or QApplication([])


def info(key, state="ready", packages=()):
    return DependencyInfo(key, key, DependencyState(state), tuple(packages))


class GroupTaskTests(unittest.TestCase):
    def service(self, rows=()):
        owner=Owner()
        owner.openwrt_backend.snapshot=replace(owner.openwrt_backend.snapshot,dependencies=tuple(rows))
        service=OpenWrtDependencyService(owner)
        return owner, service

    def test_bulk_common_uses_existing_manager_and_includes_only_common_items(self):
        rows=tuple(info(key) for key in COMMON_ITEMS)
        owner,service=self.service(rows)
        service.manager.install_all=Mock(return_value=rows)
        self.assertTrue(service.install_async({"scan":True},scope="common_all"))
        operation,event=owner.pending
        result=operation(event,Mock())
        self.assertFalse(result["error"])
        self.assertEqual(set(service.task.items),set(COMMON_ITEMS))
        call=service.manager.install_all.call_args
        self.assertEqual(set(call.args[0]),set(COMMON_ITEMS)-{"sftp"})
        self.assertTrue(call.kwargs["include_sftp"])
        self.assertFalse(set(call.args[0]) & CAPTURE_KEYS)
        service._complete(service.task.session_id,result)
        self.assertIsNone(service.task)

    def test_single_capture_installs_selection_and_refreshes_other_capture_states(self):
        old=(info("wpa_cli","missing",("wpa-cli",)),info("scan","missing",("aircrack-ng",)))
        owner,service=self.service(old)
        service.manager.install_all=Mock(return_value=(info("scan"),))
        refreshed=(info("scan"),info("handshake"),info("wps","missing",("reaver",)))
        service.manager.check=Mock(return_value=refreshed)
        features={"scan":True,"handshake":True,"wps":True,"wpa_cli":True}
        self.assertTrue(service.install_async(features,scope="capture",items=("scan",)))
        operation,event=owner.pending
        result=operation(event,Mock())
        self.assertFalse(result["error"])
        self.assertEqual(service.manager.install_all.call_args.args[0],{"scan":True})
        self.assertFalse(service.manager.install_all.call_args.kwargs["include_sftp"])
        self.assertEqual(service.manager.check.call_args.args[0],{"scan":True,"handshake":True,"wps":True})
        service._complete(service.task.session_id,result)
        self.assertEqual(next(item for item in owner.openwrt_backend.snapshot.dependencies if item.key=="wpa_cli").state,DependencyState.MISSING)
        self.assertEqual(next(item for item in owner.openwrt_backend.snapshot.dependencies if item.key=="handshake").state,DependencyState.READY)

    def test_capture_scope_rejects_foreign_or_unselected_packages(self):
        for package in ("wpa-cli","tmux","iw","reaver"):
            owner,service=self.service()
            with self.subTest(package=package),self.assertRaises(ValueError):
                service.uninstall_async({"scan":True},[package],scope="capture",items=("scan",))
            self.assertFalse(owner.task_controller.busy)

    def test_single_readonly_check_does_not_disable_other_capture_rows(self):
        owner,service=self.service((info("scan"),info("handshake"),info("password")))
        service.manager.check=Mock(return_value=(info("scan","disabled"),info("handshake","disabled"),info("password")))
        service.check_capture_async({"scan":True,"handshake":True,"password":True},"password")
        operation,event=owner.pending
        result=operation(event,Mock())
        self.assertFalse(result["error"])
        service.manager.check.assert_called_once()
        service._complete(service.task.session_id,result)
        self.assertEqual(next(row for row in owner.openwrt_backend.snapshot.dependencies if row.key=="scan").state,DependencyState.READY)

    def test_cancelled_bulk_preserves_error_and_reprobes_without_install_retry(self):
        from modules.capture_services.openwrt.signal_manager import SignalError
        owner,service=self.service()
        service.manager.install_all=Mock(side_effect=SignalError("cancelled","cancelled installation"))
        service.manager._probe_sftp=Mock(return_value=False)
        service.manager.check=Mock(return_value=tuple(info(key,"missing") for key in COMMON_ITEMS))
        service.install_async({},scope="common_all")
        operation,event=owner.pending
        result=operation(event,Mock())
        self.assertIn("cancelled installation",result["error"])
        service.manager.install_all.assert_called_once()
        service._complete(service.task.session_id,result)
        self.assertFalse(owner.task_controller.busy)
        self.assertTrue(all(row.state==DependencyState.MISSING for row in owner.openwrt_backend.snapshot.dependencies))

    def test_common_bulk_rejects_missing_verification_results(self):
        owner,service=self.service()
        service.manager.install_all=Mock(return_value=(info("sftp"),))
        service.install_async({},scope="common_all")
        operation,event=owner.pending
        self.assertTrue(operation(event,Mock())["error"])
        service._complete(service.task.session_id,{"error":"missing result","checked":()})
        self.assertFalse(owner.task_controller.busy)

    def test_both_native_managers_remove_single_capture_package_by_actual_inventory(self):
        fixture=uninstall_fixtures.UninstallTests()
        for pm in ("opkg","apk"):
            manager=fixture.manager("scan",pm)
            manager.check.return_value=(info("scan","missing",("aircrack-ng",)),)
            fixture.inventory(manager,("aircrack-ng",),pm)
            manager.uninstall_packages({"scan":True},False,Event(),Mock(),["aircrack-ng"],include_sftp=False)
            commands=[call.args[1] for call in manager._execute_stage.call_args_list]
            self.assertEqual(len(commands),2)
            self.assertTrue(all("aircrack-ng" in command for command in commands))
            self.assertTrue(all("--force" not in command and "--autoremove" not in command for command in commands))

    def test_single_uninstall_accepts_provider_command_after_exact_package_removal(self):
        owner,service=self.service()
        service.manager.uninstall_packages=Mock(return_value=(info("scan"),))
        service.manager.check=Mock(return_value=(info("scan"),info("handshake")))
        service.uninstall_async({"scan":True,"handshake":True},["aircrack-ng"],scope="capture",items=("scan",))
        operation,event=owner.pending
        self.assertFalse(operation(event,Mock())["error"])


class ConfirmationTests(unittest.TestCase):
    def page(self, rows):
        service=Mock(task=None);service.ensure_available.return_value=True
        snapshot=OpenWrtSessionSnapshot(connection_state=OpenWrtConnectionState.CONNECTED,dependencies=tuple(rows))
        page=NS(capture_service=NS(dependency_service=service,openwrt_snapshot=snapshot),
                openwrt_tab=NS(connection_settings=lambda:{"enabled_features":{"scan":True,"handshake":False}}),
                settings_info_panel=Mock())
        return page,service

    def test_capture_preview_does_not_include_common_or_disabled_feature_packages(self):
        page,service=self.page((info("scan","missing",("aircrack-ng",)),
            info("handshake","missing",("foreign-disabled",)),info("wpa_cli","missing",("wpa-cli",))))
        target="modules.main_pages.packet_capture.openwrt.dependency_actions.ConfirmDialog"
        with patch(target) as dialog:
            dialog.return_value.exec.return_value=dialog.Accepted
            install_capture(page)
            text=dialog.call_args.args[1]
            self.assertIn("aircrack-ng",text)
            self.assertNotIn("wpa-cli",text)
            self.assertNotIn("foreign-disabled",text)
        service.install_async.assert_called_once_with({"scan":True,"handshake":False},scope="capture",items=None)

    def test_common_missing_does_not_trigger_capture_install(self):
        page,service=self.page((info("scan"),info("wpa_cli","missing",("wpa-cli",))))
        with patch("modules.main_pages.packet_capture.openwrt.dependency_actions.ConfirmDialog") as dialog:
            install_capture(page)
            dialog.assert_not_called()
        service.install_async.assert_not_called()

    def test_system_row_only_checks_and_never_uninstalls(self):
        page,service=self.page((info("password"),))
        operate_capture(page,"password")
        service.check_capture_async.assert_called_once()
        service.install_async.assert_not_called()
        service.uninstall_async.assert_not_called()

    def test_common_bulk_confirmation_uses_same_service(self):
        page,service=self.page((info("wpa_cli","missing",("wpa-cli",)),))
        section=NS(service=service,owner=NS(openwrt_snapshot=page.capture_service.openwrt_snapshot),card=None)
        with patch("modules.main_pages.packet_capture.openwrt.common_dependency_actions.ConfirmDialog") as dialog:
            dialog.return_value.exec.return_value=dialog.Accepted
            install_common(section)
            self.assertIn("wpa-cli",dialog.call_args.args[1])
        service.install_async.assert_called_once_with({},scope="common_all")

    def test_cancelled_confirmation_starts_no_task(self):
        page,service=self.page((info("scan","missing",("aircrack-ng",)),))
        with patch("modules.main_pages.packet_capture.openwrt.dependency_actions.ConfirmDialog") as dialog:
            dialog.return_value.exec.return_value=-1
            install_capture(page,"scan")
        service.install_async.assert_not_called()


class ViewService(QObject):
    taskChanged=Signal(object)
    taskFinished=Signal(str,bool,str)
    requestRejected=Signal(str)
    def __init__(self):
        super().__init__()
        self.task=None
        self.cancel=Mock()
        self.ensure_available=Mock(return_value=True)
        self.install_async=Mock()


class ViewOwner(QObject):
    openwrtSnapshotChanged=Signal(object)
    activityChanged=Signal()
    def __init__(self):
        super().__init__()
        self.is_busy=False
        self.dependency_service=ViewService()
        self.common_bundle_download=BundleService()
        self.capture_bundle_download=BundleService()
        rows=tuple(info(key) for key in (*COMMON_ITEMS,*CAPTURE_KEYS))
        self.openwrt_snapshot=OpenWrtSessionSnapshot(connection_state=OpenWrtConnectionState.CONNECTED,dependencies=rows)


class ControlTests(unittest.TestCase):
    def test_capture_rows_show_original_names_and_busy_scope(self):
        from modules.main_pages.packet_capture.openwrt.dependency_section import DependencySection
        parent=QWidget();owner=ViewOwner();actions=[]
        section=DependencySection(parent,{"enabled_features":dict.fromkeys(CAPTURE_KEYS,True)},actions.append,owner.dependency_service)
        section.set_connected(True);section.apply_snapshot(owner.openwrt_snapshot)
        for key,packages in CAPTURE_PACKAGES.items():
            for package in packages:self.assertIn(package,section.checks[key].text())
        section.tool_buttons["scan"].click()
        self.assertEqual(actions,["capture_dependency:scan"])
        self.assertEqual(section.tool_buttons["password"].text(),"检查")
        owner.dependency_service.task=DependencyTask("a","capture",("scan",),"install","running")
        section.set_busy(True)
        self.assertTrue(section.tool_buttons["scan"].isEnabled())
        self.assertEqual(section.tool_buttons["scan"].text(),"停止")
        self.assertFalse(section.tool_buttons["handshake"].isEnabled())
        section.dispose();parent.deleteLater()

    def test_bulk_uninstall_button_removed_without_removing_single_controls(self):
        from modules.main_pages.packet_capture.openwrt.dependency_section import DependencySection
        parent=QWidget();owner=ViewOwner()
        section=DependencySection(parent,{},Mock(),owner.dependency_service)
        self.assertFalse(hasattr(section,"uninstall_button"))
        self.assertIn("scan",section.tool_buttons)
        self.assertIn("handshake",section.tool_buttons)
        section.set_connected(True)
        section.set_busy(False)
        section.apply_snapshot(owner.openwrt_snapshot)
        section.dispose();parent.deleteLater()

    def test_stale_stop_click_never_starts_a_new_capture_action(self):
        from modules.main_pages.packet_capture.openwrt.dependency_section import DependencySection
        parent=QWidget();owner=ViewOwner();actions=[]
        section=DependencySection(parent,{"enabled_features":{"scan":True}},actions.append,owner.dependency_service)
        section.set_connected(True)
        owner.dependency_service.task=DependencyTask("old","capture",("scan",),"install","running")
        section.set_busy(True)
        owner.dependency_service.task=None
        section.tool_buttons["scan"].click()
        owner.dependency_service.cancel.assert_called_once_with("old")
        self.assertEqual(actions,[])
        section.dispose();parent.deleteLater()

    def test_common_bulk_button_cancels_only_its_own_session(self):
        from modules.main_pages.packet_capture.openwrt.common_tool_section import CommonToolSection
        parent=QWidget();owner=ViewOwner()
        section=CommonToolSection(parent,owner)
        self.assertEqual(section.install_all_button.text(),"检查并安装全部常用依赖")
        owner.dependency_service.task=DependencyTask("bulk","common_all",COMMON_ITEMS,"install","running")
        owner.is_busy=True
        section.apply_snapshot(owner.openwrt_snapshot)
        self.assertTrue(section.install_all_button.isEnabled())
        self.assertEqual(section.install_all_button.text(),"停止安装")
        self.assertFalse(section.rows["wpa_cli"][1].isEnabled())
        section.install_all_button.click()
        owner.dependency_service.cancel.assert_called_once_with("bulk")
        section.dispose();parent.deleteLater()


if __name__=="__main__":
    unittest.main()
