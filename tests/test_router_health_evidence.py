"""Boot comparison, bounded export, compatibility and diagnostic failure isolation."""
import json
import subprocess
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from modules.capture_services.openwrt.router_health_evidence import (
    SAMPLE_COMMAND, LOG_COMMAND, BEGIN, END, parse_sample, parse_faults,
    collect_connection, accept_guard, RouterHealthEvidence)
from modules.capture_services.openwrt.operation_diagnostics import OperationDiagnostics, sanitized_operation_evidence
from modules.capture_services.openwrt.command_runner import RemoteCommandResult
from modules.log_history import HistoryMasker
from tests.posix_shell import local_shell

BOOT1 = '00000000-0000-0000-0000-000000000001'
BOOT2 = '00000000-0000-0000-0000-000000000002'


def sample(boot=BOOT1, uptime=100):
    return f'{BEGIN}\nBOOT {boot}\nUPTIME {uptime}\nTOTAL 250000\nFREE 150000\nAVAILABLE 180000\n{END}\n'


class RouterHealthTests(unittest.TestCase):
    def journal(self):
        journal = OperationDiagnostics()
        journal.bind_connection('device-a')
        return journal

    def runner(self, journal, output):
        return NS(operation_diagnostics=journal, run=Mock(return_value=RemoteCommandResult('router_health', 0, output)))

    def test_normal_login_baseline_does_not_read_logs(self):
        journal = self.journal()
        runner = self.runner(journal, sample())
        collect_connection(runner)
        self.assertEqual(runner.run.call_args.args[1], SAMPLE_COMMAND)
        self.assertEqual(len(journal.snapshot()['router_health']['samples']), 1)
        self.assertFalse(journal.snapshot()['router_health']['incidents'])

    def test_both_boot_id_states_and_uptime_fallback(self):
        for before, after, expected in (
            ({'boot_id': BOOT1}, {'boot_id': BOOT2}, 'changed'),
            ({'boot_id': BOOT1}, {'boot_id': BOOT1}, 'same'),
            ({'uptime_seconds': 100}, {'uptime_seconds': 5}, 'uptime_decreased'),
            ({'uptime_seconds': 5}, {'uptime_seconds': 100}, 'unknown'),
            ({}, {}, 'unknown'),
        ):
            with self.subTest(expected=expected):
                health = RouterHealthEvidence()
                health.sample(before, 'guard')
                health.disconnected()
                health.disconnected()
                health.sample(after, 'connection')
                rows = health.snapshot()['incidents']
                self.assertEqual(len(rows), 2)
                self.assertEqual(rows[-1]['boot_comparison'], expected)
                self.assertFalse(health.pending)

    def test_reconnect_collects_logs_and_export_masks_them(self):
        journal = self.journal()
        journal.health_sample(parse_sample(sample()), 'guard', journal.epoch)
        journal.record('command', operation='guard_probe', status='failed', connected_before=True, connected_after=False)
        runner = self.runner(journal, sample(BOOT2, 5) + '__RM_FAULT_BEGIN__\nSOURCE dmesg\nLOG Out of memory at 192.168.1.1 password=secret-test\nMISSING pstore\n__RM_FAULT_END__\n')
        collect_connection(runner)
        self.assertIn(LOG_COMMAND, runner.run.call_args.args[1])
        exported = sanitized_operation_evidence(journal.snapshot(), HistoryMasker())
        health = exported['router_health']
        self.assertEqual(health['incidents'][-2]['boot_comparison'], 'changed')
        self.assertEqual(health['incidents'][-1]['collection_status'], 'completed')
        self.assertNotIn('192.168.1.1', json.dumps(exported))
        self.assertNotIn('secret-test', json.dumps(exported))

    def test_missing_optional_fields_and_old_export_are_accepted(self):
        self.assertEqual(parse_sample(BEGIN + '\nUPTIME 3\nFREE 7\n' + END), {'uptime_seconds': 3, 'mem_free_kb': 7})
        self.assertEqual(sanitized_operation_evidence({'schema_version': 1}, HistoryMasker())['router_health']['samples'], [])
        self.assertEqual(parse_faults('__RM_FAULT_BEGIN__\nMISSING logread\nMISSING dmesg\nMISSING pstore\n__RM_FAULT_END__')['sources_missing'], ['logread', 'dmesg', 'pstore'])

    def test_diagnostics_timeout_cancel_bad_output_and_exception_do_not_break_login(self):
        for result, error, status in (
            (RemoteCommandResult('router_health', -1, timed_out=True), None, 'timeout'),
            (RemoteCommandResult('router_health', -1, cancelled=True), None, 'cancelled'),
            (RemoteCommandResult('router_health', 0, 'bad data'), None, 'failed'),
            (None, ConnectionResetError(), 'failed'),
        ):
            journal = self.journal()
            runner = self.runner(journal, '')
            runner.run.return_value, runner.run.side_effect = result, error
            collect_connection(runner)
            self.assertEqual(journal.snapshot()['router_health']['incidents'][-1]['collection_status'], status)

    def test_existing_resource_output_survives_invalid_optional_sample(self):
        journal = self.journal()
        runner = self.runner(journal, '')
        for extra in (sample(), BEGIN + '\nbad data\n' + END, ''):
            self.assertEqual(accept_guard(runner, '171:242:10:20\n' + extra, journal.epoch).strip().split(':'), ['171', '242', '10', '20'])

    def test_device_change_clears_and_stale_results_are_rejected(self):
        journal = self.journal()
        old = journal.epoch
        journal.health_sample(parse_sample(sample()), 'guard', old)
        journal.health_disconnected()
        journal.bind_connection('device-a')
        self.assertTrue(journal.health.pending)
        journal.bind_connection('device-b')
        journal.health_sample(parse_sample(sample()), 'guard', old)
        journal.health_collection('completed', {}, old)
        self.assertFalse(journal.health.pending)
        self.assertEqual(journal.snapshot()['router_health']['samples'], [])
        self.assertEqual(journal.snapshot()['router_health']['incidents'], [])

    def test_inflight_probe_cannot_land_on_replacement_device(self):
        journal = self.journal()
        runner = self.runner(journal, sample())
        def replace(*args, **kwargs):
            journal.bind_connection('device-b')
            return RemoteCommandResult('router_health', 0, sample())
        runner.run.side_effect = replace
        collect_connection(runner)
        self.assertEqual(journal.snapshot()['router_health']['samples'], [])

    def test_storage_bounded_and_snapshots_are_copies(self):
        health = RouterHealthEvidence()
        for _ in range(100):
            health.sample({'boot_id': BOOT1}, 'guard')
            health.disconnected()
            health.sample({'boot_id': BOOT1}, 'connection')
        snapshot = health.snapshot()
        self.assertEqual(len(snapshot['samples']), 16)
        self.assertEqual(len(snapshot['incidents']), 8)
        snapshot['samples'].clear()
        self.assertEqual(len(health.snapshot()['samples']), 16)

    def test_shell_syntax_and_no_router_mutations(self):
        shell = local_shell()
        if shell is None: self.skipTest('Git Bash unavailable')
        script = SAMPLE_COMMAND + LOG_COMMAND
        result = subprocess.run([str(shell), '-n'], input=script, text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        for command in ('reboot;', 'dmesg -c', 'mount ', 'uci ', 'killall ', 'rm ', 'logread -f'):
            self.assertNotIn(command, script)

    def test_real_resource_probe_preserves_stats_and_emergency_threshold(self):
        from modules.capture_services.openwrt.auto_capture_service import AutoCaptureService
        for available, expected in ((171, 'normal'), (8, 'warning'), (5, 'danger')):
            journal = self.journal()
            runner = self.runner(journal, f'{available}:242:10:20\n' + sample())
            owner = NS(openwrt_snapshot=NS(connection_state='connected'))
            service = NS(owner=owner, runner=runner, ssh=NS(_client=None), is_busy=True,
                         resourceStatsUpdated=NS(emit=Mock()), messageGenerated=NS(emit=Mock()), stop_all=Mock())
            with patch('modules.capture_services.openwrt.auto_capture_service.threading.Thread') as thread:
                thread.side_effect = lambda **kw: NS(start=kw['target'])
                AutoCaptureService.poll_resources(service)
            service.resourceStatsUpdated.emit.assert_called_once_with(available, 242, 10, 20, expected)
            self.assertEqual(service.stop_all.call_count, int(expected == 'danger'))
            self.assertEqual(len(journal.snapshot()['router_health']['samples']), 1)

    def test_new_connection_discards_old_probe_for_same_device(self):
        journal = self.journal()
        runner = self.runner(journal, sample())
        old = object()
        runner.ssh_client = NS(_client=old)
        def change(*args, **kwargs):
            runner.ssh_client._client = object()
            return RemoteCommandResult('router_health', 0, sample())
        runner.run.side_effect = change
        collect_connection(runner)
        self.assertEqual(journal.snapshot()['router_health']['samples'], [])
        self.assertEqual(accept_guard(runner, '1:2:3:4\n' + sample(), journal.epoch, old), '1:2:3:4\n')
        self.assertEqual(journal.snapshot()['router_health']['samples'], [])

    def test_unexpected_transport_callback_records_without_a_command_failure(self):
        from PySide6.QtCore import QObject, Signal
        from modules.capture_services.router_connection_state import RouterConnectionState
        class Owner(QObject):
            openwrtSnapshotChanged = Signal(object)
        owner = Owner()
        journal = self.journal()
        ssh = NS(_client=None, connected=False)
        owner.openwrt_backend = NS(ssh_client=ssh, command_runner=NS(operation_diagnostics=journal))
        state = RouterConnectionState(owner)
        ssh.connection_lost_callback(object())
        self.assertTrue(journal.health.pending)
        journal.bind_connection('device-b')
        ssh._client = object()
        ssh.connection_lost_callback(object())
        self.assertFalse(journal.health.pending)
        self.assertIsNotNone(state)

    def test_regular_capture_probe_preserves_every_original_exit_code(self):
        from modules.capture_services.openwrt.router_health_evidence import run_process_probe
        import shlex
        shell = local_shell()
        if shell is None: self.skipTest('Git Bash unavailable')
        for status in (0, 1, 80, 81):
            journal = self.journal()
            def run(name, command, **kwargs):
                command = command.replace(SAMPLE_COMMAND, 'printf %s ' + shlex.quote(sample()))
                output = subprocess.run([str(shell)], input=command, text=True, capture_output=True, timeout=5)
                return RemoteCommandResult(name, output.returncode, output.stdout, output.stderr)
            runner = NS(operation_diagnostics=journal, run=run)
            result = run_process_probe(runner, 'airodump-ng', f'printf original-output; exit {status}')
            self.assertEqual(result.exit_code, status)
            self.assertEqual(result.stdout, 'original-output')
            self.assertEqual(journal.snapshot()['router_health']['samples'][-1]['phase'], 'capture_check')

    def test_regular_capture_failure_collects_faults_without_connection_loss(self):
        from modules.capture_services.openwrt.router_health_evidence import collect_capture
        journal = self.journal()
        runner = self.runner(journal, sample() + '__RM_FAULT_BEGIN__\nSOURCE dmesg\nLOG mt76 firmware crash\n__RM_FAULT_END__\n')
        collect_capture(runner, 'capture_failure', failed=True)
        health = sanitized_operation_evidence(journal.snapshot(), HistoryMasker())['router_health']
        self.assertEqual(health['samples'][-1]['phase'], 'capture_failure')
        self.assertIn('mt76 firmware crash', health['incidents'][-1]['lines'])
        self.assertFalse(journal.health.pending)

    def test_non_capture_process_probe_is_unchanged(self):
        from modules.capture_services.openwrt.router_health_evidence import run_process_probe
        journal = self.journal()
        runner = self.runner(journal, 'original')
        run_process_probe(runner, 'unrelated', 'original-command')
        runner.run.assert_called_once_with('poll_unrelated', 'original-command', timeout=5, cancel_event=None)

    def test_strict_sample_parser(self):
        for text in (sample().replace('UPTIME 100', 'UPTIME -1'), sample().replace(BOOT1, 'bad'), sample().replace(END, ''), sample().replace('FREE 150000', 'UPTIME 100')):
            with self.assertRaises(ValueError): parse_sample(text)


if __name__ == '__main__': unittest.main()
