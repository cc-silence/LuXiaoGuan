"""Real persistence/export, privacy and termination coverage for WPS diagnostics."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
import zipfile

from modules.log_history import LogHistory, install_history
from modules.log_services import export_logs
from modules.capture_services.openwrt.models import AccessPointInfo, CaptureCleanupError
from modules.capture_services.openwrt.wps_capture import OpenWrtWpsCapture
from modules.capture_services.openwrt.wps_diagnostics import WpsDiagnosticTrace, CATEGORY, MAX_EVENTS
from tests import test_wps_lifecycle as lifecycle
from tests import test_wps_quick_and_radio as quick


class WpsDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.stores = []
        self.store = self.open_store()

    def open_store(self):
        store = LogHistory(self.root / 'history')
        self.stores.append(store)
        install_history(store)
        return store

    def tearDown(self):
        install_history(None)
        for store in self.stores:
            store.close()
        self.tmp.cleanup()

    def rows(self):
        snapshot = self.store.snapshot(CATEGORY)
        return [json.loads(line) for run in snapshot['runs'] for row in run['records']
                if row[2] == 'wps-diagnostics' for line in row[0].splitlines()]

    def trace(self):
        return WpsDiagnosticTrace('session', 3, 10,
            AccessPointInfo('02:11:22:33:44:55', ssid='PrivateNetwork', channel=36, band='5G'),
            'normal', {'attack_seconds': 60, 'spoof_mac_enabled': True})

    def test_tail_overlap_and_sensitive_native_text_never_reach_disk_or_export(self):
        trace = self.trace()
        first = 'Reaver v1.6.6\nAssociated with 02:11:22:33:44:55 (PrivateNetwork)\nSending EAPOL START request'
        trace.feed(first)
        trace.feed(first)
        trace.feed('Sending EAPOL START request\nReceived identity request\nReceived M1 message\nWPS transaction failed (code: 0x02)\nWPS PIN: 87654321\nWPA PSK: TopSecretValue\nexecuting pixiewps -a PrivateAuthKey')
        trace.finish()
        rows = self.rows()
        self.assertEqual(sum(r['event'] == 'associated' for r in rows), 1)
        self.assertEqual(sum(r['event'] == 'eapol_start_sent' for r in rows), 1)
        self.assertIn(2, [r.get('native_code') for r in rows])
        self.assertTrue(all(r['target_number'] == 4 for r in rows))
        self.assertIn('m1_received', [r['event'] for r in rows])
        output = self.root / 'export.zip'
        export_logs(output, CATEGORY, [])
        with zipfile.ZipFile(output) as archive:
            text = '\n'.join(archive.read(name).decode('utf-8') for name in archive.namelist())
            self.assertIn('current-run/wps-diagnostics.txt', archive.namelist())
        for secret in ['87654321', 'TopSecretValue', 'PrivateAuthKey', 'PrivateNetwork', '02:11:22:33:44:55']:
            self.assertNotIn(secret, text)
        self.store.flush()
        for file in (self.root / 'history').glob('*'):
            if file.is_file():
                raw = file.read_bytes()
                for secret in ['87654321', 'TopSecretValue', 'PrivateAuthKey', 'PrivateNetwork']:
                    self.assertNotIn(secret.encode(), raw)

    def test_limit_keeps_final_sample_and_finish_and_empty_is_explicit(self):
        sink = Mock(); sink.append.return_value = True
        with patch('modules.capture_services.openwrt.wps_diagnostics.current_history', return_value=sink):
            trace = self.trace()
            trace.feed(''); trace.feed('')
            for index in range(MAX_EVENTS + 20):
                trace.feed(f'Received M1 message {index}')
            trace.feed('Received M7 message', final=True)
            trace.finish(cancelled=True)
        rows = [json.loads(line) for call in sink.append.call_args_list for line in call.args[1].splitlines()]
        self.assertEqual(sum(r['event'] == 'timeline_limit' for r in rows), 1)
        self.assertEqual(sum(r['event'] == 'log_empty_or_unavailable' for r in rows), 1)
        self.assertIn('m7_received', [r['event'] for r in rows])
        self.assertEqual(rows[-1]['outcome'], 'cancelled')
        self.assertLessEqual(len(trace.previous), 100)
        self.assertLessEqual(len(rows), MAX_EVENTS + 6)

    def test_normal_timeout_cancel_and_cleanup_failure_keep_records(self):
        helper = lifecycle.WpsLifecycleTests()
        for kwargs in [dict(timed=True), dict(cancel=Event()), dict(stop_error=True)]:
            try:
                helper.run_target(self.root, **kwargs)
            except RuntimeError:
                pass
        rows = self.rows()
        reasons = [r.get('reason') for r in rows if r['event'] == 'process_stopped']
        self.assertIn('time_budget', reasons)
        self.assertIn('cancelled', reasons)
        self.assertIn('error', [r.get('outcome') for r in rows])
        self.assertIn('cleanup', [r.get('error_kind') for r in rows])

    def test_quick_success_cancel_and_final_output_are_saved(self):
        helper = quick.DefaultPinTests()
        helper.run_quick(self.root, "WPS PIN: '40414089'\nWPA PSK: 'AuditOnlySecret'")
        event = Event()
        def tail(*args):
            event.set()
            return 'Sending EAPOL START request'
        with self.assertRaises(RuntimeError):
            helper.run_quick(self.root, tail, event=event)
        rows = self.rows()
        self.assertTrue(any(r.get('credential_confirmed') for r in rows))
        self.assertIn('cancelled', [r.get('outcome') for r in rows])
        self.assertTrue(any(r.get('final_sample') for r in rows))

    def test_history_survives_restart_and_is_in_previous_run_export(self):
        trace = self.trace(); trace.feed('Received M3 message'); trace.finish()
        self.store.close()
        self.store = self.open_store()
        output = self.root / 'restart.zip'
        export_logs(output, CATEGORY, [])
        with zipfile.ZipFile(output) as archive:
            self.assertIn('previous-run/wps-diagnostics.txt', archive.namelist())
            self.assertIn('m3_received', archive.read('previous-run/wps-diagnostics.txt').decode())

    def test_failed_history_does_not_change_task_result(self):
        sink = Mock(); sink.append.side_effect = OSError('disk full')
        with patch('modules.capture_services.openwrt.wps_diagnostics.current_history', return_value=sink), patch('modules.capture_services.openwrt.wps_diagnostics.LOGGER') as logger:
            trace = self.trace(); trace.feed('Received M1 message'); trace.finish()
            logger.warning.assert_called_once()

    def test_backend_skip_and_prepare_failure_are_not_silent(self):
        from modules.capture_services.backends.openwrt_backend import OpenWrtCaptureBackend
        backend = object.__new__(OpenWrtCaptureBackend)
        backend.ssh_client = NS(connected=True)
        backend.reconnect_cleanup = Mock(); backend.workspace_manager = Mock()
        backend.wps_capture = OpenWrtWpsCapture(Mock(), Mock())
        backend._find_scan_radio = Mock(side_effect=RuntimeError('controlled prepare failure'))
        backend._restore_task_wireless = Mock(return_value=[])
        backend._finish_capture_task = Mock()
        targets = (AccessPointInfo('02:11:22:33:44:55', channel=1),
                   AccessPointInfo('02:11:22:33:44:66', channel=1, wps_supported=True))
        with patch('modules.capture_services.backends.openwrt_backend.CaptureWorkspaceFactory.create', return_value=NS(local_dir=self.root, remote_dir='/tmp/owned')):
            backend.run_wps('batch', {'resolved_output_dir': str(self.root)}, targets, 'normal')
        rows = self.rows()
        self.assertIn('target_skipped', [r['event'] for r in rows])
        self.assertIn('preparation_failed', [r['event'] for r in rows])
        self.assertEqual({r['target_number'] for r in rows}, {1, 2})

    def test_last_output_after_stop_is_saved_and_start_failure_has_terminal_record(self):
        manager = Mock()
        stopped = [False]
        manager.is_running.return_value = False
        manager.stop.side_effect = lambda *a, **k: stopped.__setitem__(0, True)
        manager.read_log_tail.side_effect = lambda *a: 'Received M7 message' if stopped[0] else 'Sending EAPOL START request'
        capture = OpenWrtWpsCapture(Mock(), manager)
        target = AccessPointInfo('02:11:22:33:44:55', channel=1, wps_supported=True)
        workspace = NS(local_dir=self.root, remote_dir='/tmp/owned')
        with patch.object(capture, '_base_args', return_value='reaver'), patch.object(capture, '_download_wpc', return_value=''):
            capture.run_targets(workspace, 'ownmon', 'owned', (target,), 'offline', {'attack_seconds': 10}, self.root, Mock())
            manager.start.side_effect = RuntimeError('controlled start failure')
            with self.assertRaises(RuntimeError):
                capture.run_targets(workspace, 'ownmon', 'owned', (target,), 'normal', {'attack_seconds': 10}, self.root, Mock())
        rows = self.rows()
        self.assertTrue(any(r['event'] == 'm7_received' and r.get('final_sample') for r in rows))
        self.assertEqual(rows[-1]['outcome'], 'error')
