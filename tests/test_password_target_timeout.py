"""Channel recovery, unconfirmed candidates and target isolation for online verification."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import Mock, patch
from modules.capture_services.openwrt.models import AccessPointInfo
from modules.capture_services.openwrt.password_verification import OpenWrtPasswordVerifier, PasswordVerifyCancelled
from modules.capture_services.openwrt.password_control import ControlRequestTimeout
from modules.capture_services.openwrt.password_history import PasswordAttemptHistory
from modules.capture_services.openwrt.password_reporting import PasswordVerificationReporter
from modules.capture_services.openwrt.password_rounds import CandidateRound

TARGETS = tuple(AccessPointInfo('02:11:22:33:44:' + f'{n:02x}', ssid=chr(65 + n), band='2.4G', encryption='WPA2-PSK') for n in range(3))
OPEN = 'modules.capture_services.openwrt.password_control.open_control'


class PasswordTargetTimeoutTests(unittest.TestCase):
    def fixture(self):
        runner = Mock(); runner.ssh_client.connected = True
        verifier = OpenWrtPasswordVerifier(runner)
        verifier._backup_configs = Mock(); verifier._restore = Mock(return_value='')
        verifier._pick_radio = Mock(return_value='radio7'); verifier._setup_environment = Mock()
        verifier._set_credentials = Mock(); verifier._verify_connection = Mock(return_value=None)
        return verifier

    def test_native_channel_loss_restarts_once_and_retries_same_candidate(self):
        verifier = self.fixture()
        broken = Mock(); broken.attempt.side_effect = ControlRequestTimeout('status')
        healthy = Mock(); healthy.attempt.return_value = True
        with TemporaryDirectory() as folder, patch(OPEN, side_effect=[broken, healthy]):
            result = verifier._verify_single(TARGETS[0], ['candidate'], 0, 1, 'owned', 'net', folder, None, None, False)
        self.assertTrue(result['entry'].found)
        self.assertEqual(healthy.attempt.call_count, 1)
        broken.close.assert_called_once(); healthy.close.assert_called_once()

    def test_restart_failure_uses_compatibility_then_next_signal_returns_to_native(self):
        verifier = self.fixture()
        verifier._verify_connection.return_value = True
        broken = Mock(); broken.attempt.side_effect = ControlRequestTimeout('status')
        healthy = Mock(); healthy.attempt.return_value = True
        phases = []
        def progress(update):
            text = getattr(update, 'phase', '')
            if text: phases.append(text)
        with TemporaryDirectory() as folder, patch(OPEN, side_effect=[broken, None, healthy]):
            results = verifier.verify_targets(TARGETS[:2], ['candidate'], 'owned', folder, progress_callback=progress)
        self.assertEqual([entry.found for entry in results], [True, True])
        verifier._set_credentials.assert_called_once()
        self.assertTrue(any('兼容' in text for text in phases))
        self.assertEqual(healthy.attempt.call_count, 1)

    def test_compatibility_unconfirmed_is_not_wrong_key_and_batch_continues(self):
        verifier = self.fixture()
        broken = Mock(); broken.attempt.side_effect = ControlRequestTimeout('status')
        healthy = Mock(); healthy.attempt.return_value = True
        with TemporaryDirectory() as folder, PasswordAttemptHistory(Path(folder) / 'history', key=b'k' * 32) as history:
            with patch(OPEN, side_effect=[broken, None, healthy]):
                results = verifier.verify_targets(TARGETS[:2], ['candidate'], 'owned', folder, attempt_history=history)
            self.assertTrue(results[0].error)
            self.assertEqual(results[0].outcome, 'unconfirmed')
            self.assertEqual(results[0].unconfirmed, 1)
            self.assertFalse(results[0].found)
            self.assertFalse(history.contains(TARGETS[0], 'candidate'))
            self.assertTrue(results[1].found)

    def test_single_unconfirmed_candidate_moves_to_next_candidate(self):
        verifier = self.fixture()
        control = Mock(); control.attempt.side_effect = [None, False]
        verifier._verify_connection.return_value = False
        with TemporaryDirectory() as folder, patch(OPEN, return_value=control):
            results = verifier.verify_targets(TARGETS[:1], ['candidate-one', 'candidate-two'], 'owned', folder)
        entry = results[0]
        self.assertFalse(entry.found)
        self.assertEqual(entry.tried, 2)
        self.assertEqual(entry.unconfirmed, 1)
        self.assertEqual(entry.outcome, 'unconfirmed')
        self.assertEqual(control.attempt.call_count, 2)

    def test_ssh_loss_still_aborts_and_runs_restore(self):
        verifier = self.fixture()
        broken = Mock()
        def lost(*args):
            verifier.command_runner.ssh_client.connected = False
            raise ControlRequestTimeout('status')
        broken.attempt.side_effect = lost
        with TemporaryDirectory() as folder, patch(OPEN, return_value=broken):
            with self.assertRaises(TimeoutError):
                verifier.verify_targets(TARGETS, ['candidate'], 'owned', folder)
        broken.close.assert_called_once(); verifier._restore.assert_called_once()

    def test_cancel_during_attempt_still_aborts_and_runs_restore(self):
        verifier = self.fixture(); event = Event()
        broken = Mock()
        def cancelled(*args):
            event.set(); raise ControlRequestTimeout('status')
        broken.attempt.side_effect = cancelled
        with TemporaryDirectory() as folder, patch(OPEN, return_value=broken):
            with self.assertRaises(PasswordVerifyCancelled):
                verifier.verify_targets(TARGETS, ['candidate'], 'owned', folder, event)
        broken.close.assert_called_once(); verifier._restore.assert_called_once()

    def test_next_environment_preparation_failure_is_not_swallowed(self):
        verifier = self.fixture()
        verifier._pick_radio.side_effect = ['radio7', 'radio8', 'radio9']
        verifier._setup_environment.side_effect = [None, RuntimeError('controlled preparation failure')]
        controls = [Mock(), Mock(), Mock()]
        controls[0].attempt.return_value = True; controls[2].attempt.return_value = True
        with TemporaryDirectory() as folder, patch(OPEN, side_effect=controls):
            with self.assertRaisesRegex(RuntimeError, 'controlled preparation failure'):
                verifier.verify_targets(TARGETS, ['candidate'], 'owned', folder)
        controls[2].attempt.assert_not_called(); verifier._restore.assert_called_once()

    def test_capability_probe_timeout_uses_compatibility_then_native_again(self):
        verifier = self.fixture()
        verifier._verify_connection.return_value = True
        healthy = Mock(); healthy.attempt.return_value = True
        phases = []
        def progress(update):
            text = getattr(update, 'phase', '')
            if text: phases.append(text)
        with TemporaryDirectory() as folder, patch(OPEN, side_effect=[TimeoutError('probe'), healthy]):
            results = verifier.verify_targets(TARGETS[:2], ['candidate'], 'owned', folder, progress_callback=progress)
        self.assertEqual([entry.found for entry in results], [True, True])
        verifier._set_credentials.assert_called_once()
        self.assertTrue(any('兼容' in text for text in phases))
        self.assertEqual(healthy.attempt.call_count, 1)

    def test_control_cleanup_failure_still_aborts_batch(self):
        verifier = self.fixture()
        healthy = Mock(); healthy.attempt.return_value = True
        healthy.close.side_effect = RuntimeError('controlled cleanup failure')
        with TemporaryDirectory() as folder, patch(OPEN, return_value=healthy):
            with self.assertRaisesRegex(RuntimeError, 'controlled cleanup failure'):
                verifier.verify_targets(TARGETS[:1], ['candidate'], 'owned', folder)
        verifier._restore.assert_called_once()

    def test_unconfirmed_target_continues_into_later_dictionary_rounds(self):
        verifier = self.fixture()
        first = Mock(); first.attempt.return_value = True
        unknown = Mock(); unknown.attempt.return_value = None
        third = Mock(); third.attempt.return_value = True
        later = Mock(); later.attempt.return_value = True
        rounds = (CandidateRound('one', ('candidate',)), CandidateRound('two', ('another-candidate',)))
        with TemporaryDirectory() as folder, patch(OPEN, side_effect=[first, unknown, third, later]):
            results = verifier.verify_targets(TARGETS, ['candidate', 'another-candidate'], 'owned', folder,
                                             rounds=rounds, dictionary_first=True)
        self.assertEqual(len(results), 3)
        self.assertTrue(results[1].found)
        self.assertEqual(results[1].unconfirmed, 1)
        self.assertEqual(first.attempt.call_count, 1)
        self.assertEqual(third.attempt.call_count, 1)
        self.assertEqual(later.attempt.call_count, 1)

    def test_real_target_timeout_is_skipped_and_batch_continues(self):
        from modules.capture_services.openwrt.password_verification import PasswordTargetResult, PasswordTargetTimeout
        verifier = self.fixture()
        def found(target, index):
            return {'entry': PasswordTargetResult(target.bssid, target.ssid, True, 1, 1), 'setup_done': True}
        with TemporaryDirectory() as folder:
            fake = Mock(side_effect=[found(TARGETS[0], 0), PasswordTargetTimeout(1, 1, 0), found(TARGETS[2], 2)])
            with patch.object(verifier, '_verify_single', fake):
                results = verifier.verify_targets(TARGETS, ['candidate'], 'owned', folder)
        self.assertEqual([entry.found for entry in results], [True, False, True])
        self.assertTrue(results[1].error)
        self.assertEqual(results[1].outcome, 'timeout')
        self.assertTrue(results[1].final)

    def test_summary_counts_unconfirmed_and_timeouts_separately(self):
        from modules.capture_services.capture_service import CaptureService
        from modules.capture_services.openwrt.models import OpenWrtSessionSnapshot
        from modules.capture_services.openwrt.password_verification import PasswordTargetResult
        service = Mock(); service.openwrt_backend.snapshot = OpenWrtSessionSnapshot(task_state='capturing')
        timeout = PasswordTargetResult(TARGETS[1].bssid, 'B', False, 1, 2, error=True, outcome='timeout')
        unknown = PasswordTargetResult(TARGETS[2].bssid, 'C', False, 1, 2, error=True, outcome='unconfirmed', unconfirmed=1)
        CaptureService._on_openwrt_password_succeeded(service, ((timeout, unknown), ''))
        summary = service.messageGenerated.emit.call_args.args[0]
        self.assertIn('未找到 0', summary)
        self.assertIn('超时未完成 1', summary)
        self.assertIn('未确认 1', summary)


if __name__ == '__main__':
    unittest.main()
