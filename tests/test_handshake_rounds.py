"""Coordinator regression for fixed and unlimited capture rounds."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace as NS
from unittest.mock import Mock
from modules.capture_services.backends.openwrt_backend import OpenWrtCaptureBackend, HandshakeTargetResult
from modules.capture_services.openwrt.handshake_capture import HandshakeCaptureResult
from modules.capture_services.openwrt.models import AccessPointInfo

class HandshakeRoundTests(unittest.TestCase):
    def run_batch(self, *, infinite, finish_round=4, cancel_round=None, error=False, unknown=False):
        target=AccessPointInfo("02:11:22:33:44:55", channel=6)
        event=Event()
        calls=[]
        backend=NS(ssh_client=NS(connected=True), _ensure_remote_tmp=Mock(),
                   reconnect_cleanup=Mock(), workspace_manager=Mock(),
                   _finish_capture_task=Mock())
        def capture(*args, **kwargs):
            round_no=args[7]
            calls.append(round_no)
            if cancel_round == round_no:
                event.set()
            if error:
                return HandshakeTargetResult(target, error="interface unavailable", error_code="preparation_failed"), False
            verdict="complete" if round_no == finish_round else ("unknown" if unknown else "partial")
            result=HandshakeCaptureResult("", target.bssid, "", verdict=verdict, content_fingerprint="unchanged")
            return HandshakeTargetResult(target, result), verdict != "complete"
        backend._capture_single_handshake=capture
        with TemporaryDirectory() as folder:
            settings={"resolved_output_dir":folder, "handshake_rounds_infinite":infinite, "handshake_rounds":5}
            result=OpenWrtCaptureBackend.capture_handshakes_passive(backend, "offline", settings, (target,), event)
        backend._finish_capture_task.assert_called_once()
        return calls,result

    def run_tune_batch(self, fail_rounds, rounds=5, grouped=True):
        target = AccessPointInfo("02:11:22:33:44:55", band="2.4G", channel=9)
        calls = []
        backend = NS(ssh_client=NS(connected=True), _ensure_remote_tmp=Mock(),
                     reconnect_cleanup=Mock(), workspace_manager=Mock(),
                     _finish_capture_task=Mock())
        def capture(*args, **kwargs):
            round_no = args[7]
            calls.append(round_no)
            if round_no in fail_rounds:
                return HandshakeTargetResult(target, error="tune failed", error_code="channel_tune_failed"), False
            result = HandshakeCaptureResult("", target.bssid, "", verdict="complete")
            return HandshakeTargetResult(target, result), False
        backend._capture_single_handshake = capture
        with TemporaryDirectory() as folder:
            settings = {"resolved_output_dir": folder, "handshake_rounds": rounds}
            result = OpenWrtCaptureBackend.capture_handshakes_passive(
                backend, "offline", settings, (target,), Event(), group_same_channel=grouped)
        return calls, result, backend

    def test_channel_failure_stops_after_three_rounds(self):
        calls, result, _ = self.run_tune_batch({1, 2, 3, 4})
        self.assertEqual(calls, [1, 2, 3])
        self.assertEqual(result.results[0].error_code, "channel_tune_failed")

    def test_channel_failure_then_success_clears_old_error(self):
        calls, result, backend = self.run_tune_batch({1, 2})
        self.assertEqual(calls, [1, 2, 3])
        self.assertEqual(result.results[0].result.verdict, "complete")
        self.assertFalse(result.note)
        self.assertIsNone(backend._finish_capture_task.call_args.args[1])

    def test_round_limit_still_applies_to_channel_failures(self):
        calls, _, _ = self.run_tune_batch({1, 2, 3}, rounds=2)
        self.assertEqual(calls, [1, 2])

    def test_other_capture_path_keeps_existing_failure_behavior(self):
        calls, _, _ = self.run_tune_batch({1, 2, 3}, grouped=False)
        self.assertEqual(calls, [1])

    def test_unlimited_repeats_identical_content_until_success(self):
        calls,result=self.run_batch(infinite=True)
        self.assertEqual(calls,[1,2,3,4])
        self.assertEqual(result.results[0].result.verdict,"complete")
        self.assertEqual(result.results[0].stop_reason,"")

    def test_fixed_mode_retries_identical_content_until_success(self):
        calls,result=self.run_batch(infinite=False)
        self.assertEqual(calls,[1,2,3,4])
        self.assertEqual(result.results[0].result.verdict,"complete")

    def test_fixed_mode_honors_round_limit_with_identical_content(self):
        calls,result=self.run_batch(infinite=False, finish_round=99)
        self.assertEqual(calls,[1,2,3,4,5])
        self.assertEqual(result.results[0].result.verdict,"partial")

    def test_unlimited_remains_cancellable(self):
        calls,result=self.run_batch(infinite=True, finish_round=99, cancel_round=4)
        self.assertEqual(calls,[1,2,3,4])
        self.assertTrue(result.cancelled)

    def test_execution_failure_is_not_retried_forever(self):
        calls,result=self.run_batch(infinite=True,error=True)
        self.assertEqual(calls,[1])
        self.assertIn("interface unavailable",result.note)

    def test_uncheckable_capture_remains_explicit(self):
        calls,result=self.run_batch(infinite=True,unknown=True)
        self.assertEqual(calls,[1])
        self.assertEqual(result.results[0].stop_reason,"unknown")

class ChannelTraceTests(unittest.TestCase):
    def test_failed_tune_keeps_bounded_readback_and_error(self):
        from unittest.mock import patch
        from modules.capture_services.openwrt.handshake_capture import (
            ChannelTuneError, OpenWrtHandshakeCapture)
        from modules.capture_services.openwrt.command_runner import RemoteCommandResult
        clock = [0.0]
        runner = Mock()
        runner.run.return_value = RemoteCommandResult(
            "read", 0, stdout="channel 11 (2462 MHz)", stderr="driver note")
        processes = Mock()
        processes.is_running.return_value = True
        capture = OpenWrtHandshakeCapture(runner, processes)
        def sleep(seconds):
            clock[0] += seconds
        with patch("modules.capture_services.openwrt.handshake_capture.time.monotonic",
                   side_effect=lambda: clock[0]), patch(
                   "modules.capture_services.openwrt.handshake_capture.time.sleep",
                   side_effect=sleep):
            with self.assertRaises(ChannelTuneError) as caught:
                capture._verify_tuned_channel(object(), "ownedmon", 9, None)
        trace = caught.exception.channel_trace
        self.assertIn("channel-verification", trace)
        self.assertIn("channel 11", trace)
        self.assertIn("driver note", trace)
        self.assertLessEqual(trace.count("elapsed="), 20)
        self.assertNotIn("2>/dev/null", runner.run.call_args.args[1])

class CompletedTargetSelectionTests(unittest.TestCase):
    def test_completed_targets_skipped_and_remaining_started(self):
        from modules.capture_services.capture_service import CaptureService
        from modules.capture_services.openwrt.models import (
            AccessPointCaptureState, OpenWrtConnectionState, OpenWrtSessionSnapshot)
        completed = AccessPointInfo("02:11:22:33:44:55", channel=6,
                                    capture_state=AccessPointCaptureState.CAPTURED)
        pending = AccessPointInfo("02:11:22:33:44:66", channel=6)
        snapshot = OpenWrtSessionSnapshot(connection_state=OpenWrtConnectionState.CONNECTED,
                                           access_points=(completed, pending))
        backend = NS(snapshot=snapshot, capture_handshakes_passive=Mock())
        owner = NS(_shutting_down=False, task_controller=NS(busy=False, begin=Mock(return_value=Event())),
                   openwrt_backend=backend, messageGenerated=NS(emit=Mock()),
                   openwrtSnapshotChanged=NS(emit=Mock()), _stash_privacy_mask=Mock(),
                   _stash_result_prefs=Mock(), _masked_target=lambda ssid,bssid: bssid,
                   _start_openwrt_worker=Mock(), _on_openwrt_handshakes_succeeded=Mock(),
                   _on_openwrt_handshakes_failed=Mock(), _on_openwrt_handshakes_progress=Mock(),
                   _on_openwrt_handshakes_message=Mock())
        started = CaptureService.capture_openwrt_handshakes_async(
            owner, {}, (completed.bssid, pending.bssid))
        self.assertTrue(started)
        operation = owner._start_openwrt_worker.call_args.args[1]
        operation(Event(), Mock(), Mock())
        self.assertEqual(backend.capture_handshakes_passive.call_args.args[2], (pending,))
        self.assertEqual(owner._handshake_batch_targets, (pending.bssid,))
        self.assertTrue(backend.snapshot.access_points[0].selected)

    def test_all_completed_does_not_begin_task(self):
        from modules.capture_services.capture_service import CaptureService
        from modules.capture_services.openwrt.models import (
            AccessPointCaptureState, OpenWrtConnectionState, OpenWrtSessionSnapshot)
        point = AccessPointInfo("02:11:22:33:44:55", channel=6,
                                capture_state=AccessPointCaptureState.CAPTURED)
        snapshot = OpenWrtSessionSnapshot(connection_state=OpenWrtConnectionState.CONNECTED,
                                           access_points=(point,))
        owner = NS(_shutting_down=False, task_controller=NS(busy=False, begin=Mock()),
                   openwrt_backend=NS(snapshot=snapshot), messageGenerated=NS(emit=Mock()))
        self.assertFalse(CaptureService.capture_openwrt_handshakes_async(owner, {}, (point.bssid,)))
        owner.task_controller.begin.assert_not_called()

if __name__ == "__main__":
    unittest.main()
