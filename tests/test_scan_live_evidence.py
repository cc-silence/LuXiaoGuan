"""Scan evidence must survive faults without changing capture or login behavior."""
import json
import subprocess
import tempfile
import unittest
from collections import deque
from pathlib import Path
from threading import Event
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from tests.test_capture_live_evidence import Channel, runner
from tests.posix_shell import local_shell
from modules.capture_services.openwrt.scan_evidence import collect_scan_context, COMMAND, BEGIN, END
from modules.capture_services.openwrt.capture_live_logs import CaptureLogRun
from modules.capture_services.openwrt.operation_diagnostics import sanitized_operation_evidence
from modules.log_history import HistoryMasker, LogHistory
from modules.log_history_export import history_attachments


def scene():
    channel = Channel([(BEGIN + '\nDRIVER mt7915e firmware 2024\n' + END + '\n').encode()], eof=True)
    return runner(channel), channel


class ScanEvidenceTests(unittest.TestCase):
    def test_context_preserves_parameters_and_masks_sensitive_lines(self):
        r, channel = scene()
        channel.chunks = deque([(BEGIN + '\npassword=private-value\naddr AA:BB:CC:DD:EE:FF\n192.168.1.1\n' + END + '\n').encode()])
        collect_scan_context(r, 'rmmon1', '5G', 60, 10)
        rows = sanitized_operation_evidence(r.operation_diagnostics.snapshot(), HistoryMasker())['capture_evidence']['scan_contexts']
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['duration_seconds'], rows[0]['refresh_seconds'], rows[0]['band']), (60, 10, '5G'))
        self.assertEqual(rows[0]['state'], 'ended')
        self.assertTrue(channel.closed)
        for value in ('private-value', 'AA:BB:CC:DD:EE:FF', '192.168.1.1'):
            self.assertNotIn(value, json.dumps(rows))

    def test_unavailable_channel_cancel_and_invalid_interface_do_not_break_login(self):
        r, channel = scene()
        r.ssh_client.open_diagnostic_stream.side_effect = TimeoutError('busy')
        collect_scan_context(r, 'rmmon1', '5G', 24, 10)
        self.assertTrue(r.ssh_client.connected)
        self.assertEqual(r.operation_diagnostics.snapshot()['capture_evidence']['scan_contexts'][-1]['state'], 'failed')
        r.ssh_client.open_diagnostic_stream.reset_mock()
        token = Event(); token.set()
        collect_scan_context(r, 'rmmon1', '5G', 60, 10, token)
        collect_scan_context(r, 'rmmon1; reboot', '5G', 60, 10)
        r.ssh_client.open_diagnostic_stream.assert_not_called()

    def test_partial_context_and_budget_exhaustion_are_explicit(self):
        r, channel = scene()
        channel.chunks = deque([b'partial firmware line'])
        channel.eof = True
        collect_scan_context(r, 'rmmon1', '5G', 60, 10)
        row = r.operation_diagnostics.snapshot()['capture_evidence']['scan_contexts'][-1]
        self.assertEqual(row['state'], 'failed')
        self.assertIn('partial firmware', row['stdout'])
        r, channel = scene()
        with patch('modules.capture_services.openwrt.scan_evidence.MAX_OUTPUT', 12):
            collect_scan_context(r, 'rmmon1', '5G', 60, 10)
        self.assertEqual(r.operation_diagnostics.snapshot()['capture_evidence']['scan_contexts'][-1]['state'], 'limit')
        self.assertTrue(channel.closed)
        self.assertLessEqual(len(r.operation_diagnostics.snapshot()['capture_evidence']['scan_contexts'][-1]['stdout'].encode()), 12)

    def test_context_from_replaced_connection_is_discarded(self):
        r, channel = scene()
        def replaced(*args, **kwargs):
            r.ssh_client._client = object()
            return channel
        r.ssh_client.open_diagnostic_stream.side_effect = replaced
        collect_scan_context(r, 'rmmon1', '5G', 60, 10)
        self.assertFalse(r.operation_diagnostics.snapshot()['capture_evidence']['scan_contexts'])
        self.assertTrue(channel.closed)

    def test_stream_fault_budget_and_same_connection_restart(self):
        channel = Channel([b'kern.err kernel: panic-tail\n', b'kern.err firmware reset\n'], eof=True)
        r = runner(channel)
        with patch('modules.capture_services.openwrt.capture_live_logs.MAX_STREAM_BYTES', 35):
            lease = r.capture_logs.acquire()
            self.assertTrue(lease.run.done.wait(2))
        states = r.operation_diagnostics.snapshot()['capture_evidence']['stream_states']
        self.assertIn('limit', [row['state'] for row in states])
        self.assertTrue(channel.closed)
        r.ssh_client.open_diagnostic_stream.return_value = Channel([b'kern.err next scan\n'], eof=True)
        next_lease = r.capture_logs.acquire()
        self.assertIsNot(lease.run, next_lease.run)
        self.assertTrue(next_lease.run.done.wait(2))
        lease.close(); next_lease.close()
        self.assertEqual(r.ssh_client.open_diagnostic_stream.call_count, 2)
        self.assertTrue(r.ssh_client.connected)

    def test_cancel_keeps_already_buffered_kernel_output(self):
        channel = Channel([b'kern.err panic-buffered-before-cancel\n'])
        r = runner(channel)
        run = CaptureLogRun(r.capture_logs, r.ssh_client._client)
        def opened(*args, **kwargs):
            run.stop.set()
            return channel
        r.ssh_client.open_diagnostic_stream.side_effect = opened
        run._work()
        rows = r.operation_diagnostics.snapshot()['capture_evidence']['system_lines']
        self.assertIn('panic-buffered-before-cancel', rows[0]['text'])
        self.assertTrue(channel.closed)

    def test_scan_entry_holds_logger_until_remote_cleanup_finishes(self):
        from tests.test_scan_takeover import ScanExecutionTests
        for fail in (False, True):
            fixture = ScanExecutionTests()
            backend = fixture.make_backend()
            channel = Channel([b'kern.err panic-before-disconnect\n'])
            r = runner(channel)
            r.ssh_client.host_fingerprint = 'fingerprint'
            backend.command_runner = r
            backend.ssh_client = r.ssh_client
            def cleanup(*args):
                self.assertIsNotNone(r.capture_logs.run)
                self.assertFalse(channel.closed)
            backend._finish_capture_task.side_effect = cleanup
            if fail:
                backend.wifi_scanner.scan.side_effect = RuntimeError('original scan fault')
            with patch('modules.capture_services.openwrt.management_link.require_management_link'), \
                 patch('modules.capture_services.openwrt.scan_channel_policy.scan_takeover_needed', return_value=False):
                if fail:
                    with self.assertRaisesRegex(RuntimeError, 'original scan fault'):
                        backend.scan_wifi('session', fixture.options('5G'), cancel_event=Event())
                else:
                    self.assertEqual(backend.scan_wifi('session', fixture.options('5G')).csv_path, 'scanned')
            backend._finish_capture_task.assert_called_once()
            self.assertIsNone(r.capture_logs.run)
            self.assertTrue(channel.closed)

    def test_probe_timeout_and_bounded_context_history(self):
        r, channel = scene()
        channel.chunks.clear(); channel.eof = False
        with patch('modules.capture_services.openwrt.scan_evidence.time.monotonic', side_effect=(0, 5, 5)):
            collect_scan_context(r, 'rmmon1', '5G', 60, 10)
        row = r.operation_diagnostics.snapshot()['capture_evidence']['scan_contexts'][-1]
        self.assertEqual(row['state'], 'deadline')
        self.assertTrue(row['timed_out'])
        self.assertTrue(channel.closed)
        for i in range(20):
            r.operation_diagnostics.capture_evidence('scan_contexts', {'duration_seconds': 60}, r.operation_diagnostics.epoch)
        self.assertEqual(len(r.operation_diagnostics.snapshot()['capture_evidence']['scan_contexts']), 8)

    def test_context_and_kernel_evidence_survive_history_reopen(self):
        import hashlib
        r, channel = scene()
        with tempfile.TemporaryDirectory() as folder:
            history = LogHistory(folder, 'scan-test')
            try:
                with patch('modules.log_history.current_history', return_value=history):
                    collect_scan_context(r, 'rmmon1', '5G', 60, 10)
                    r.operation_diagnostics.capture_evidence('system_lines', {'text': 'kern.err panic-before-reboot'}, r.operation_diagnostics.epoch)
                history.flush()
            finally:
                history.close()
            history = LogHistory(folder, 'next-run')
            try:
                exported = history_attachments(history, '\u6293\u5305', hashlib.sha256(b'host-key-a').hexdigest(), HistoryMasker())
                data = b'\n'.join(content for name, content in exported if name.endswith('.txt'))
                self.assertIn(b'panic-before-reboot', data)
                self.assertIn(b'duration_seconds', data)
            finally:
                history.close()

    def test_real_shell_reads_stack_at_end_of_pstore_without_keyword_filter(self):
        from modules.capture_services.openwrt.router_health_evidence import LOG_COMMAND, parse_faults
        shell = local_shell()
        if shell is None:
            self.skipTest('Local shell unavailable')
        with tempfile.TemporaryDirectory() as folder:
            pstore = Path(folder) / 'pstore'; pstore.mkdir()
            stack = 'old boot text\n' * 1000 + 'Kernel panic - test\n' + ''.join('stack_frame_'+str(i)+'\n' for i in range(25))
            (pstore / 'dmesg-ramoops-0').write_text(stack, encoding='utf-8')
            script = 'logread() { printf "ordinary log\\n"; }\ndmesg() { printf "ordinary kernel\\n"; }\n'
            script += LOG_COMMAND.replace('/sys/fs/pstore', './pstore')
            result = subprocess.run([str(shell)], input=script, text=True, capture_output=True, cwd=folder, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            data = parse_faults(result.stdout)
            self.assertEqual(data['pstore_files'], 1)
            self.assertTrue(any('stack_frame_24' in line for line in data['lines']))
            self.assertTrue(any('Kernel panic' in line for line in data['lines']))

    def test_shell_parses_without_mutating_router_configuration(self):
        shell = local_shell()
        if shell is None:
            self.skipTest('Local shell unavailable')
        command = COMMAND.replace('@INTERFACE@', "'rmmon1'")
        result = subprocess.run([str(shell), '-n'], input=command, text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        for value in ('uci ', 'reboot;', 'dmesg -c', 'mount ', 'killall '):
            self.assertNotIn(value, command)
