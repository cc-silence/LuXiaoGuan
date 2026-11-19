"""Timeout ownership, cancellation and per-target batch regression."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
from threading import Event
from unittest.mock import Mock, patch

from modules.capture_services.openwrt.command_runner import RemoteCommandResult as Result
from modules.capture_services.openwrt.deauth_command import run_deauth, DeauthTargetTimeout
from modules.capture_services.openwrt.models import CaptureCleanupError, AccessPointInfo
from modules.capture_services.openwrt.handshake_capture import OpenWrtHandshakeCapture, HandshakeCaptureResult
from modules.capture_services.backends.openwrt_backend import OpenWrtCaptureBackend, HandshakeTargetResult


class DeauthTimeoutTests(unittest.TestCase):
    def runner(self, result, owner='321 456'):
        runner = Mock()
        def run(name, *args, **kwargs):
            if name == 'deauth_client': return result
            return Result(name, 0, owner if name == 'read_deauth_owner' else '')
        runner.run.side_effect = run
        return runner

    def test_success_preserves_exit_and_output_without_stopping(self):
        original = Result('deauth_client', 0, 'Sending 64 directed DeAuth')
        runner = self.runner(original); processes = Mock()
        self.assertIs(run_deauth(runner, processes, 'aireplay-ng -0 15', 15, None), original)
        self.assertEqual(runner.run.call_args_list[0].kwargs['timeout'], 55)
        processes.stop.assert_not_called()

    def test_foreground_shell_preserves_real_exit_status_and_output(self):
        import os, shutil, subprocess
        bash = str(Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Git/bin/bash.exe') if os.name == 'nt' else shutil.which('bash')
        if not bash:
            self.skipTest('POSIX shell unavailable')
        def run(name, script, **kwargs):
            checked = subprocess.run([bash, '-n'], input=script, text=True, capture_output=True, timeout=5)
            self.assertEqual(checked.returncode, 0, checked.stderr)
            done = subprocess.run([bash, '-c', script], text=True, capture_output=True, timeout=5)
            return Result(name, done.returncode, done.stdout, done.stderr)
        runner = NS(run=run); processes = Mock()
        result = run_deauth(runner, processes, "sh -c 'printf sample-output; exit 7'", 15, None)
        self.assertEqual(result.exit_code, 7)
        self.assertEqual(result.stdout, 'sample-output')
        processes.stop.assert_not_called()

    def test_timeout_is_recoverable_only_after_owned_stop(self):
        runner = self.runner(Result('deauth_client', -1, timed_out=True)); processes = Mock()
        processes.stop.return_value = True
        with self.assertRaises(DeauthTargetTimeout):
            run_deauth(runner, processes, 'aireplay-ng -0 15', 15, None)
        handle = processes.register.call_args.args[0]
        self.assertEqual((handle.pid, handle.start_token), (321, '456'))
        processes.stop.assert_called_once_with(handle)
        self.assertEqual(runner.run.call_args.args[0], 'release_deauth_owner')

    def test_cancel_also_stops_owned_sender_without_cancel_event(self):
        event = Event(); event.set()
        runner = self.runner(Result('deauth_client', -1, cancelled=True)); processes = Mock()
        processes.stop.return_value = True
        with self.assertRaises(InterruptedError):
            run_deauth(runner, processes, 'aireplay-ng -0 15', 15, event)
        processes.stop.assert_called_once()
        self.assertNotIn('cancel_event', runner.run.call_args_list[1].kwargs)

    def test_missing_or_invalid_identity_cannot_continue(self):
        for owner in ('', '1 42', '0 42', '321 invalid', '321'):
            with self.subTest(owner=owner):
                runner = self.runner(Result('deauth_client', -1, timed_out=True), owner)
                with self.assertRaises(CaptureCleanupError):
                    run_deauth(runner, Mock(), 'aireplay-ng -0 15', 15, None)
                self.assertNotIn('release_deauth_owner', [c.args[0] for c in runner.run.call_args_list])

    def test_stop_failure_is_fatal_and_retains_identity(self):
        runner = self.runner(Result('deauth_client', -1, timed_out=True)); processes = Mock()
        processes.stop.side_effect = RuntimeError('still alive')
        with self.assertRaises(CaptureCleanupError):
            run_deauth(runner, processes, 'aireplay-ng -0 15', 15, None)
        self.assertEqual(runner.run.call_args.args[0], 'read_deauth_owner')

    def test_group_continues_other_target_and_does_not_repeat_failed_one(self):
        import struct
        with TemporaryDirectory() as folder:
            workspace = NS(local_dir=Path(folder), remote_dir='/tmp/owned')
            runner = Mock(); runner.run.return_value = Result('offline', 0)
            processes = Mock(); processes.is_running.return_value = True
            capture = OpenWrtHandshakeCapture(runner, processes)
            capture._verify_tuned_channel = Mock()
            capture._refresh_live_clients = Mock(return_value=['02:11:22:33:44:77'])
            capture._deauth_clients = Mock(side_effect=[DeauthTargetTimeout(), DeauthTargetTimeout()])
            manager = Mock()
            data = bytes.fromhex('d4c3b2a1') + struct.pack('<HHiiii', 2, 4, 0, 0, 65535, 105)
            manager.download_atomic.side_effect = lambda remote, local, event: Path(local).write_bytes(data)
            targets = tuple(AccessPointInfo(bssid, band='2.4G', channel=6) for bssid in
                ('02:11:22:33:44:55', '02:11:22:33:44:66'))
            clock = iter(range(0, 300, 21))
            with patch('modules.capture_services.openwrt.handshake_capture.time.monotonic', side_effect=lambda: next(clock)), \
                 patch('modules.capture_services.openwrt.handshake_capture.time.sleep'):
                result = capture.capture_passive(workspace, 'wbmon1', 'session', targets[0],
                    180, manager, active_deauth=True, defer_analysis=True, group_targets=targets)
            calls = capture._deauth_clients.call_args_list
            self.assertEqual([c.args[0] for c in calls], [t.bssid for t in targets])
            self.assertEqual(set(result.deauth_timeout_targets), {t.bssid for t in targets})
            self.assertTrue(all(r.deauth_timed_out for r in result.analyze()))
            processes.stop.assert_called_once()

    def test_batch_moves_on_then_retries_failed_target_next_round(self):
        targets = tuple(AccessPointInfo(bssid, band='2.4G', channel=channel) for bssid, channel in
            (('02:11:22:33:44:55', 6), ('02:11:22:33:44:66', 11)))
        backend = NS(ssh_client=NS(connected=True), _ensure_remote_tmp=Mock(), reconnect_cleanup=Mock(),
            workspace_manager=Mock(), _finish_capture_task=Mock())
        calls = []
        def capture(*args, **kwargs):
            target, round_no = args[4], args[7]; calls.append((target.bssid, round_no))
            if target == targets[0] and round_no == 1:
                return HandshakeTargetResult(target, HandshakeCaptureResult('', target.bssid, '',
                    verdict='none', deauth_timed_out=True), 'timeout', 'deauth_timeout'), True
            return HandshakeTargetResult(target, HandshakeCaptureResult('', target.bssid, '', verdict='complete')), False
        backend._capture_single_handshake = capture
        with TemporaryDirectory() as folder:
            result = OpenWrtCaptureBackend.capture_handshakes_passive(backend, 'offline',
                {'resolved_output_dir':folder, 'handshake_rounds':2}, targets, Event())
        self.assertEqual(calls, [(targets[0].bssid, 1), (targets[1].bssid, 1), (targets[0].bssid, 2)])
        self.assertTrue(all(r.result.verdict == 'complete' for r in result.results))
        self.assertFalse(result.note)

    def test_timeout_result_is_not_misreported_as_no_packets(self):
        target = AccessPointInfo('02:11:22:33:44:55', channel=6)
        result = HandshakeCaptureResult('', target.bssid, '', verdict='none', deauth_timed_out=True)
        entry, retry = OpenWrtCaptureBackend._save_handshake_entry(NS(), result, {}, Path('.'), target, [], Mock())
        self.assertEqual(entry.error_code, 'deauth_timeout')
        self.assertTrue(retry)


if __name__ == '__main__': unittest.main()
