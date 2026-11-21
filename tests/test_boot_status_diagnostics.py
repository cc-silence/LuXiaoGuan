"""Read-only boot policy reasons survive the real diagnostics export."""
import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from modules.capture_services.management_diagnostics import ManagementTrace
from modules.capture_services.openwrt.command_runner import RemoteCommandResult
from modules.capture_services.openwrt.operation_diagnostics import OperationDiagnostics, sanitized_operation_evidence
from modules.capture_services.openwrt.router_boot_status import read_boot_status
from modules.capture_services.openwrt.router_policy_plan import boot_state_reason
from modules.display_privacy import masker_for


class BootStatusDiagnosticsTests(unittest.TestCase):
    def test_each_reason_is_recorded_and_exported_without_raw_output(self):
        reasons = ("old_watch", "script_symlink", "missing_script_link", "hash_unavailable", "hash_unrecognized", "invalid_link")
        for reason in reasons:
            with self.subTest(reason=reason):
                ssh=SimpleNamespace(_client=object(),host_fingerprint="pin",connected=True)
                runner=Mock()
                runner.operation_diagnostics=OperationDiagnostics()
                runner.operation_diagnostics.bind_connection("device")
                runner.run.return_value=RemoteCommandResult("probe",0,"unknown:"+reason)
                manager=SimpleNamespace(ssh=ssh,runner=runner)
                trace=ManagementTrace(runner,"policy_status",{})
                result=trace.execute(lambda e,r:read_boot_status(manager,e),threading.Event(),Mock())
                self.assertEqual(result,dict(boot_state=None,boot_reason=reason))
                data=sanitized_operation_evidence(runner.operation_diagnostics.snapshot(),masker_for([]))
                self.assertEqual(data["management"][-1]["boot_reason"],reason)
                self.assertNotIn("unknown:",json.dumps(data))

    def test_unrecognized_output_is_never_recorded(self):
        ssh=SimpleNamespace(_client=object(),host_fingerprint="pin",connected=True)
        runner=Mock();runner.run.return_value=RemoteCommandResult("probe",0,"unknown:private-secret")
        result=read_boot_status(SimpleNamespace(ssh=ssh,runner=runner),threading.Event())
        self.assertEqual(result,dict(boot_state=None,boot_reason="probe_unexpected"))

    def test_snapshot_reasons_preserve_unknown_safety_gate(self):
        snapshot={"capabilities":{"old_watch":True},"files":{}}
        self.assertEqual(boot_state_reason(snapshot),"old_watch")
        snapshot["capabilities"]["old_watch"]=False
        snapshot["files"]["/etc/init.d/luxiaoguan-wifi-policy"]="f"*64
        self.assertEqual(boot_state_reason(snapshot),"hash_unrecognized")
