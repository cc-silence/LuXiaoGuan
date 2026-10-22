"""Offline evidence and redacted ZIP regression for rejected capture preflight."""
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from modules.capture_services.openwrt.command_runner import OpenWrtCommandRunner, RemoteCommandResult
from modules.capture_services.openwrt.radio_diagnostics import (
    RadioSelectionDiagnostics, MAX_ATTEMPT_BYTES, MAX_RECORDS, sanitized_radio_evidence)
from modules.capture_services.openwrt.radio_policy import phy_supports_channel
from modules.capture_services.backends.openwrt_backend import OpenWrtCaptureBackend
from modules.capture_services.openwrt.models import AccessPointInfo, OpenWrtSessionSnapshot
from modules.capture_services.openwrt.diagnostic_context import router_context_json
from modules.display_privacy import masker_for
from modules.log_services import export_logs, _router_context_bytes

IW_DEV = """phy#0
    Interface phy0-sta0
        type managed
        addr aa:bb:cc:dd:ee:ff
        ssid PrivateNetwork
        channel 11 (2462 MHz), width: 20 MHz
    Interface phy0-ap0
        type AP
        channel 11 (2462 MHz), width: 20 MHz
phy#1
    Interface phy1-ap0
        type AP
        channel 36 (5180 MHz), width: 80 MHz
"""
PHY_INFO = """Wiphy phy0
    Supported interface modes:
        * managed
        * AP
        * monitor
    Band 1:
        Frequencies:
            * 2412 MHz [1] (disabled)
            * 2462 MHz [11] (20.0 dBm)
"""


class RadioDiagnosticsTests(unittest.TestCase):
    def runner(self, output=PHY_INFO):
        runner = OpenWrtCommandRunner(Mock())
        def execute(name, command, timeout, cancel_event=None, **kwargs):
            if name == 'list_phys':
                text = 'phy0 phy1'
            elif name in ('probe_phy_channel', 'probe_phy_band'):
                text = output
            else:
                text = IW_DEV
            return RemoteCommandResult(name, 0, text)
        runner._run = Mock(side_effect=execute)
        return runner

    def test_preflight_rejection_exports_targets_raw_evidence_and_reason_codes(self):
        backend = OpenWrtCaptureBackend()
        backend.command_runner = self.runner()
        targets = tuple(AccessPointInfo('aa:bb:cc:dd:ee:ff', 'PrivateNetwork', -55,
            channel=c, band='2.4G') for c in (1, 6, 11))
        entries = backend.probe_handshake_takeover(targets, {})
        self.assertTrue(entries[0].error)
        self.assertTrue(entries[1].error)
        self.assertEqual(entries[2].phy, 'phy0')
        evidence = backend.command_runner.radio_diagnostics.snapshot()
        attempt = evidence['attempts'][0]
        self.assertEqual(attempt['status'], 'rejected')
        self.assertEqual([r['channel'] for r in attempt['targets']], [1, 6, 11])
        self.assertEqual([r['reason'] for r in attempt['checks']],
                         ['channel_disabled', 'channel_not_listed', 'available'])
        operations = {r['operation'] for r in attempt['queries']}
        self.assertIn('probe_takeover_needs', operations)
        self.assertIn('probe_phy_channel', operations)
        self.assertTrue(all('command' not in r and 'ssid' not in r for r in attempt['queries']))
        self.assertTrue(all(r['elapsed_seconds'] is not None for r in attempt['queries']))
        self.assertTrue(all(call.args[1].startswith(('iw ', 'ls ')) for call in backend.command_runner._run.call_args_list))
        masker = masker_for(['PrivateNetwork'])
        evidence['attempts'][0]['queries'][0]['password'] = 'do-not-export'
        evidence['attempts'][0]['queries'][0]['stderr'] = 'password=do-not-export\nnext useful line'
        context = router_context_json(OpenWrtSessionSnapshot.initial(), masker,
            radio_evidence=evidence, app_version='1.2.3_test')
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'feedback.zip'
            export_logs(path, 'capture', [('failed', 'warning')], masker=masker,
                        router_context=context, app_version='1.2.3_test')
            with zipfile.ZipFile(path) as archive:
                raw = archive.read('router-info.json').decode('utf-8')
                payload = json.loads(raw)
                self.assertIsNone(archive.testzip())
                self.assertIn('radio-selection.json', archive.namelist())
                index = json.loads(archive.read('diagnostic-index.json'))
                self.assertEqual(index['attachments'][0]['file'], 'radio-selection.json')
                self.assertIn('1.2.3_test', archive.read('README.txt').decode())
            self.assertNotIn('PrivateNetwork', raw)
            self.assertNotIn('aa:bb:cc:dd:ee:ff', raw)
            self.assertNotIn('do-not-export', raw)
            exported = payload['radio_selection']['attempts'][0]
            self.assertEqual(exported['targets'][0]['channel'], 1)
            self.assertIn('2412 MHz [1] (disabled)', raw)
            self.assertIn('2462 MHz [11]', raw)
            self.assertEqual(exported['checks'][1]['reason'], 'channel_not_listed')

    def test_channel_frequency_formats_and_restrictions(self):
        cases = (
            ('2412', 1, '', True, 'available'),
            ('2412.0', 1, '', True, 'available'),
            ('2437.0', 6, '', True, 'available'),
            ('2442.0', 7, '', True, 'available'),
            ('5180.0', 36, '', True, 'available'),
            ('2484.0', 14, 'disabled', False, 'channel_disabled'),
            ('2412.0', 1, 'disabled', False, 'channel_disabled'),
            ('2412.5', 1, '', False, 'channel_not_listed'),
            ('2437.0', 1, '', False, 'channel_not_listed'),
        )
        for mhz, channel, flags, expected, reason in cases:
            with self.subTest(mhz=mhz, channel=channel, flags=flags):
                output = PHY_INFO[:PHY_INFO.index('    Band 1:')]
                output += f'    Band 1:\n        Frequencies:\n            * {mhz} MHz [{channel}] ({flags})\n'
                runner = self.runner(output)
                with runner.radio_diagnostics.attempt('test', ()):
                    self.assertEqual(phy_supports_channel(runner, 'phy0', channel), expected)
                checks = runner.radio_diagnostics.snapshot()['attempts'][0]['checks']
                self.assertEqual(checks[0]['reason'], reason)

    def test_decimal_frequencies_allow_capture_preflight(self):
        output = PHY_INFO[:PHY_INFO.index('    Band 1:')]
        output += ('    Band 1:\n        Frequencies:\n'
                   '            * 2412.0 MHz [1] (30.0 dBm)\n'
                   '            * 2437.0 MHz [6] (30.0 dBm)\n'
                   '            * 2442.0 MHz [7] (30.0 dBm)\n')
        backend = OpenWrtCaptureBackend()
        backend.command_runner = self.runner(output)
        targets = tuple(AccessPointInfo('aa:bb:cc:dd:ee:ff', 'PrivateNetwork', -55,
            channel=c, band='2.4G') for c in (1, 6, 7))
        entries = backend.probe_handshake_takeover(targets, {})
        self.assertEqual(len(entries), len(targets))
        self.assertTrue(all(entry.phy == 'phy0' and not entry.error for entry in entries))
        self.assertTrue(all(entry.need_takeover for entry in entries))

    def test_unknown_and_monitor_rejections_keep_original_behavior(self):
        for output, expected in ((PHY_INFO.replace('        * monitor\n', ''), 'monitor_mode_not_advertised'),
                                 ('unrecognized phy output', 'channel_list_unparsed')):
            runner = self.runner(output)
            with runner.radio_diagnostics.attempt('test', (SimpleNamespace(band='2.4G', channel=1),)):
                if expected == 'channel_list_unparsed':
                    with self.assertRaises(RuntimeError):
                        phy_supports_channel(runner, 'phy0', 1)
                else:
                    self.assertFalse(phy_supports_channel(runner, 'phy0', 1))
            self.assertEqual(runner.radio_diagnostics.snapshot()['attempts'][0]['checks'][0]['reason'], expected)

    def test_timeout_transport_error_and_cancellation_are_exportable(self):
        for result in (RemoteCommandResult('probe_phy_channel', -1, 'partial output', timed_out=True),
                       RemoteCommandResult('probe_phy_channel', -1, 'partial output', cancelled=True)):
            runner = self.runner()
            runner._run = Mock(return_value=result)
            with self.assertRaises(RuntimeError):
                with runner.radio_diagnostics.attempt('test', (SimpleNamespace(band='2.4G', channel=1),)):
                    phy_supports_channel(runner, 'phy0', 1)
            record = runner.radio_diagnostics.snapshot()['attempts'][0]['queries'][0]
            self.assertEqual(record['stdout'], 'partial output')
            self.assertEqual(record['timed_out'], result.timed_out)
            self.assertEqual(record['cancelled'], result.cancelled)
        runner = self.runner()
        runner._run = Mock(side_effect=OSError('channel closed'))
        with self.assertRaises(OSError):
            with runner.radio_diagnostics.attempt('test', (SimpleNamespace(band='2.4G', channel=1),)):
                phy_supports_channel(runner, 'phy0', 1)
        self.assertEqual(runner.radio_diagnostics.snapshot()['attempts'][0]['queries'][0]['error_type'], 'OSError')

    def test_recording_is_bounded_and_freezes_without_mutating_live_data(self):
        journal = RadioSelectionDiagnostics()
        target = (SimpleNamespace(band='2.4G', channel=11),)
        for i in range(5):
            with journal.attempt('test', target):
                for _ in range(40):
                    journal.query('probe_phy_channel', 'iw phy phy0 info',
                                  result=RemoteCommandResult('probe_phy_channel', 0, 'x' * 100000))
        snapshot = journal.snapshot()
        self.assertEqual(len(snapshot['attempts']), 2)
        for attempt in snapshot['attempts']:
            self.assertLessEqual(len(attempt['queries']), MAX_RECORDS)
            self.assertLessEqual(sum(len(q['stdout'].encode()) for q in attempt['queries']), MAX_ATTEMPT_BYTES)
            self.assertGreater(attempt['records_omitted'], 0)
            self.assertTrue(any(q['stdout_truncated'] for q in attempt['queries']))
        snapshot['attempts'].clear()
        self.assertEqual(len(journal.snapshot()['attempts']), 2)
        journal.clear()
        self.assertEqual(journal.snapshot()['attempts'], [])

    def test_latest_failure_survives_following_successful_scans(self):
        journal = RadioSelectionDiagnostics()
        target = (SimpleNamespace(band='2.4G', channel=11),)
        with self.assertRaises(RuntimeError):
            with journal.attempt('failed_preflight', target):
                raise RuntimeError('retained reason')
        for _ in range(5):
            with journal.attempt('successful_scan', target):
                pass
        attempts = journal.snapshot()['attempts']
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0]['operation'], 'failed_preflight')
        self.assertEqual(attempts[0]['error'], 'retained reason')
        self.assertEqual(attempts[1]['operation'], 'successful_scan')

    def test_same_router_reconnect_keeps_evidence_other_router_clears_it(self):
        journal = RadioSelectionDiagnostics()
        journal.bind_connection('host-key-a')
        with journal.attempt('old_preflight', ()):
            pass
        journal.bind_connection('host-key-a')
        self.assertEqual(len(journal.snapshot()['attempts']), 1)
        journal.bind_connection('host-key-b')
        self.assertEqual(journal.snapshot()['attempts'], [])
        self.assertNotIn('host-key', json.dumps(journal.snapshot()))

    def test_export_boundary_rejects_oversized_or_arbitrary_evidence(self):
        with self.assertRaises(ValueError):
            sanitized_radio_evidence({'schema_version': 1, 'attempts': [{}, {}, {}]}, masker_for([]))
        context = json.dumps({'schema_version': 1, 'device': None,
                             'radio_selection': {'schema_version': 1, 'attempts': [], 'secret': 'bad'}})
        output = _router_context_bytes(context, masker_for([])).decode()
        self.assertNotIn('bad', output)
        self.assertNotIn('secret', output)

    def test_no_query_output_is_retained_outside_a_radio_attempt(self):
        runner = self.runner()
        runner.run('probe_phy_channel', 'iw phy phy0 info', 10)
        self.assertEqual(runner.radio_diagnostics.snapshot()['attempts'], [])
        with runner.radio_diagnostics.attempt('test', ()):
            runner.run('password_check', 'never retain this command', 10)
        self.assertEqual(runner.radio_diagnostics.snapshot()['attempts'][0]['queries'], [])
