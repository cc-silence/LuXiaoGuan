"""Automatic AP evidence reaches feedback ZIPs without manual router commands."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
import zipfile

from tests.test_ap_kernel_readiness import KernelApDevice
from tests.test_wireless_setup import LuaRuntime
from modules.capture_services.openwrt.signal_commands import signal_command
from modules.capture_services.openwrt.wireless_status_commands import STATUS_LUA
from modules.capture_services.openwrt.signal_manager import RouterSignalManager, SignalError
from modules.capture_services.openwrt.signal_models import SignalInventory, SignalSnapshot, same_signal_configuration
from modules.capture_services.openwrt.wireless_status_models import parse_wireless_status
from modules.capture_services.openwrt.wireless_runtime_observations import merge_runtime
from modules.capture_services.openwrt.operation_diagnostics import OperationDiagnostics
from modules.capture_services.openwrt.readiness_diagnostics import readiness_evidence
from modules.capture_services.openwrt.diagnostic_context import router_context_json
from modules.capture_services.openwrt.models import OpenWrtSessionSnapshot
from modules.display_privacy import masker_for
from modules.log_services import export_logs


@unittest.skipUnless(LuaRuntime, "Lua unavailable")
class AutomaticApDiagnosticsTests(unittest.TestCase):
    def inventory_data(self, device):
        command=signal_command("read")
        body=command.split("<<'WINBOX_SIGNAL_LUA'\n",1)[1].rsplit("\nWINBOX_SIGNAL_LUA",1)[0]
        device._output=[]
        device.lua.globals().arg=device.wrap({1:json.dumps(dict(action="read"))})
        device.lua.execute(body)
        return json.loads("".join(device._output))["result"]

    def manager(self, device):
        journal=OperationDiagnostics()
        manager=RouterSignalManager(NS(operation_diagnostics=journal))
        def run(action,event,**values):
            if action=="change":
                device.config["wireless"][values["section"]]["disabled"]="0" if values["enabled"] else "1"
                return {"committed":True}
            return self.inventory_data(device)
        manager._run=Mock(side_effect=run)
        event=Mock(spec=threading.Event)
        event.is_set.return_value=False;event.wait.return_value=False
        return manager,journal,event

    def test_inventory_preserves_exact_kernel_rejection_and_verdict(self):
        for field,value,cause in (("kernel_carrier","0","kernel_carrier"),
                                  ("kernel_flags","0x1002","kernel_down"),
                                  ("kernel_type","managed","kernel_mode"),
                                  ("kernel_ssid","Different","kernel_ssid")):
            with self.subTest(cause=cause):
                device=KernelApDevice();setattr(device,field,value)
                row=self.inventory_data(device)["rows"][0]
                self.assertEqual(row["running"],"inactive")
                self.assertEqual(row["readiness"]["ap_failure"],cause)
                self.assertEqual(row["readiness"]["native_error"],4)
                self.assertTrue(row["readiness"]["interface_up"])
                self.assertNotIn("Home",json.dumps(row["readiness"]))

    def test_direct_enable_failure_and_export_keep_final_evidence_after_later_refresh(self):
        device=KernelApDevice();device.kernel_carrier="0"
        device.config["wireless"]["home"]["disabled"]="1"
        manager,journal,event=self.manager(device)
        before=manager.read(event)
        from modules.capture_services.openwrt.signal_recovery import SIGNAL_READY_SECONDS
        now = [0.0]
        def advance_wait(_seconds):
            now[0] += SIGNAL_READY_SECONDS + 1
            return False
        event.wait.side_effect = advance_wait
        with patch("modules.capture_services.openwrt.signal_manager.time.monotonic", side_effect=lambda: now[0]):
            with self.assertRaises(SignalError):manager.change(before,"home",True,event)
        failure=journal.snapshot()["configuration_failures"][-1]
        self.assertEqual(failure["ap_failure"],"kernel_carrier")
        self.assertEqual(failure["native_error"],4)
        self.assertFalse(failure["ap_carrier"])
        device.kernel_carrier="1"
        current=manager.read(event)
        self.assertEqual(current.rows[0].running,"active")
        snapshot=SignalSnapshot(current,connected=True,stale=False)
        context=router_context_json(OpenWrtSessionSnapshot.initial(),masker_for([]),
            operation_evidence=journal.snapshot(),signal_snapshot=snapshot)
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/"feedback.zip"
            export_logs(output,"signal",[],router_context=context)
            with zipfile.ZipFile(output) as archive:
                failures=json.loads(archive.read("operation-diagnostics.json"))["configuration_failures"]
                latest=json.loads(archive.read("signal-state.json"))["rows"][0]["readiness"]
                self.assertEqual(failures[-1]["ap_failure"],"kernel_carrier")
                self.assertEqual(latest["ap_failure"],"none")
                raw=archive.read("operation-diagnostics.json")+archive.read("signal-state.json")
                self.assertNotIn(b"original-password",raw)
                self.assertNotIn(b"Home",raw)

    def test_repair_failure_retains_own_trace_without_extra_reloads(self):
        device=KernelApDevice();device.kernel_carrier="0"
        manager,journal,event=self.manager(device)
        manager._run_command=Mock(return_value=NS(cancelled=False,timed_out=False,exit_code=0))
        clock=[0.0]
        event.wait.side_effect=lambda seconds:clock.__setitem__(0,clock[0]+seconds) or False
        with patch("time.monotonic",side_effect=lambda:clock[0]):
            with self.assertRaises(SignalError):manager.repair(manager.read(event),"home",event)
        failure=journal.snapshot()["configuration_failures"][-1]
        self.assertEqual((failure["source"],failure["ap_failure"]),("repair","kernel_carrier"))
        self.assertEqual(failure["verify_attempts"],31)
        self.assertEqual(failure["elapsed_seconds"],30)
        manager._run_command.assert_called_once()

    def test_native_periodic_refresh_keeps_probe_evidence_and_configuration_identity(self):
        device=KernelApDevice();device.kernel_carrier="0"
        manager,journal,event=self.manager(device)
        before=manager.read(event)
        device._output=[];device.lua.execute(STATUS_LUA)
        raw=json.loads("".join(device._output))
        self.assertTrue(raw["ok"])
        sample=parse_wireless_status(raw["result"])
        self.assertEqual(sample.runtime[0].readiness["ap_failure"],"kernel_carrier")
        device.kernel_carrier="1"
        device._output=[];device.lua.execute(STATUS_LUA)
        sample=parse_wireless_status(json.loads("".join(device._output))["result"])
        after=merge_runtime(before,sample.runtime)
        self.assertTrue(same_signal_configuration(before,after))
        self.assertEqual(after.rows[0].readiness["ap_failure"],"none")
        self.assertTrue(after.rows[0].readiness["kernel_ap_started"])

    def test_failure_reason_and_native_dfs_fields_survive_snapshot(self):
        device=KernelApDevice();original=device.call
        def call(obj,method,args):
            if obj.startswith("hostapd.") and method=="get_status":
                return device.wrap(dict(status="DFS",freq=5500,channel=100,ssid="Secret",
                                        dfs=dict(cac_active=1,cac_seconds=60))),None
            return original(obj,method,args)
        device.lua.globals().bridge_call=call
        proof=self.inventory_data(device)["rows"][0]["readiness"]
        self.assertEqual((proof["ap_failure"],proof["ap_state"],proof["cac_seconds"]),
                         ("control_state","DFS",60))
        self.assertTrue(proof["cac_active"])
        self.assertNotIn("Secret",json.dumps(proof))


class ReadinessPrivacyTests(unittest.TestCase):
    def test_arbitrary_fields_and_values_cannot_enter_snapshot_or_operation_history(self):
        value=dict(ap_failure="password=secret",ap_state="private",ssid="private",key="secret",
                   ap_carrier="private",native_error=True,device_error=999,frequency=float("inf"),
                   kernel_flags_readable=False)
        self.assertEqual(readiness_evidence(value),dict(kernel_flags_readable=False))
        from modules.capture_services.openwrt.wireless_readiness_evidence import WirelessReadinessEvidence
        journal=OperationDiagnostics()
        with WirelessReadinessEvidence(journal,"ap").observing() as trace:
            for _ in range(300):trace.sample(value)
        raw=json.dumps(journal.snapshot())
        self.assertNotIn("private",raw);self.assertNotIn("secret",raw)
        self.assertLessEqual(len(journal.snapshot()["configuration"]),13)
