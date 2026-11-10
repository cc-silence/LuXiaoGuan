"""Regression tests for bridge scan failure diagnostics."""
import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from modules.capture_services.openwrt.operation_diagnostics import OperationDiagnostics, sanitized_operation_evidence
from modules.capture_services.openwrt.scan_failure_evidence import record_scan_failure
from modules.capture_services.openwrt.signal_manager import SignalError
from modules.capture_services.openwrt.wireless_setup_manager import WirelessSetupManager
from modules.capture_services.openwrt.wireless_setup_models import WirelessEnvironment
from modules.log_history import HistoryMasker


class ScanFailureEvidenceTests(unittest.TestCase):
    def test_failure_and_export_include_safe_context_and_rpc_code(self):
        journal = OperationDiagnostics()
        runner = SimpleNamespace(operation_diagnostics=journal)
        radio = {"name": "radio1", "phy": "phy1", "hwmode": "11a", "interfaces": ["wlan1"]}
        env = {"radios": [radio], "rows": [
            {"mode": "sta", "device": "radio1", "section": "s1", "ssid": "PRIVATE_SECRET"},
            {"mode": "sta", "device": "radio1", "section": "s2"}], "active": ["s1"]}
        error = SignalError("rpc_iwinfo_scan_7", "PRIVATE_SECRET")
        error.scan_interface = "wlan1-actual"
        manager = WirelessSetupManager(runner)
        manager.inspect = Mock(return_value=WirelessEnvironment(data=env))
        manager._call = Mock(side_effect=error)
        with self.assertRaises(SignalError):
            manager.scan(5, threading.Event(), Mock(), radio="radio1")
        exported = sanitized_operation_evidence(journal.snapshot(), HistoryMasker())
        row = next(row for row in exported["failures"] if row.get("operation") == "wireless_scan_failure")
        self.assertEqual((row["scan_radio"], row["scan_interface"], row["band"]), ("radio1", "wlan1-actual", "5g"))
        self.assertEqual((row["scan_error"], row["native_error"], row["bridge_active_count"]), ("rpc_iwinfo_scan", 7, 1))
        self.assertNotIn("PRIVATE_SECRET", json.dumps(exported))

    def test_cancel_does_not_create_failure_diagnostic(self):
        journal = OperationDiagnostics()
        manager = WirelessSetupManager(SimpleNamespace(operation_diagnostics=journal))
        radio = {"name": "radio1", "phy": "phy1", "interfaces": ["wlan1"]}
        manager.inspect = Mock(return_value=WirelessEnvironment(data={"radios": [radio]}))
        manager._call = Mock(side_effect=SignalError("cancelled", "cancelled"))
        with self.assertRaises(SignalError):
            manager.scan(5, threading.Event(), Mock(), radio="radio1")
        self.assertFalse(any(row.get("operation") == "wireless_scan_failure" for row in journal.snapshot()["failures"]))

    def test_temporary_error_and_unknown_values(self):
        journal = OperationDiagnostics()
        error = SignalError("scan_native", "secret")
        error.native_error = 7
        record_scan_failure(SimpleNamespace(operation_diagnostics=journal), {},
                            {"name": "radio0", "band": "2g"}, "", error)
        row = journal.snapshot()["failures"][-1]
        self.assertTrue(row["scan_temporary"])
        self.assertEqual((row["scan_error"], row["native_error"]), ("scan_native", 7))
        self.assertNotIn("bridge_active_count", row)

    def test_call_preserves_structured_native_error(self):
        manager = WirelessSetupManager(None)
        manager.signal._execute = Mock(return_value=json.dumps({"ok": False, "code": "scan_native", "native_error": 7}))
        with self.assertRaises(SignalError) as raised:
            manager._call("scan", threading.Event(), radio="radio0", interface="")
        self.assertEqual(raised.exception.native_error, 7)


if __name__ == "__main__":
    unittest.main()
