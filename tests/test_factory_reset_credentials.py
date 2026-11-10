"""Reset credential invalidation is target-bound and independent of page lifetime."""
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import unittest
from PySide6.QtCore import QObject, Signal
from PySide6.QtTest import QSignalSpy
from tests.test_standalone import APP
from modules.capture_services.factory_reset_credentials import FactoryResetCredentials
from modules.main_pages.packet_capture.openwrt.router_login_page import RouterLoginPage


class Service(QObject):
    completed = Signal(str, object)
    failed = Signal(str, str)
    passwordInvalidated = Signal(object)


class ResetCredentialTests(unittest.TestCase):
    def setUp(self):
        self.service = Service()
        self.addCleanup(self.service.deleteLater)
        self.settings = dict(host='192.168.11.1', port=2222, user='root')
        self.ssh = NS(_host=self.settings['host'], _port=2222, _user='root',
                      _client=object(), host_fingerprint='device', authenticated_settings=self.settings)
        self.service.manager = NS(ssh=self.ssh)
        self.owner = self.service.owner = NS(credentials=Mock(), set_openwrt_password=Mock(),
            _openwrt_credential_target=Mock(return_value='target-old-router'), settingsMessageGenerated=Mock())
        self.helper = FactoryResetCredentials(self.service)
        self.service.factory_credentials = self.helper
        self.spy = QSignalSpy(self.service.passwordInvalidated)

    def capture(self, operation='reset'):
        self.helper.capture('job', 'maintenance_apply', dict(review=dict(operation=operation)), self.ssh)

    def test_only_confirmed_reset_clears_original_credential_memory_and_ui(self):
        self.capture()
        self.ssh._client = None
        self.service.completed.emit('job', dict(operation='reset', outcome='reconnect'))
        self.owner.set_openwrt_password.assert_called_once_with('')
        self.assertEqual(self.spy.count(), 1)
        args = self.owner.credentials.submit.call_args.args
        self.assertEqual(args[:4], ('delete', 'target-old-router', 'root', ''))
        self.assertEqual(self.owner._openwrt_credential_target.call_args.args[0], self.settings)
        self.assertFalse(self.helper.targets)

    def test_failure_cancellation_uncertain_restore_and_reboot_keep_password(self):
        for operation, outcome in [('reset', 'uncertain'), ('reset', 'restored'), ('restore', 'reconnect'), ('reboot', 'reconnect')]:
            self.capture(operation)
            self.service.completed.emit('job', dict(operation=operation, outcome=outcome))
        self.capture()
        self.service.failed.emit('job', 'cancelled')
        self.owner.credentials.submit.assert_not_called()
        self.owner.set_openwrt_password.assert_not_called()
        self.assertFalse(self.helper.targets)

    def test_new_login_is_never_cleared_or_overwritten(self):
        self.capture()
        self.ssh._client = object()
        self.service.completed.emit('job', dict(operation='reset', outcome='reconnect'))
        self.owner.credentials.submit.assert_not_called()
        self.owner.set_openwrt_password.assert_not_called()
        self.assertEqual(self.spy.count(), 0)

    def test_other_host_login_keeps_memory_and_deletes_only_reset_target(self):
        self.capture()
        self.ssh._host = '192.168.99.1'
        self.ssh._client = object()
        self.service.completed.emit('job', dict(operation='reset', outcome='reconnect'))
        self.owner.set_openwrt_password.assert_not_called()
        self.assertEqual(self.owner._openwrt_credential_target.call_args.args[0], self.settings)
        self.assertEqual(self.spy.count(), 0)

    def test_page_rebuilt_while_deletion_pending_cannot_reload_old_password(self):
        self.capture()
        self.service.completed.emit('job', dict(operation='reset', outcome='reconnect'))
        page = NS(service=NS(router_management=NS(factory_credentials=self.helper)),
                  _credential_settings=lambda: self.settings)
        self.assertFalse(RouterLoginPage._load_saved_password(page))
        callback = self.owner.credentials.submit.call_args.args[-1]
        callback(True, None)
        self.assertFalse(self.helper.is_pending(self.settings))

    def test_keyring_failure_is_reported_without_claiming_deletion(self):
        self.capture()
        self.service.completed.emit('job', dict(operation='reset', outcome='reconnect'))
        callback = self.owner.credentials.submit.call_args.args[-1]
        callback(None, OSError('keyring unavailable'))
        self.owner.settingsMessageGenerated.emit.assert_called_once()
        self.assertEqual(self.owner.settingsMessageGenerated.emit.call_args.args[1], 'error')

    def test_credential_worker_start_failure_unblocks_future_reads_and_reports_error(self):
        self.owner.credentials.submit.side_effect = OSError('cannot start credential worker')
        self.capture()
        self.service.completed.emit('job', dict(operation='reset', outcome='reconnect'))
        self.assertFalse(self.helper.pending_deletions)
        self.assertFalse(self.helper.targets)
        self.owner.settingsMessageGenerated.emit.assert_called_once()
        self.assertEqual(self.owner.settingsMessageGenerated.emit.call_args.args[1], 'error')

    def test_new_remembered_password_cannot_be_written_ahead_of_pending_delete(self):
        from modules.capture_services.capture_service import CaptureService
        self.capture()
        self.service.completed.emit('job', dict(operation='reset', outcome='reconnect'))
        self.owner.router_management = self.service
        self.owner._openwrt_password = 'new password'
        settings = dict(self.settings, remember_password=True)
        with patch('modules.platform_services.credentials.write_generic_password') as write:
            with self.assertRaises(OSError):
                CaptureService.save_openwrt_password_preference(self.owner, settings)
            write.assert_not_called()
            callback = self.owner.credentials.submit.call_args.args[-1]
            callback(True, None)
            CaptureService.save_openwrt_password_preference(self.owner, settings)
            write.assert_called_once_with('target-old-router', 'root', 'new password')

    def test_pending_delete_uses_the_same_canonical_key_as_saved_credentials(self):
        from modules.capture_services.capture_service import CaptureService
        self.capture()
        self.service.completed.emit('job', dict(operation='reset', outcome='reconnect'))
        self.owner.router_management = self.service
        self.owner._openwrt_password = 'new password'
        variant = dict(self.settings, host=' 192.168.11.1 ', port='2222', user='ROOT', remember_password=True)
        self.assertTrue(self.helper.is_pending(variant))
        with patch('modules.platform_services.credentials.write_generic_password') as write:
            with self.assertRaises(OSError):
                CaptureService.save_openwrt_password_preference(self.owner, variant)
            write.assert_not_called()

    def test_login_ui_clears_only_matching_target_and_invalidates_pending_reads(self):
        page = NS(_credential_identity=lambda: ('192.168.11.1', 2222, 'root'),
                  _credential_ui=NS(intent=5), connection=NS(password=Mock()))
        RouterLoginPage._password_invalidated(page, dict(host='other', port=2222, user='root'))
        page.connection.password.clear.assert_not_called()
        RouterLoginPage._password_invalidated(page, self.settings)
        page.connection.password.clear.assert_called_once()
        self.assertEqual(page._credential_ui.intent, 6)

    def test_actual_management_queue_stages_and_invalidates_without_a_page(self):
        from tests.test_router_policy_worker import Owner, wait_for
        from modules.capture_services.router_management_service import RouterManagementService
        self.ssh.connected = True
        owner = Owner(self.ssh, NS())
        self.addCleanup(owner.deleteLater)
        owner.set_openwrt_password = Mock()
        owner.credentials = Mock()
        owner._openwrt_credential_target = Mock(return_value='queue-target')
        service = RouterManagementService(owner)
        done = QSignalSpy(service.completed)
        with patch('modules.capture_services.openwrt.router_backup.RouterBackupManager') as backup:
            backup.return_value.execute.return_value = dict(operation='reset', outcome='reconnect')
            service.submit('maintenance_apply', review=dict(operation='reset'), backup_path=None)
            wait_for(done, 1)
        owner.credentials.submit.assert_called_once()
        owner.set_openwrt_password.assert_called_once_with('')


if __name__ == '__main__':
    unittest.main()
