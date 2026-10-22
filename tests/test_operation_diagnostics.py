"""Offline regressions for automatic evidence and export privacy."""
import json
import tempfile
import unittest
import zipfile
from pathlib import Path, PurePosixPath
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from modules.capture_services.openwrt.command_runner import OpenWrtCommandRunner, RemoteCommandResult
from modules.capture_services.openwrt.operation_diagnostics import OperationDiagnostics, sanitized_operation_evidence
from modules.capture_services.openwrt.diagnostic_context import router_context_json
from modules.capture_services.openwrt.models import OpenWrtSessionSnapshot
from modules.capture_services.openwrt.signal_models import RouterSignal, SignalInventory, SignalSnapshot
from modules.capture_services.openwrt.signal_diagnostics import signal_evidence
from modules.display_privacy import masker_for
from modules.log_services import export_logs
from modules.platform_services.windows_wifi_capture import get_connected_wifi_band


class OperationDiagnosticTests(unittest.TestCase):
    def test_cleanup_only_channel_closed_is_exported_without_log_references(self):
        from modules.capture_services.backends.openwrt_backend import OpenWrtCaptureBackend, CaptureCleanupError
        from modules.capture_services.openwrt.workspace import CaptureWorkspaceManager
        backend = OpenWrtCaptureBackend()
        error = RuntimeError("Channel closed. password=must-not-leak")
        error.ssh_stage, error.ssh_kind = "exec_request", "command"
        ssh = NS(connected=True, exec_command=Mock(side_effect=error))
        backend.ssh_client = ssh
        backend.command_runner = OpenWrtCommandRunner(ssh)
        backend.workspace_manager = CaptureWorkspaceManager(ssh, backend.command_runner)
        backend.process_manager = NS(handles=lambda: ())
        backend._restore_task_wireless = Mock(return_value=[])
        backend.reconnect_cleanup = Mock()
        with tempfile.TemporaryDirectory() as folder:
            workspace = NS(local_dir=Path(folder), remote_dir=PurePosixPath("/tmp/winbox-capture/test"), session_id="test")
            with patch("modules.capture_services.local_cache_cleanup.acknowledge_session"):
                with self.assertRaises(CaptureCleanupError):
                    backend._finish_capture_task(workspace)
            backend.reconnect_cleanup.remember.assert_called_once_with(workspace, None)
            evidence = backend.command_runner.operation_diagnostics.snapshot()
            failure = next(r for r in evidence["failures"] if r["event"] == "command")
            self.assertEqual(failure["stage"], "exec_request")
            self.assertEqual(failure["reason"], "channel_closed")
            self.assertTrue(failure["connected_after"])
            summary = next(r for r in evidence["failures"] if r["operation"] == "finish_capture")
            self.assertFalse(summary["primary_failed"])
            self.assertTrue(summary["wireless_cleanup_confirmed"])
            context = router_context_json(OpenWrtSessionSnapshot.initial(), masker_for([]),
                                          operation_evidence=evidence)
            output = Path(folder) / "feedback.zip"
            export_logs(output, "capture", [], router_context=context)
            with zipfile.ZipFile(output) as archive:
                self.assertIsNone(archive.testzip())
                raw = archive.read("operation-diagnostics.json").decode()
                self.assertIn("cleanup_capture_workspace", raw)
                self.assertIn("restore_confirmed", raw)
                self.assertIn("reconnect_recovery_pending", raw)
                self.assertNotIn("must-not-leak", raw)
                index = json.loads(archive.read("diagnostic-index.json"))
                self.assertIn("operation-diagnostics.json", [r["file"] for r in index["attachments"]])

    def test_success_timeout_cancellation_and_nonzero_are_distinct(self):
        for result, status in ((RemoteCommandResult("test", 0), "completed"),
            (RemoteCommandResult("test", 1), "failed"),
            (RemoteCommandResult("test", -1, timed_out=True), "timeout"),
            (RemoteCommandResult("test", -1, cancelled=True), "cancelled")):
            runner = OpenWrtCommandRunner(NS(connected=True))
            runner._run = Mock(return_value=result)
            self.assertIs(runner.run("test", "password=never-store", 1), result)
            event = runner.operation_diagnostics.snapshot()["events"][-1]
            self.assertEqual(event["status"], status)
            self.assertNotIn("never-store", json.dumps(runner.operation_diagnostics.snapshot()))
            self.assertIn("elapsed_seconds", event)

    def test_failure_retained_after_successes_and_bound_to_device(self):
        journal = OperationDiagnostics()
        journal.bind_connection("device-a")
        journal.record("command", operation="cleanup", status="failed")
        for _ in range(300):
            journal.record("command", status="completed", stdout="secret", password="secret")
        data = journal.snapshot()
        self.assertEqual(len(data["events"]), 64)
        self.assertEqual(data["failures"][0]["operation"], "cleanup")
        self.assertNotIn("secret", json.dumps(data))
        journal.bind_connection("device-a")
        self.assertEqual(len(journal.snapshot()["failures"]), 1)
        data["failures"].clear()
        self.assertEqual(len(journal.snapshot()["failures"]), 1)
        journal.bind_connection("device-b")
        self.assertFalse(journal.snapshot()["events"])

    def test_export_rejects_oversized_records_and_drops_unapproved_fields(self):
        masker = masker_for([])
        with self.assertRaises(ValueError):
            sanitized_operation_evidence(dict(schema_version=1, events=[{}]*65), masker)
        clean = sanitized_operation_evidence(dict(schema_version=1,
            events=[dict(event="test", command="secret", stdout="secret", reason="x"*10000,
                         elapsed_seconds=float("inf"))]), masker)
        self.assertNotIn("secret", json.dumps(clean))
        self.assertEqual(len(clean["events"][0]["reason"]), 128)
        self.assertNotIn("elapsed_seconds", clean["events"][0])

    def test_wifi_evidence_is_windows_source_and_has_no_network_name(self):
        journal = OperationDiagnostics()
        report = "Name : WLAN\nState : connected\nSSID : PrivateNetwork\nChannel : 11\n"
        with patch("modules.platform_services.windows_wifi_capture._run_netsh", return_value=report):
            self.assertEqual(get_connected_wifi_band(journal), ("2.4G", "PrivateNetwork", "11"))
        event = journal.snapshot()["events"][-1]
        self.assertEqual((event["source"], event["band"], event["channel"]), ("windows_netsh", "2.4G", 11))
        self.assertNotIn("PrivateNetwork", json.dumps(journal.snapshot()))
        with patch("modules.platform_services.windows_wifi_capture._run_netsh", side_effect=OSError("secret")):
            self.assertEqual(get_connected_wifi_band(journal), ("", "", ""))
        self.assertEqual(journal.snapshot()["events"][-1]["reason"], "probe_failed")

    def test_signal_button_blocker_export_has_no_ssid_or_client_details(self):
        row = RouterSignal("private-section", "PrivateNetwork", "sta", "radio1", "5g", True, True, "inactive")
        snapshot = SignalSnapshot(SignalInventory((row,)), connected=True, stale=False)
        evidence = signal_evidence(snapshot)
        self.assertEqual(evidence["rows"][0]["blocked_reason"], "parent_disabled")
        context = router_context_json(OpenWrtSessionSnapshot.initial(), masker_for([]), signal_snapshot=snapshot)
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "feedback.zip"
            export_logs(output, "signal", [], router_context=context)
            with zipfile.ZipFile(output) as archive:
                raw = archive.read("signal-state.json").decode()
                self.assertIn("parent_disabled", raw)
                self.assertNotIn("PrivateNetwork", raw)
                self.assertNotIn("private-section", raw)
