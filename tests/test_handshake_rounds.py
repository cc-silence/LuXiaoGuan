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

    def test_unlimited_repeats_identical_content_until_success(self):
        calls,result=self.run_batch(infinite=True)
        self.assertEqual(calls,[1,2,3,4])
        self.assertEqual(result.results[0].result.verdict,"complete")
        self.assertEqual(result.results[0].stop_reason,"")

    def test_fixed_mode_retains_no_progress_stop(self):
        calls,result=self.run_batch(infinite=False)
        self.assertEqual(calls,[1,2])
        self.assertEqual(result.results[0].stop_reason,"no_progress")

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

if __name__ == "__main__":
    unittest.main()
