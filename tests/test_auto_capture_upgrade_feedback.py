"""Upgrade rejection feedback uses the resident dependency service reason."""
from types import SimpleNamespace as NS
from PySide6.QtWidgets import QWidget
import unittest
from unittest.mock import Mock, patch
from tests.test_feedback_regressions import APP, Owner
from modules.capture_services.dependency_service import OpenWrtDependencyService
from modules.main_pages.packet_capture.openwrt.auto_capture_page import AutoCapturePage


def make_page(service, messages):
    page = QWidget()
    page.service = NS(current_device=None, dependency_service=service, messageGenerated=messages)
    return page


class UpgradeFeedbackTests(unittest.TestCase):
    def test_rejected_upgrade_reports_actual_service_reason(self):
        for mode in ('busy', 'shutdown', 'disconnected', 'identity'):
            with self.subTest(mode=mode):
                owner = Owner()
                service = OpenWrtDependencyService(owner)
                if mode == 'busy':
                    owner.task_controller.begin('wireless_inspect', 'inspect')
                elif mode == 'shutdown':
                    owner._shutting_down = True
                elif mode == 'disconnected':
                    owner.ssh.connected = False
                else:
                    owner.ssh.host_fingerprint = ''
                messages = Mock()
                page = make_page(service, messages)
                self.addCleanup(page.deleteLater)
                with patch('modules.main_pages.packet_capture.openwrt.auto_capture_page.MessageDialog') as dialog:
                    AutoCapturePage._on_upgrade_hcx_clicked(page)
                    self.assertTrue(service.last_rejection_reason)
                    self.assertEqual(dialog.call_args.args[1], service.last_rejection_reason)
                self.assertEqual(owner.workers, [])
                messages.emit.assert_not_called()
                owner.task_controller.finish()
                owner.deleteLater()

    def test_accepted_upgrade_starts_only_selected_dependency_once(self):
        service = Mock(last_rejection_reason='')
        service.upgrade_hcx_async.return_value = True
        messages = Mock()
        page = make_page(service, messages)
        self.addCleanup(page.deleteLater)
        with patch('modules.main_pages.packet_capture.openwrt.auto_capture_page.MessageDialog') as dialog:
            AutoCapturePage._on_upgrade_hcx_clicked(page)
        dialog.assert_not_called()
        service.ensure_available.assert_not_called()
        service.upgrade_hcx_async.assert_called_once_with()
        service.install_async.assert_not_called()
        messages.emit.assert_called_once()

    def test_accepted_check_clears_previous_rejection(self):
        owner = Owner()
        service = OpenWrtDependencyService(owner)
        owner.ssh.connected = False
        self.assertFalse(service.ensure_available())
        self.assertTrue(service.last_rejection_reason)
        owner.ssh.connected = True
        self.assertTrue(service.ensure_available())
        self.assertEqual(service.last_rejection_reason, '')
        owner.deleteLater()

    def test_start_failure_does_not_claim_upgrade_started(self):
        service = Mock(last_rejection_reason='')
        service.upgrade_hcx_async.return_value = False
        messages = Mock()
        page = make_page(service, messages)
        self.addCleanup(page.deleteLater)
        with patch('modules.main_pages.packet_capture.openwrt.auto_capture_page.MessageDialog') as dialog:
            AutoCapturePage._on_upgrade_hcx_clicked(page)
        self.assertTrue(dialog.call_args.args[1])
        messages.emit.assert_not_called()
