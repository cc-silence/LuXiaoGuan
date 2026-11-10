"""Authenticated login survives local record faults; mutation guards stay closed."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from modules.capture_services.backends.openwrt_backend import OpenWrtCaptureBackend
from modules.capture_services.openwrt.models import OpenWrtDeviceInfo, DependencyInfo, DependencyState
from modules.capture_services.openwrt.operation_diagnostics import OperationDiagnostics, sanitized_operation_evidence
from modules.capture_services.openwrt.password_recovery_admission import (
    RecoveryRecordsUnavailable, prepare_login, read_records, require_ready, WARNING)
from modules.capture_services.openwrt.password_recovery_store import RecoveryStore
from modules.capture_services.openwrt.reconnect_cleanup import ReconnectCleanup
from tests.test_ssh_empty_password import KEY, router
from modules.log_history import HistoryMasker


class RecoveryAdmissionTests(unittest.TestCase):
    def backend(self, client, store):
        backend = OpenWrtCaptureBackend.__new__(OpenWrtCaptureBackend)
        backend.ssh_client = client
        backend.password_recovery_store = store
        backend.command_runner = NS(radio_diagnostics=Mock(), operation_diagnostics=OperationDiagnostics())
        backend.reconnect_cleanup = ReconnectCleanup(backend)
        backend.device_probe = Mock()
        backend.device_probe.probe.return_value = OpenWrtDeviceInfo()
        backend.dependency_manager = Mock()
        backend.dependency_manager.check_sftp.return_value = DependencyInfo('sftp','SFTP',DependencyState.READY)
        backend.dependency_manager.check.return_value = ()
        backend.dependency_manager.check_terminal.return_value = DependencyInfo('terminal','Terminal',DependencyState.READY)
        backend.process_manager = Mock()
        backend.process_manager.reap_stale_processes.return_value = 0
        backend._finished_sessions = set()
        backend._recycle_remote_residuals = Mock(return_value='')
        backend._recover_crashed_takeovers = Mock(return_value='')
        return backend

    def connect(self, backend, client, cancel=None):
        return backend.connect(dict(host=client._host,port=client._port,user='root'), 'valid',
                               client.fingerprint_for_key(KEY), cancel_event=cancel or Event())

    def test_real_ssh_authentication_survives_local_permission_failure_without_cleanup(self):
        store = Mock()
        store.records.side_effect = PermissionError(13, 'private-path-secret')
        with router('normal') as (client, server):
            backend = self.backend(client, store)
            with patch('modules.capture_services.openwrt.tool_catalog.check_tools', return_value=()), patch('modules.capture_services.openwrt.monitor_residuals.login_monitor_cleanup') as monitor, patch.object(backend.reconnect_cleanup,'recover') as recover:
                result = self.connect(backend, client)
            self.assertEqual(result.status, 'connected')
            self.assertTrue(client.connected)
            self.assertIn(WARNING, result.detail)
            self.assertEqual(server.attempts, [('password','root','valid')])
            monitor.assert_not_called()
            recover.assert_not_called()
            backend.process_manager.reap_stale_processes.assert_not_called()
            backend._recycle_remote_residuals.assert_not_called()
            backend._recover_crashed_takeovers.assert_not_called()
            backend.device_probe.probe.assert_called_once()
            with self.assertRaises(RuntimeError):
                backend.reconnect_cleanup.require_ready()
            backend._check_remote_tmp = Mock()
            with self.assertRaises(RuntimeError):
                backend._ensure_remote_tmp()
            backend._check_remote_tmp.assert_not_called()
            evidence = sanitized_operation_evidence(backend.command_runner.operation_diagnostics.snapshot(), HistoryMasker())
            rows = [r for r in evidence['events'] if r.get('operation')=='password_recovery_check']
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['reason'], 'recovery_record_io')
            self.assertEqual(rows[0]['errno'], 13)
            self.assertNotIn('private-path-secret', json.dumps(evidence))
            self.assertNotIn('private-path-secret', result.detail)

    def test_invalid_record_is_preserved_and_does_not_disconnect_authenticated_session(self):
        with TemporaryDirectory() as folder, router('normal') as (client, server):
            store = RecoveryStore(Path(folder)/'records')
            store.root.mkdir()
            record = store._path(client.fingerprint_for_key(KEY), 'a'*32)
            record.write_bytes(b'{invalid-json-private-secret')
            backend = self.backend(client, store)
            with patch('modules.capture_services.openwrt.tool_catalog.check_tools', return_value=()):
                result = self.connect(backend, client)
            self.assertEqual(result.status,'connected')
            self.assertTrue(client.connected)
            self.assertEqual(record.read_bytes(), b'{invalid-json-private-secret')
            self.assertNotIn('private-secret',result.detail)

    def test_real_restore_failure_remains_fatal_and_closes_transport(self):
        with router('normal') as (client, server):
            backend = self.backend(client, Mock())
            with patch('modules.capture_services.openwrt.password_recovery.recover_pending', side_effect=RuntimeError('restore not verified')):
                with self.assertRaisesRegex(RuntimeError, 'restore not verified'):
                    self.connect(backend, client)
            self.assertFalse(client.connected)
            backend.device_probe.probe.assert_not_called()

    def test_changed_device_pending_cleanup_does_not_reject_real_login(self):
        from modules.capture_services.openwrt.reconnect_cleanup import PendingCleanup
        with router('normal') as (client, server):
            store = Mock(); store.records.return_value = []
            backend = self.backend(client, store)
            pending = PendingCleanup(Mock(), None, 'different-device')
            backend.reconnect_cleanup.pending = pending
            with patch('modules.capture_services.openwrt.tool_catalog.check_tools', return_value=()), patch('modules.capture_services.openwrt.monitor_residuals.login_monitor_cleanup') as monitor:
                result = self.connect(backend, client)
            self.assertEqual(result.status, 'connected')
            self.assertTrue(client.connected)
            self.assertIs(backend.reconnect_cleanup.pending, pending)
            backend.device_probe.probe.assert_called_once()
            monitor.assert_not_called()
            backend.process_manager.reap_stale_processes.assert_not_called()
            backend._recycle_remote_residuals.assert_not_called()
            backend._recover_crashed_takeovers.assert_not_called()

    def test_cleanup_failure_is_advisory_but_cancel_is_not(self):
        with router('normal') as (client, server):
            store = Mock(); store.records.return_value = []
            backend = self.backend(client, store)
            with patch.object(backend.reconnect_cleanup, 'recover', side_effect=RuntimeError('cleanup failed')), patch('modules.capture_services.openwrt.tool_catalog.check_tools', return_value=()):
                result = self.connect(backend, client)
            self.assertEqual(result.status, 'connected')
            self.assertTrue(client.connected)
            backend.process_manager.reap_stale_processes.assert_not_called()
            with patch.object(backend.reconnect_cleanup, 'recover', side_effect=InterruptedError('cancelled')):
                with self.assertRaises(InterruptedError):
                    backend.reconnect_cleanup.recover_for_login(client.host_fingerprint, Event())

    def test_normal_login_retains_existing_cleanup(self):
        with router('normal') as (client, server):
            store = Mock();store.records.return_value = []
            backend = self.backend(client,store)
            with patch('modules.capture_services.openwrt.tool_catalog.check_tools',return_value=()), patch('modules.capture_services.openwrt.monitor_residuals.login_monitor_cleanup',return_value='') as monitor:
                result=self.connect(backend,client)
            self.assertEqual(result.status,'connected')
            monitor.assert_called_once()
            backend.process_manager.reap_stale_processes.assert_called_once()
            backend._recycle_remote_residuals.assert_called_once()
            backend._recover_crashed_takeovers.assert_called_once()
            backend.reconnect_cleanup.require_ready()

    def test_block_is_bound_to_fingerprint_and_clears_only_after_successful_recheck(self):
        store=Mock();store.records.side_effect=ValueError('private-record-secret')
        client=NS(host_fingerprint='SHA256:'+'a'*43)
        backend=self.backend(client,store)
        _,warning=prepare_login(backend,client.host_fingerprint)
        self.assertEqual(warning,WARNING)
        for action in ('policy_apply','common_save','upload','initial_verify'):
            with self.subTest(action=action),self.assertRaises(RuntimeError):
                require_ready(backend,action,{})
        for action in ('common_read','policy_read','download','local_list'):
            require_ready(backend,action,{})
        require_ready(backend,'file_batch',{'jobs':[{'operation':'download'}]})
        with self.assertRaises(RuntimeError):
            require_ready(backend,'file_batch',{'jobs':[{'operation':'upload'}]})
        client.host_fingerprint='SHA256:'+'b'*43
        backend.reconnect_cleanup.require_password_check_ready()
        client.host_fingerprint='SHA256:'+'a'*43
        store.records.side_effect=None;store.records.return_value=[]
        with self.assertRaises(RuntimeError):
            require_ready(backend)
        self.assertEqual(prepare_login(backend,client.host_fingerprint),(0,''))
        require_ready(backend)

    def test_dependency_writes_are_rejected_but_readonly_checks_remain_available(self):
        from modules.capture_services.dependency_service import OpenWrtDependencyService
        client=NS(host_fingerprint='SHA256:'+'a'*43,connected=True)
        backend=self.backend(client,Mock())
        backend.snapshot=NS(connection_state=NS(value='connected'))
        backend.reconnect_cleanup.set_password_check_issue(client.host_fingerprint,{'reason':'recovery_record_io'})
        service=NS(owner=NS(_shutting_down=False,task_controller=NS(busy=False),is_busy=False,
                            openwrt_backend=backend),_active=False,_reject=Mock(return_value=False))
        self.assertFalse(OpenWrtDependencyService.ensure_available(service,'install','common'))
        self.assertFalse(OpenWrtDependencyService.ensure_available(service,'uninstall','common'))
        self.assertTrue(OpenWrtDependencyService.ensure_available(service,'check','common'))
        self.assertTrue(OpenWrtDependencyService.ensure_available(service))
        self.assertEqual(service._reject.call_count,2)

    def test_cancel_is_not_downgraded_to_warning(self):
        client=NS(host_fingerprint='SHA256:'+'a'*43)
        store=Mock();store.records.side_effect=InterruptedError('cancelled')
        backend=self.backend(client,store)
        with self.assertRaises(InterruptedError):
            prepare_login(backend,client.host_fingerprint)
        cancel=Event();cancel.set()
        with self.assertRaises(InterruptedError):
            prepare_login(backend,client.host_fingerprint,cancel)
        self.assertEqual(store.records.call_count,1)


if __name__ == '__main__':
    unittest.main()
