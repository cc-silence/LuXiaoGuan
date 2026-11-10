"""Verified capture-channel reopen after a refused exec request, plus its evidence."""
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from paramiko.ssh_exception import SSHException

from modules.capture_services.openwrt.auto_capture_service import AutoCaptureRadioSession
from modules.capture_services.openwrt.command_runner import RemoteCommandResult
from modules.capture_services.openwrt.models import OpenWrtRadioInfo


def refused(stage="exec_request", message="Channel closed."):
    """Paramiko raises this both for a close and for SSH_MSG_CHANNEL_FAILURE."""
    error = SSHException(message)
    error.ssh_stage = stage
    error.ssh_kind = "capture"
    return error


class CaptureChannelRecoveryTests(unittest.TestCase):
    def build(self):
        service = Mock()
        runner = Mock()
        ssh_client = Mock()
        session = AutoCaptureRadioSession(
            OpenWrtRadioInfo(name="radio0", interface="phy0"), runner, ssh_client,
            Path("."), service)
        session.remote_cap_file = "/root/cap/hcxmon0_20261107_085929.pcapng"
        return session, runner, ssh_client, service

    def test_exec_refusal_reopens_once_after_verified_stop_and_reset(self):
        session, runner, ssh_client, service = self.build()
        transport_client, channel = Mock(), Mock()
        runner.run.side_effect = lambda name, *args, **kwargs: RemoteCommandResult(
            name, 0, {"reset_iface": "READY", "scan_capture_process": "ABSENT"}
            .get(name, "PROCESS_STOPPED"))
        ssh_client._client = transport_client
        ssh_client.connected = True
        ssh_client.open_capture_stream.side_effect = [refused(), channel]

        self.assertIs(session._open_capture_or_recover(transport_client, "capture-cmd"), channel)
        self.assertEqual(ssh_client.open_capture_stream.call_count, 2)
        self.assertEqual([call.args[0] for call in runner.run.call_args_list],
                         ["recover_capture_channel", "scan_capture_process", "reset_iface"])
        self.assertEqual(ssh_client.open_capture_stream.call_args.args[1], "capture-cmd")
        messages = [str(call.args[1]) for call in service.radioStreamOutput.emit.call_args_list]
        self.assertTrue(any("通道恢复" in text for text in messages))
        self.assertTrue(any("已确认无残留进程" in text for text in messages))

    def test_surviving_capture_process_blocks_the_reopen(self):
        session, runner, ssh_client, _ = self.build()
        transport_client = Mock()
        runner.run.side_effect = lambda name, *args, **kwargs: RemoteCommandResult(
            name, 0, {"scan_capture_process": "PRESENT"}.get(name, "PROCESS_STOPPED"))
        ssh_client._client = transport_client
        ssh_client.connected = True
        ssh_client.open_capture_stream.side_effect = refused()

        with self.assertRaises(RuntimeError) as caught:
            session._open_capture_or_recover(transport_client, "capture-cmd")
        self.assertIn("Channel closed.", str(caught.exception))
        self.assertEqual(ssh_client.open_capture_stream.call_count, 1)
        self.assertEqual([call.args[0] for call in runner.run.call_args_list],
                         ["recover_capture_channel", "scan_capture_process"])

    def test_unconfirmed_process_identity_keeps_original_error_and_skips_reopen(self):
        session, runner, ssh_client, _ = self.build()
        transport_client = Mock()
        runner.run.return_value = RemoteCommandResult(
            "recover_capture_channel", 47, "", "PROCESS_IDENTITY_UNCONFIRMED")
        ssh_client._client = transport_client
        ssh_client.connected = True
        ssh_client.open_capture_stream.side_effect = refused()

        with self.assertRaises(RuntimeError) as caught:
            session._open_capture_or_recover(transport_client, "capture-cmd")
        self.assertIn("exec_request", str(caught.exception))
        self.assertIn("Channel closed.", str(caught.exception))
        self.assertEqual(ssh_client.open_capture_stream.call_count, 1)
        self.assertEqual([call.args[0] for call in runner.run.call_args_list],
                         ["recover_capture_channel"])

    def test_second_refusal_reports_retry_stage_without_a_third_attempt(self):
        session, runner, ssh_client, service = self.build()
        transport_client = Mock()
        runner.run.side_effect = lambda name, *args, **kwargs: RemoteCommandResult(
            name, 0, {"reset_iface": "READY", "scan_capture_process": "ABSENT"}
            .get(name, "PROCESS_STOPPED"))
        ssh_client._client = transport_client
        ssh_client.connected = True
        ssh_client.open_capture_stream.side_effect = [
            refused(), refused(message="Channel is not open.")]

        with self.assertRaises(RuntimeError) as caught:
            session._open_capture_or_recover(transport_client, "capture-cmd")
        self.assertIn("Channel is not open.", str(caught.exception))
        self.assertEqual(ssh_client.open_capture_stream.call_count, 2)

    def test_channel_failure_before_command_submission_is_not_retried_here(self):
        session, runner, ssh_client, _ = self.build()
        transport_client = Mock()
        ssh_client._client = transport_client
        ssh_client.connected = True
        ssh_client.open_capture_stream.side_effect = refused("pty_request")

        with self.assertRaises(RuntimeError) as caught:
            session._open_capture_or_recover(transport_client, "capture-cmd")
        self.assertIn("pty_request", str(caught.exception))
        self.assertEqual(ssh_client.open_capture_stream.call_count, 1)
        runner.run.assert_not_called()

    def test_stop_request_wins_over_the_retry(self):
        session, runner, ssh_client, _ = self.build()
        transport_client = Mock()
        ssh_client._client = transport_client
        ssh_client.connected = True
        ssh_client.open_capture_stream.side_effect = refused()
        session._stop_event.set()

        with self.assertRaises(RuntimeError):
            session._open_capture_or_recover(transport_client, "capture-cmd")
        self.assertEqual(ssh_client.open_capture_stream.call_count, 1)
        runner.run.assert_not_called()


class CleanupCommandTests(unittest.TestCase):
    def test_stop_and_scan_recognise_the_compatibility_build(self):
        from modules.capture_services.openwrt.auto_capture_cleanup import (
            capture_process_scan_command, owned_capture_stop_command)
        stop = owned_capture_stop_command("hcxmon0")
        scan = capture_process_scan_command("hcxmon0")
        for command in (stop, scan):
            self.assertIn("hcxdumptool|hcx-ifindex1", command)
            self.assertIn("hcxmon0", command)
        self.assertIn("--interface=", scan)

    def test_invalid_interface_names_are_rejected(self):
        from modules.capture_services.openwrt.auto_capture_cleanup import (
            capture_process_scan_command, owned_capture_stop_command)
        for builder in (owned_capture_stop_command, capture_process_scan_command):
            with self.assertRaises(ValueError):
                builder("wlan0")

    def test_preflight_sweep_recognises_the_compatibility_build(self):
        from modules.capture_services.openwrt.auto_capture_cleanup import (
            sweep_stale_monitors_command)
        sweep = sweep_stale_monitors_command(["hcxmon0"])
        self.assertIn("hcxdumptool|hcx-ifindex1", sweep)
        self.assertNotIn('= "hcxdumptool"', sweep)
        self.assertIn("--interface=$d", sweep)


class InlineSweepTests(unittest.TestCase):
    def test_inline_sweep_uses_the_shared_process_names(self):
        from modules.capture_services.openwrt.auto_capture_cleanup import (
            CAPTURE_PROCESS_NAMES)
        service = Mock()
        session = AutoCaptureRadioSession(
            OpenWrtRadioInfo(name="radio0", interface="phy0"), Mock(), Mock(),
            Path("."), service)
        script = session._build_sweep_stale_script()
        self.assertIn("grep -E '" + "|".join(CAPTURE_PROCESS_NAMES) + "'", script)
        self.assertIn(session.mon_iface, script)
        self.assertIn("wiphy " + session.phy[3:], script)


class TransportEvidenceTests(unittest.TestCase):
    SCENE = "\n".join([
        "__RM_TRANSPORT_BEGIN__",
        "UPTIME 501",
        "MEMFREE 123456",
        "MEMAVAIL 234567",
        "LOGREAD 1",
        "LOG authpriv.info dropbear[412]: Child connection from 192.168.1.50:60412",
        "LOG authpriv.warn dropbear[412]: password=supersecret rejected",
        "__RM_TRANSPORT_END__",
    ])

    def runner(self, journal, stdout):
        return SimpleNamespace(
            operation_diagnostics=journal,
            ssh_client=SimpleNamespace(connected=True),
            run=lambda *args, **kwargs: RemoteCommandResult(
                "capture_transport_scene", 0, stdout),
        )

    def test_scene_is_bounded_masked_and_exported(self):
        from modules.capture_services.openwrt.capture_transport_evidence import (
            collect_transport_failure)
        from modules.capture_services.openwrt.operation_diagnostics import (
            OperationDiagnostics, sanitized_operation_evidence)
        from modules.log_history import HistoryMasker

        journal = OperationDiagnostics()
        collect_transport_failure(self.runner(journal, self.SCENE), "radio0", "exec_request")
        row = journal.snapshot()["capture_transport_failures"][0]
        self.assertEqual(row["collection_status"], "completed")
        self.assertEqual(row["stage"], "exec_request")
        self.assertEqual(row["uptime_seconds"], 501)
        self.assertEqual(row["mem_free_kb"], 123456)
        self.assertEqual(row["mem_available_kb"], 234567)
        self.assertIs(row["logread"], True)
        text = "\n".join(row["service_log"])
        self.assertNotIn("192.168.1.50", text)
        self.assertNotIn("supersecret", text)

        exported = sanitized_operation_evidence(journal.snapshot(), HistoryMasker())
        self.assertEqual(len(exported["capture_transport_failures"]), 1)
        self.assertEqual(exported["capture_transport_failures"][0]["stage"], "exec_request")

    def test_malformed_scene_is_recorded_as_failed_without_free_text(self):
        from modules.capture_services.openwrt.capture_transport_evidence import (
            collect_transport_failure)
        from modules.capture_services.openwrt.operation_diagnostics import OperationDiagnostics

        journal = OperationDiagnostics()
        collect_transport_failure(self.runner(journal, "not-a-scene"), "radio0", "exec_request")
        row = journal.snapshot()["capture_transport_failures"][0]
        self.assertEqual(row["collection_status"], "failed")
        self.assertEqual(row["service_log"], [])
        self.assertNotIn("uptime_seconds", row)

    def test_retention_and_identity_change_bound_the_scene(self):
        from modules.capture_services.openwrt.operation_diagnostics import OperationDiagnostics

        journal = OperationDiagnostics()
        journal.bind_connection("device-a")
        for _ in range(10):
            journal.transport_failure({"radio": "radio0", "stage": "exec_request",
                                       "collection_status": "completed", "logread": False})
        self.assertEqual(len(journal.snapshot()["capture_transport_failures"]), 8)
        journal.bind_connection("device-b")
        self.assertEqual(journal.snapshot()["capture_transport_failures"], [])


if __name__ == "__main__":
    unittest.main()
