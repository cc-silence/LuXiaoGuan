"""Exercise the real Lua scan path after a radio scan timeout."""
import copy
import threading
import unittest
from unittest.mock import Mock, patch
from tests.test_wireless_setup import LuaDevice, LuaRuntime
from modules.capture_services.openwrt.wireless_setup_manager import WirelessSetupManager
from modules.capture_services.openwrt.wireless_setup_models import WirelessEnvironment
from modules.capture_services.openwrt.signal_manager import SignalError


@unittest.skipUnless(LuaRuntime, "Lua runtime unavailable")
class BridgeTimeoutFallbackTests(unittest.TestCase):
    def device(self, failure=7):
        class Device(LuaDevice):
            def __init__(self):
                super().__init__()
                self.targets = []
                self.now = 0
                self.config["wireless"]["home"]["disabled"] = "1"
                self.config["wireless"]["upstream"] = {".type": "wifi-iface", "device": "radio7", "mode": "sta"}
            def call(self, obj, method, args):
                data = self.unwrap(args)
                if obj == "iwinfo" and method == "scan":
                    self.targets.append(data["device"])
                    self.now = 10
                    if data["device"] == "radio7":
                        return None, failure
                return super().call(obj, method, args)
        return Device()

    def run_scan(self, device):
        manager = WirelessSetupManager(device)
        env = {"radios": [{"name": "radio7", "phy": "phy2", "band": "5g", "interfaces": ["iface-upstream"]}],
               "rows": [{"mode": "sta", "device": "radio7", "section": "upstream"}], "active": ["upstream"]}
        manager.inspect = Mock(return_value=WirelessEnvironment(data=env))
        cancel = Mock(is_set=Mock(return_value=False), wait=Mock(return_value=False))
        with patch("modules.capture_services.openwrt.wireless_setup_manager.time.monotonic", side_effect=lambda: device.now):
            return manager.scan(5, cancel, Mock(), radio="radio7")

    def test_timeout_retries_only_verified_station_without_changing_config(self):
        device = self.device()
        before = copy.deepcopy(device.config)
        rows = self.run_scan(device)
        self.assertEqual(device.targets, ["radio7", "iface-upstream"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(device.config, before)
        self.assertNotIn(("uci", "apply"), device.calls)
        self.assertIn(("iwinfo", "info"), device.calls)

    def test_fallback_failure_stops_retrying_and_is_logged(self):
        from types import SimpleNamespace
        from modules.capture_services.openwrt.operation_diagnostics import OperationDiagnostics, sanitized_operation_evidence
        from modules.log_history import HistoryMasker
        journal = OperationDiagnostics()
        manager = WirelessSetupManager(SimpleNamespace(operation_diagnostics=journal))
        radio = {"name": "radio1", "phy": "phy1", "interfaces": ["wlan1"]}
        manager.inspect = Mock(return_value=WirelessEnvironment(data={"radios": [radio]}))
        timeout = SignalError("rpc_iwinfo_scan_7", "sensitive detail")
        timeout.scan_target = "radio"
        manager._call = Mock(side_effect=[timeout, SignalError("rpc_iwinfo_info_7", "sensitive detail")])
        cancel = Mock(is_set=Mock(return_value=False), wait=Mock(return_value=False))
        with patch("modules.capture_services.openwrt.wireless_setup_manager.time.monotonic", return_value=0):
            with self.assertRaises(SignalError):
                manager.scan(30, cancel, Mock(), radio="radio1")
        self.assertEqual(manager._call.call_count, 2)
        cancel.wait.assert_not_called()
        evidence = sanitized_operation_evidence(journal.snapshot(), HistoryMasker())
        rows = [r for r in evidence["workflows"] if r.get("operation") == "wireless_scan_fallback"]
        self.assertTrue(any(r.get("status") == "started" for r in rows))
        failure = next(r for r in rows if r.get("status") == "failed")
        self.assertEqual((failure.get("stage"), failure.get("scan_error"), failure.get("native_error")),
                         ("preflight", "rpc_iwinfo_info", 7))
        self.assertNotIn("sensitive detail", repr(evidence))

    def test_fallback_success_logs_interface_and_result_count(self):
        from modules.capture_services.openwrt.operation_diagnostics import OperationDiagnostics, sanitized_operation_evidence
        from modules.log_history import HistoryMasker
        device = self.device()
        device.operation_diagnostics = OperationDiagnostics()
        self.run_scan(device)
        evidence = sanitized_operation_evidence(device.operation_diagnostics.snapshot(), HistoryMasker())
        row = next(r for r in evidence["workflows"] if r.get("operation") == "wireless_scan_fallback" and r.get("status") == "completed")
        self.assertEqual((row.get("scan_interface"), row.get("result_rows")), ("iface-upstream", 1))

    def test_other_error_does_not_retry(self):
        device = self.device(failure=6)
        with self.assertRaises(SignalError):
            self.run_scan(device)
        self.assertEqual(device.targets, ["radio7"])

    def test_unresponsive_iwinfo_never_starts_fallback_scan(self):
        device = self.device()
        original = device.call
        def call(obj, method, args):
            if obj == "iwinfo" and method == "info":
                return None, 7
            return original(obj, method, args)
        device.call = call
        device.lua.globals().bridge_call = call
        with self.assertRaises(SignalError):
            self.run_scan(device)
        self.assertEqual(device.targets, ["radio7"])

    def test_missing_station_never_uses_fallback(self):
        device = self.device()
        device.config["wireless"]["upstream"]["disabled"] = "1"
        manager = WirelessSetupManager(device)
        with self.assertRaises(SignalError):
            manager._call("scan", threading.Event(), radio="radio7", interface="iface-upstream", compat_interface=True)
        self.assertEqual(device.targets, [])

    def test_ambiguous_stations_never_use_fallback(self):
        device = self.device()
        device.config["wireless"]["upstream2"] = {".type": "wifi-iface", "device": "radio7", "mode": "sta"}
        manager = WirelessSetupManager(device)
        with self.assertRaises(SignalError):
            manager._call("scan", threading.Event(), radio="radio7", interface="iface-upstream", compat_interface=True)
        self.assertEqual(device.targets, [])


if __name__ == "__main__":
    unittest.main()
