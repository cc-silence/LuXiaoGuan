"""Login reconciliation runs in the existing background management queue."""
import unittest
from unittest.mock import patch
from PySide6.QtTest import QSignalSpy
from tests import test_router_policy_worker as worker_tests
from modules.capture_services.openwrt.command_runner import RemoteCommandResult


class RecoveryWorkerTests(unittest.TestCase):
    fixture = worker_tests.PolicyWorkerTests.fixture

    def test_pending_backup_triggers_login_verification_without_wifi_receipt(self):
        owner, service, ssh, runner = self.fixture()
        runner.run.return_value = RemoteCommandResult('router_policy_status', 0, 'on')
        done = QSignalSpy(service.completed)
        notices = QSignalSpy(service.initialVerificationCompleted)
        cleanup = dict(verified=True, note='Historical recovery copy verified and removed')
        try:
            with patch('modules.capture_services.openwrt.initial_reconnect_store.ReconnectStore.read', return_value=None), \
                 patch('modules.capture_services.openwrt.policy_backup_recovery.needs_reconcile', return_value=True), \
                 patch('modules.capture_services.openwrt.initial_reconnect.verify_pending', return_value=None), \
                 patch('modules.capture_services.openwrt.policy_backup_recovery.reconcile', return_value=cleanup) as verify:
                owner.openwrtSnapshotChanged.emit(None)
                worker_tests.wait_for(done, 2)
                self.assertEqual(done.count(), 2)
                verify.assert_called_once()
                self.assertEqual(notices.count(), 1)
                self.assertEqual(notices.at(0)[0], cleanup)
        finally:
            owner.deleteLater()

    def test_unresolved_backup_is_reported_without_confirming_initialization(self):
        owner, service, ssh, runner = self.fixture()
        done = QSignalSpy(service.completed)
        notices = QSignalSpy(service.initialVerificationCompleted)
        retained = dict(verified=False, note='Recovery copy retained for verification')
        try:
            with patch('modules.capture_services.openwrt.initial_reconnect.verify_pending', return_value=None), \
                 patch('modules.capture_services.openwrt.policy_backup_recovery.reconcile', return_value=retained):
                service.submit('initial_verify', kind='initial')
                worker_tests.wait_for(done, 1)
                self.assertEqual(done.count(), 1)
                self.assertEqual(notices.at(0)[0], retained)
        finally:
            owner.deleteLater()

    def test_cleanup_error_cannot_change_verified_initialization_to_failure(self):
        owner, service, ssh, runner = self.fixture()
        done = QSignalSpy(service.completed)
        notices = QSignalSpy(service.initialVerificationCompleted)
        failed = QSignalSpy(service.failed)
        initial = dict(verified=True, note='Initialization verified')
        try:
            with patch('modules.capture_services.openwrt.initial_reconnect.verify_pending', return_value=initial), \
                 patch('modules.capture_services.openwrt.policy_backup_recovery.reconcile', side_effect=ValueError('Invalid recovery metadata')):
                service.submit('initial_verify', kind='initial')
                worker_tests.wait_for(done, 1)
                self.assertEqual(done.count(), 1)
                self.assertEqual(failed.count(), 0)
                self.assertTrue(notices.at(0)[0]['verified'])
                self.assertIn('Initialization verified', notices.at(0)[0]['note'])
                self.assertIn('ValueError', notices.at(0)[0]['note'])
        finally:
            owner.deleteLater()

    def test_bad_cleanup_receipt_does_not_break_normal_login_policy_status(self):
        owner, service, ssh, runner = self.fixture()
        runner.run.return_value = RemoteCommandResult('router_policy_status', 0, 'on')
        done = QSignalSpy(service.completed)
        notices = QSignalSpy(service.initialVerificationCompleted)
        failures = QSignalSpy(service.failed)
        try:
            with patch('modules.capture_services.openwrt.initial_reconnect_store.ReconnectStore.read', return_value=None), \
                 patch('modules.capture_services.openwrt.policy_backup_recovery.PolicyRecoveryStore.read', side_effect=ValueError('Damaged recovery receipt')):
                owner.openwrtSnapshotChanged.emit(None)
                worker_tests.wait_for(done, 2)
                self.assertEqual(done.count(), 2)
                self.assertEqual(failures.count(), 0)
                self.assertEqual(service.policy_status, dict(boot_state=True))
                self.assertEqual(notices.count(), 1)
                self.assertFalse(notices.at(0)[0]['verified'])
                self.assertIn('ValueError', notices.at(0)[0]['note'])
                self.assertEqual(runner.run.call_count, 1)
        finally:
            owner.deleteLater()
