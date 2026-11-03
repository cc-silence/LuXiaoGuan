"""Integration boundaries for the collaborator capture lifecycle changes."""
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from modules.capture_services.openwrt.auto_capture_service import CaptureStartupCoordinator
from modules.capture_services.openwrt.command_runner import OpenWrtCommandRunner


class CaptureIntegrationSafetyTests(unittest.TestCase):
    def test_timeout_blocks_all_waiters_until_the_next_explicit_start(self):
        coordinator = CaptureStartupCoordinator(["radio0", "radio7"])
        coordinator.mark_ready("radio0")
        self.assertFalse(coordinator.wait_ready("radio0", timeout=0))
        coordinator.mark_ready("radio7")
        self.assertFalse(coordinator.wait_ready("radio7", timeout=0))

    def test_cancelled_gate_does_not_start_even_when_all_radios_are_ready(self):
        coordinator = CaptureStartupCoordinator(["radio7"])
        coordinator.mark_ready("radio7")
        coordinator.cancel()
        self.assertFalse(coordinator.wait_ready("radio7", timeout=0))

    def test_owner_authorized_channel_retry_is_preserved(self):
        from modules.capture_services.openwrt.command_runner import RemoteCommandResult
        runner = OpenWrtCommandRunner(SimpleNamespace(connected=True))
        original = RuntimeError("Channel closed")
        result = RemoteCommandResult("takeover_disable", 0)
        with patch.object(runner, "_run", side_effect=[original, result]) as execute, patch("modules.capture_services.openwrt.command_runner.time.sleep"):
            self.assertIs(runner.run("takeover_disable", "uci commit wireless", 10), result)
        self.assertEqual(execute.call_count, 2)

    def test_pending_failure_does_not_release_until_cleanup_finishes(self):
        coordinator = CaptureStartupCoordinator(["radio0", "radio7"])
        coordinator.mark_ready("radio0")
        coordinator.mark_failed("radio7", "preparation failed")
        waiting = threading.Event()
        result = []
        original_wait = coordinator._cond.wait
        def wait(timeout):
            waiting.set()
            return original_wait(timeout)
        with patch.object(coordinator._cond, "wait", side_effect=wait):
            worker = threading.Thread(target=lambda: result.append(coordinator.wait_ready("radio0", timeout=2)))
            worker.start()
            try:
                self.assertTrue(waiting.wait(1))
                self.assertEqual(result, [])
                coordinator.cleanup_finished("radio7", True)
                worker.join(2)
                self.assertFalse(worker.is_alive())
                self.assertEqual(result, [True])
            finally:
                coordinator.cancel()
                worker.join(2)

    def test_unconfirmed_cleanup_blocks_other_lanes(self):
        coordinator = CaptureStartupCoordinator(["radio0", "radio7"])
        coordinator.mark_ready("radio0")
        coordinator.mark_failed("radio7", "preparation failed")
        coordinator.cleanup_finished("radio7", False)
        self.assertFalse(coordinator.wait_ready("radio0", timeout=0))

    def test_disabled_radio_with_inert_interface_is_free_only_with_runtime_evidence(self):
        from modules.capture_services.openwrt.radio_identity import IDENTITY_MARKER
        from modules.capture_services.openwrt.radio_usage import parse_radio_usage
        snapshot = ("wireless.radio7=wifi-device\nwireless.radio7.band='5g'\n"
                    "wireless.radio7.disabled='1'\n" + IDENTITY_MARKER +
                    '{"phys":["phy2"],"inventory_complete":true,"radios":{"radio7":"phy2"}}\n')
        idle = "__RM_USAGE_BEGIN__\nP|phy2\nI|phy2|wlan7|managed|0|0\n__RM_USAGE_END__\n"
        active = idle.replace("|managed|0|0", "|managed|1|1")
        self.assertTrue(parse_radio_usage(snapshot, idle)["radio7"].free)
        self.assertFalse(parse_radio_usage(snapshot, active)["radio7"].free)

    def test_pipeline_gate_rejection_never_starts_engine_and_reports_cleanup(self):
        from pathlib import Path
        from modules.capture_services.openwrt.auto_capture_service import AutoCaptureRadioSession
        from modules.capture_services.openwrt.models import OpenWrtRadioInfo
        from modules.capture_services.openwrt.radio_usage import RadioUsage
        from modules.capture_services.openwrt.command_runner import RemoteCommandResult
        service, client = Mock(), Mock()
        runner = Mock()
        def run(name, *args, **kwargs):
            return RemoteCommandResult(name, 0, "/usr/sbin/hcxdumptool" if name == "find_hcx_bin" else "ABSENT" if name == "sweep_stale" else "")
        runner.run.side_effect = run
        session = AutoCaptureRadioSession(OpenWrtRadioInfo(name="radio7", interface="phy2"), runner, client, Path("."), service)
        gate = Mock(_expected={"radio7", "radio0"})
        gate.wait_ready.return_value = False
        session._coordinator = gate
        with patch("modules.capture_services.openwrt.auto_capture_service.OpenWrtRadioTakeover") as takeover, patch("modules.capture_services.openwrt.auto_capture_service.read_radio_usage", return_value={"radio7": RadioUsage("radio7", "phy2", "idle", "idle")}), patch.object(session, "_cleanup_session", return_value=True) as cleanup:
            takeover.return_value.resolve_phy.return_value = ("phy2", ())
            session._run_capture_pipeline("", False)
        gate.wait_ready.assert_called_once()
        cleanup.assert_called_once()
        gate.cleanup_finished.assert_called_once_with("radio7", True)
        gate.mark_failed.assert_called_once()
        client._client.get_transport.assert_not_called()
