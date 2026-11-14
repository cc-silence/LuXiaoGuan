"""Fallback has a fresh collection budget and bounded retry ownership."""
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from tests.test_bridge_scan_streaming import StreamingManagerTests
from modules.capture_services.openwrt.signal_manager import SignalError


class FallbackBudgetTests(unittest.TestCase):
    def run_case(self, failed_target, *, fallback_fails=False, cancelled=False):
        manager = StreamingManagerTests().manager()
        now = [0.0]
        starts = []
        calls = []
        def call(*args, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                now[0] += 30
                error = SignalError("rpc_iwinfo_scan_7", "timeout")
                error.scan_target = failed_target
                raise error
            now[0] += 2
            if fallback_fails:
                error = SignalError("rpc_iwinfo_scan_7", "timeout")
                error.scan_target = "radio" if failed_target == "interface" else "interface"
                raise error
            return {"results": []}
        manager._call = Mock(side_effect=call)
        cancel = NS(is_set=lambda: cancelled, wait=lambda seconds: False)
        with patch("modules.capture_services.openwrt.wireless_setup_manager.time.monotonic", side_effect=lambda: now[0]):
            if fallback_fails or cancelled:
                with self.assertRaises((SignalError, InterruptedError)):
                    manager.scan(6, cancel, Mock(), radio="radio1", on_started=starts.append)
            else:
                manager.scan(6, cancel, Mock(), radio="radio1", on_started=starts.append)
        return calls, starts, now[0]

    def test_both_directions_receive_full_budget_and_keep_successful_path(self):
        for target, option in (("interface", "force_radio"), ("radio", "compat_interface")):
            with self.subTest(target=target):
                calls, starts, ended = self.run_case(target)
                self.assertEqual(starts, [6, 36])
                self.assertEqual(ended, 36)
                self.assertEqual(len(calls), 4)
                self.assertTrue(all(row.get(option) is True for row in calls[1:]))

    def test_failed_fallback_is_not_repeated(self):
        for target in ("interface", "radio"):
            calls, starts, ended = self.run_case(target, fallback_fails=True)
            self.assertEqual(len(calls), 2)
            self.assertEqual(starts, [6, 36])

    def test_cancel_does_not_launch_scan(self):
        calls, _, _ = self.run_case("interface", cancelled=True)
        self.assertEqual(calls, [])
