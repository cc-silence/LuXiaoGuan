"""Exportable, bounded and secret-free native-control failure evidence."""
import json,unittest
from threading import Event
from unittest.mock import Mock,patch
from modules.capture_services.openwrt.password_control import PasswordControl,ControlUnavailable,ControlRequestTimeout,open_control
from modules.capture_services.openwrt.operation_diagnostics import OperationDiagnostics,sanitized_operation_evidence
from modules.capture_services.openwrt.command_runner import RemoteCommandResult
from modules.display_privacy import masker_for
from tests.test_password_control import Protocol

SECRET="NEVER-EXPORT-CREDENTIAL-FIXTURE"

class PasswordDiagnosticTests(unittest.TestCase):
    def setup_channel(self,channel=None):
        channel=channel or Protocol();channel.cancel=Event()
        ssh=Mock();ssh.exec_command.return_value=(Mock(),Mock(),Mock(),channel)
        ssh.open_control_stream.return_value=channel
        return ssh,channel,OperationDiagnostics()

    def failures(self,journal,stage):
        return [r for r in journal.snapshot()["failures"] if r.get("stage")==stage]

    def test_upstream_daemon_connection_messages_keep_categories_only(self):
        from modules.capture_services.openwrt.password_control_diagnostics import ControlEvidence
        import time
        for message in ("Could not connect to interface '"+SECRET+"' - re-trying",
                        "Connection to wpa_supplicant lost - trying to reconnect"):
            evidence=ControlEvidence(None,"pipe",time.monotonic)
            evidence.observe(message)
            self.assertEqual(evidence.fields()["control_observation"],"daemon_unreachable")
            self.assertNotIn(SECRET,repr(vars(evidence)))

    def test_completed_initialization_records_each_phase(self):
        ssh,channel,journal=self.setup_channel()
        control=PasswordControl(ssh,"owned",channel.cancel,journal=journal)
        try:
            complete={r["stage"] for r in journal.snapshot()["workflows"] if r.get("status")=="completed"}
            self.assertTrue({"control_open","control_ping","control_help","control_cache","control_disconnect"}<=complete)
            final=next(r for r in journal.snapshot()["workflows"] if r.get("stage")=="control_disconnect" and r.get("status")=="completed")
            self.assertEqual(final["control_request"],"disconnect")
            self.assertEqual(final["control_reply"],"ok")
        finally:control.close()

    def test_missing_cache_capability_is_specific_without_raw_help(self):
        ssh,channel,journal=self.setup_channel()
        send=channel.sendall
        def unavailable(data):
            send(data)
            channel.output=channel.output.replace(b"pmksa_flush = flush PMKSA cache",SECRET.encode())
        channel.sendall=unavailable
        with self.assertRaises(ControlUnavailable):
            PasswordControl(ssh,"owned",channel.cancel,journal=journal)
        event=self.failures(journal,"control_help")[-1]
        self.assertEqual(event["reason"],"native_cache_unsupported")
        self.assertFalse(event["cache_control_supported"])
        self.assertEqual(event["control_reply_rows"],1)
        self.assertNotIn(SECRET,json.dumps(journal.snapshot()))

    def test_rejected_cache_command_preserves_safe_reply_category(self):
        ssh,channel,journal=self.setup_channel()
        original=PasswordControl._request
        def request(control,command,timeout=5):
            if command=="pmksa_flush":return ["FAIL"]
            return original(control,command,timeout)
        with patch.object(PasswordControl,"_request",request),self.assertRaises(ControlUnavailable):
            PasswordControl(ssh,"owned",channel.cancel,journal=journal)
        event=self.failures(journal,"control_cache")[-1]
        self.assertEqual(event["control_reply"],"fail")
        self.assertEqual(event["reason"],"native_control_rejected")

    def test_disconnect_timeout_includes_partial_reply_and_no_error_text(self):
        ssh,channel,journal=self.setup_channel()
        original=PasswordControl._request
        def request(control,command,timeout=5):
            if command=="disconnect":
                control._evidence.partial("OK")
                raise TimeoutError(SECRET)
            return original(control,command,timeout)
        with patch.object(PasswordControl,"_request",request),self.assertRaises(ControlRequestTimeout):
            PasswordControl(ssh,"owned",channel.cancel,journal=journal)
        event=self.failures(journal,"control_disconnect")[-1]
        self.assertEqual(event["control_request"],"disconnect")
        self.assertEqual(event["control_reply"],"ok")
        self.assertEqual(event["control_reply_rows"],1)
        self.assertEqual(event["reason"],"native_request_timeout")
        self.assertNotIn(SECRET,json.dumps(journal.snapshot()))

    def test_terminal_setup_exit_is_distinct_from_daemon_timeout(self):
        channel=Mock();channel.closed=False;channel.recv_ready.return_value=False
        channel.exit_status_ready.return_value=True;channel.recv_exit_status.return_value=126
        ssh,channel,journal=self.setup_channel(channel)
        with self.assertRaises(ConnectionError):
            PasswordControl(ssh,"owned",Event(),journal=journal,transport="pty")
        event=self.failures(journal,"control_ping")[-1]
        self.assertEqual(event["reason"],"native_terminal_setup_unavailable")
        self.assertEqual(event["control_exit_code"],126)
        self.assertEqual(event["control_transport"],"pty")

    def test_runtime_flags_survive_feedback_export(self):
        runner=Mock();runner.operation_diagnostics=OperationDiagnostics()
        runner.run.return_value=RemoteCommandResult("probe",0,json.dumps({
            "radio1":{"up":False,"pending":True,"disabled":False,
                      "interfaces":[{"section":"owned","ifname":"phy1-sta0"}]}}))
        control=Mock()
        with patch("modules.capture_services.openwrt.password_control.PasswordControl",return_value=control):
            self.assertIs(open_control(runner,"owned",None),control)
        data=sanitized_operation_evidence(runner.operation_diagnostics.snapshot(),masker_for([]))
        event=next(r for r in data["workflows"] if r.get("stage")=="control_identity")
        self.assertEqual(event["runtime_match_count"],1)
        self.assertTrue(event["runtime_radio_pending"])
        self.assertFalse(event["runtime_radio_up"])
        self.assertNotIn("phy1-sta0",json.dumps(data))

    def test_control_fields_survive_export_and_ring_buffer_is_bounded(self):
        ssh,channel,journal=self.setup_channel()
        control=PasswordControl(ssh,"owned",channel.cancel,journal=journal)
        try:
            for _ in range(40):
                control._evidence.begin("control_cache")
                control._evidence.requested("set_network 7 psk "+SECRET)
                control._evidence.response(["FAIL"])
                control._evidence.failed(RuntimeError(SECRET),None)
            exported=sanitized_operation_evidence(journal.snapshot(),masker_for([SECRET]))
            event=exported["failures"][-1]
            self.assertEqual(event["stage"],"control_cache")
            self.assertEqual(event["control_request"],"set_network")
            self.assertEqual(event["control_reply"],"fail")
            self.assertLessEqual(len(exported["workflows"]),32)
            self.assertLessEqual(len(exported["failures"]),8)
            self.assertNotIn(SECRET,json.dumps(exported))
        finally:control.close()

if __name__=="__main__":unittest.main()
