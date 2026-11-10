"""Capture logging evidence without router operations."""
from collections import deque
import hashlib
import json
from pathlib import Path
import tempfile
from threading import Event
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace as NS

from modules.capture_services.openwrt.command_runner import OpenWrtCommandRunner, RemoteCommandResult
from modules.capture_services.openwrt.operation_diagnostics import OperationDiagnostics, sanitized_operation_evidence
from modules.capture_services.openwrt.capture_live_logs import CaptureLogManager, with_capture_logs
from modules.log_history import HistoryMasker, LogHistory
from modules.log_history_export import history_attachments


class Channel:
    def __init__(self, chunks=(), eof=False):
        self.chunks = deque(chunks)
        self.eof, self.closed = eof, False
        self.sent = []
        self.consumed = Event()
    def recv_ready(self): return bool(self.chunks)
    def recv(self, size):
        result = self.chunks.popleft()
        if not self.chunks: self.consumed.set()
        return result
    def exit_status_ready(self): return self.eof and not self.chunks
    def send(self, text): self.sent.append(text)
    def close(self): self.closed = True


def runner(channel=None, failure=None):
    ssh = NS(_client=object(), connected=True)
    ssh.open_diagnostic_stream = Mock(side_effect=failure, return_value=channel or Channel())
    value = OpenWrtCommandRunner(ssh)
    value.operation_diagnostics.bind_connection('host-key-a')
    return value


class CaptureLiveEvidenceTests(unittest.TestCase):
    def test_tool_failure_keeps_masked_output_and_same_result(self):
        r = runner()
        result = RemoteCommandResult('deauth_client', 1, 'Waiting for beacon 192.168.1.1', 'No such BSSID AA:BB:CC:DD:EE:FF\npassword=hidden-secret')
        with patch.object(r, '_run', return_value=result):
            actual = r.run('deauth_client', 'unchanged-command', timeout=15)
        self.assertIs(actual, result)
        row = r.operation_diagnostics.snapshot()['capture_evidence']['tool_failures'][0]
        self.assertIn('Waiting for beacon', row['stdout'])
        self.assertIn('No such BSSID', row['stderr'])
        self.assertNotIn('hidden-secret', json.dumps(row))
        self.assertNotIn('192.168.1.1', json.dumps(row))
        self.assertNotIn('AA:BB:CC:DD:EE:FF', json.dumps(row))
        self.assertNotIn('unchanged-command', json.dumps(row))

    def test_timeout_keeps_partial_output_and_success_keeps_no_tool_failure(self):
        r = runner()
        for result in (RemoteCommandResult('deauth_client', 0, 'ok'), RemoteCommandResult('deauth_client', -1, 'partial diagnostic', timed_out=True)):
            with patch.object(r, '_run', return_value=result): r.run('deauth_client', 'command', 15)
        rows = r.operation_diagnostics.snapshot()['capture_evidence']['tool_failures']
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]['timed_out'])
        self.assertEqual(rows[0]['stdout'], 'partial diagnostic')

    def test_two_radios_share_one_channel_and_last_release_stops_it(self):
        channel = Channel([b'kern.err kernel: firmware crash\n'])
        r = runner(channel)
        first = r.capture_logs.acquire()
        second = r.capture_logs.acquire()
        self.assertTrue(channel.consumed.wait(2))
        self.assertEqual(r.ssh_client.open_diagnostic_stream.call_count, 1)
        first.close()
        self.assertFalse(channel.closed)
        second.close()
        self.assertTrue(channel.closed)
        self.assertEqual(channel.sent, ['\x03'])
        self.assertTrue(second.run.done.is_set())
        self.assertIsNone(r.capture_logs.run)
        self.assertIn('firmware crash', r.operation_diagnostics.snapshot()['capture_evidence']['system_lines'][0]['text'])

    def test_split_utf8_and_last_fragment_survive_eof(self):
        raw = 'kern.err kernel: panic \u9519\u8bef'.encode('utf-8')
        channel = Channel([raw[:-2], raw[-2:]], eof=True)
        r = runner(channel)
        lease = r.capture_logs.acquire()
        self.assertTrue(lease.run.done.wait(2))
        lease.close()
        self.assertIn('\u9519\u8bef', r.operation_diagnostics.snapshot()['capture_evidence']['system_lines'][0]['text'])

    def test_missing_logread_and_unsupported_pty_are_explicit(self):
        for channel, failure, expected in ((Channel([b'__RM_LOG_UNAVAILABLE__\n'], eof=True), None, 'unsupported'), (None, RuntimeError('denied'), 'failed')):
            r = runner(channel, failure)
            lease = r.capture_logs.acquire()
            self.assertTrue(lease.run.done.wait(2))
            lease.close()
            self.assertTrue(r.ssh_client.connected)
            states = r.operation_diagnostics.snapshot()['capture_evidence']['stream_states']
            self.assertIn(expected, [row['state'] for row in states])

    def test_switch_device_rejects_old_stream_and_bounds_memory(self):
        r = runner()
        journal = r.operation_diagnostics
        old = journal.epoch
        journal.bind_connection('host-key-b')
        journal.capture_evidence('system_lines', {'text': 'kern.err old-device'}, old)
        self.assertFalse(journal.snapshot()['capture_evidence']['system_lines'])
        for index in range(200):
            journal.capture_evidence('system_lines', {'text': 'kern.err '+str(index)}, journal.epoch)
        self.assertEqual(len(journal.snapshot()['capture_evidence']['system_lines']), 128)
        clean = sanitized_operation_evidence(journal.snapshot(), HistoryMasker())
        self.assertEqual(len(clean['capture_evidence']['system_lines']), 128)
        self.assertIn('199', clean['capture_evidence']['system_lines'][-1]['text'])

    def test_history_survives_reopen_and_exports_both_capture_categories(self):
        r = runner()
        device = hashlib.sha256(b'host-key-a').hexdigest()
        with tempfile.TemporaryDirectory() as folder:
            store = LogHistory(folder, 'test')
            try:
                with patch('modules.log_history.current_history', return_value=store):
                    r.operation_diagnostics.capture_evidence('system_lines', {'text': 'kern.err kernel: panic-before-reboot'}, r.operation_diagnostics.epoch)
                store.flush()
            finally: store.close()
            store = LogHistory(folder, 'test-next-run')
            try:
                for category in ('auto-capture', '\u6293\u5305'):
                    exported = history_attachments(store, category, device, HistoryMasker())
                    self.assertTrue(any('panic-before-reboot' in data.decode('utf-8') for name, data in exported if name.endswith('.txt')))
            finally: store.close()

    def test_original_capture_exception_survives_diagnostic_start_failure(self):
        r = runner()
        owner = NS(command_runner=r)
        @with_capture_logs
        def operation(self): raise ValueError('original capture error')
        with patch.object(r.capture_logs, 'acquire', side_effect=RuntimeError('diagnostic error')):
            with self.assertRaisesRegex(ValueError, 'original capture error'): operation(owner)

    def test_already_cancelled_start_does_not_open_a_channel(self):
        r = runner()
        cancel = Event()
        cancel.set()
        self.assertIsNone(r.capture_logs.acquire(cancel))
        r.ssh_client.open_diagnostic_stream.assert_not_called()

    def test_last_panic_fragment_is_saved_on_transport_exception(self):
        channel = Channel([b'kern.err kernel: panic-last-fragment', b'raise'])
        receive = channel.recv
        def recv(size):
            value = receive(size)
            if value == b'raise': raise ConnectionResetError('disconnected')
            return value
        channel.recv = recv
        r = runner(channel)
        lease = r.capture_logs.acquire()
        self.assertTrue(lease.run.done.wait(2))
        lease.close()
        rows = r.operation_diagnostics.snapshot()['capture_evidence']['system_lines']
        self.assertIn('panic-last-fragment', rows[0]['text'])

    def test_missing_logread_shell_returns_explicit_marker(self):
        import subprocess
        from tests.posix_shell import local_shell
        from modules.capture_services.openwrt.capture_live_logs import COMMAND
        shell = local_shell()
        if shell is None: self.skipTest('Git Bash unavailable')
        check = subprocess.run([str(shell), '-n'], input=COMMAND, text=True, capture_output=True, timeout=5)
        self.assertEqual(check.returncode, 0, check.stderr)
        result = subprocess.run([str(shell)], input='PATH=/nonexistent\n'+COMMAND, text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 127)
        self.assertEqual(result.stdout, '__RM_LOG_UNAVAILABLE__\n')

    def test_optional_channel_timeout_does_not_invalidate_login(self):
        from modules.capture_services.openwrt.ssh_client import OpenWrtSshClient
        client = OpenWrtSshClient()
        connection = Mock()
        connection.get_transport().is_active.return_value = True
        client._client = connection
        connection.get_transport().open_session.side_effect = TimeoutError('busy')
        with self.assertRaises(TimeoutError):
            client.open_diagnostic_stream(connection, 'exec logread -f -l 40', Event(), timeout=.1)
        self.assertIs(client._client, connection)
        self.assertTrue(client.connected)


if __name__ == '__main__': unittest.main()
