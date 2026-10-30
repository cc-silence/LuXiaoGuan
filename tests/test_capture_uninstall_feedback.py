"""Capture uninstall selection and visible task feedback."""
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock,patch
import unittest
from PySide6.QtWidgets import QWidget,QVBoxLayout
from tests.test_dependency_group_controls import APP,info
import tests.test_dependency_group_controls as group_fixtures
from modules.capture_services.dependency_service import DependencyTask
from modules.main_pages.packet_capture.openwrt.dependency_actions import uninstall_capture
from modules.main_pages.packet_capture.openwrt.dependency_section import DependencySection
from tests.test_hcx_upgrade import Service


class CaptureUninstallTests(unittest.TestCase):
    def test_single_hcx_selection_submits_only_hcx(self):
        owner, service = group_fixtures.GroupTaskTests().service((info('pmkid'),))
        page = NS(capture_service=NS(dependency_service=service,openwrt_snapshot=owner.openwrt_backend.snapshot),
                  openwrt_tab=NS(connection_settings=lambda:{'enabled_features':{'pmkid':True}}))
        with patch('modules.main_pages.packet_capture.openwrt.dependency_actions.PackageSelectDialog') as dialog:
            dialog.return_value.exec.return_value = dialog.Accepted
            dialog.return_value.selected_packages.return_value = ('hcxdumptool',)
            uninstall_capture(page,'pmkid')
            offered = [name for name,_ in dialog.call_args.args[2]]
        self.assertIn('hcxdumptool',offered)
        self.assertNotIn('iw',offered)
        self.assertNotIn('ip-tiny',offered)
        self.assertEqual(service.task.action,'uninstall')
        self.assertEqual(service.task.items,('pmkid',))
        owner.task_controller.finish()
        owner.deleteLater()

    def test_invalid_request_reports_error_in_dialog(self):
        owner, service = group_fixtures.GroupTaskTests().service((info('pmkid'),))
        service.uninstall_async = Mock(side_effect=ValueError('rejected selection'))
        page = NS(capture_service=NS(dependency_service=service,openwrt_snapshot=owner.openwrt_backend.snapshot),
                  openwrt_tab=NS(connection_settings=lambda:{'enabled_features':{'pmkid':True}}))
        with patch('modules.main_pages.packet_capture.openwrt.dependency_actions.PackageSelectDialog') as selection, \
             patch('modules.main_pages.packet_capture.openwrt.dependency_actions.MessageDialog') as message:
            selection.return_value.exec.return_value=selection.Accepted
            selection.return_value.selected_packages.return_value=('hcxdumptool',)
            uninstall_capture(page,'pmkid')
        self.assertEqual(message.call_args.args[1],'rejected selection')
        owner.deleteLater()

    def test_uninstall_progress_failure_and_disposal(self):
        page = QWidget();layout=QVBoxLayout(page);service=Service()
        section=DependencySection(page,{},Mock(),dependency_service=service)
        layout.addWidget(section.card);page.show();APP.processEvents()
        task=DependencyTask('remove-hcx','capture',('pmkid',),'uninstall','Checking installed packages')
        service.taskChanged.emit(task)
        progress=section.task_progress.dialog
        self.assertIsNotNone(progress)
        self.assertTrue(progress.isVisible())
        service.taskChanged.emit(replace(task,message='Removing hcxdumptool'))
        self.assertEqual(progress.message.text(),'Removing hcxdumptool')
        progress.request_cancel()
        service.cancel.assert_called_once_with('remove-hcx')
        service.taskFinished.emit('other',False,'wrong task')
        self.assertTrue(progress.running)
        service.taskFinished.emit('remove-hcx',True,'removed')
        self.assertFalse(progress.running)
        self.assertIsNone(section.task_progress.dialog)
        section.dispose()
        service.taskChanged.emit(task)
        self.assertIsNone(section.task_progress.dialog)
        page.deleteLater()

    def test_hidden_capture_section_does_not_duplicate_other_page_progress(self):
        page=QWidget();service=Service()
        section=DependencySection(page,{},Mock(),dependency_service=service)
        service.taskChanged.emit(DependencyTask('hidden','capture',('pmkid',),'uninstall','Removing'))
        self.assertIsNone(section.task_progress.dialog)
        section.dispose();page.deleteLater()
