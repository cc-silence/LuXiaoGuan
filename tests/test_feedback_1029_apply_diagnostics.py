"""Failure evidence and recovery messages for the October 29 feedback."""
import json
import unittest
from modules.capture_services.configuration_progress import reporting
from modules.capture_services.openwrt.operation_diagnostics import OperationDiagnostics
from modules.capture_services.openwrt.apply_evidence import ApplyEvidence
from tests.test_checked_apply import CheckedApplyTests, run_checked, fast_deadlines, LuaRuntime
from tests.test_wireless_version_contracts import PROFILES, VersionDevice


@unittest.skipUnless(LuaRuntime, "Lua unavailable")
class ApplyDiagnosticsTests(unittest.TestCase):
    def test_failed_apply_announces_failure_before_restore(self):
        device, channel, event, stage, verify = CheckedApplyTests().fixture()
        phases=[]
        with reporting(phases.append), fast_deadlines(), self.assertRaises(RuntimeError):
            run_checked(channel, stage, lambda *_: False, event,
                        candidates=("new",), original_host="old")
        restoring=[v["stage"] for v in phases if "\u6062\u590d\u539f\u914d\u7f6e" in v["stage"]]
        self.assertTrue(restoring)
        self.assertIn("\u4fee\u6539\u5931\u8d25", restoring[0])

    def test_disabled_ap_is_reported_as_runtime_failure(self):
        device=VersionDevice(next(p for p in PROFILES if p["series"]=="21.02"))
        device.status_value="DISABLED"
        result=json.loads(device.execute(dict(action="status", radio="radio7", section="home", mode="ap")))
        self.assertTrue(result["ok"])
        self.assertFalse(result["result"]["ready"])
        self.assertEqual(result["result"]["reason"], "wireless_ap_pending")

    def test_wireless_reason_survives_export_and_restore(self):
        journal=OperationDiagnostics();journal.bind_connection("test-device")
        evidence=ApplyEvidence(journal)
        evidence.mismatch("wireless_identity_pending")
        evidence.emit("rollback");evidence.restored=True;evidence.finish("failed")
        final=journal.snapshot()["configuration_failures"][-1]
        self.assertEqual(final["last_reason"], "wireless_identity_pending")
        self.assertTrue(final["restored"])

    def test_unloaded_name_reports_field_without_logging_name(self):
        for profile in PROFILES:
            with self.subTest(series=profile["series"]):
                device=VersionDevice(profile)
                original=device.call
                def call(obj, method, args):
                    value, error=original(obj, method, args)
                    if obj=="network.wireless" and method=="status" and not error:
                        state=device.unwrap(value)
                        for row in state["radio7"]["interfaces"]:
                            if row["section"]=="home":row["config"]["ssid"]="stale-private-name"
                        return device.wrap(state),error
                    return value,error
                device.lua.globals().bridge_call=call
                result=json.loads(device.execute(dict(action="status", radio="radio7", section="home", mode="ap")))
                self.assertTrue(result["ok"])
                self.assertEqual(result["result"]["reason"], "wireless_config_ssid")
                self.assertNotIn("stale-private-name",json.dumps(result))

    def test_stale_netifd_mac_uses_actual_kernel_address(self):
        for profile in PROFILES:
            for actual in ("02:12:34:56:78:90", "02:12:34:56:78:91", None):
                with self.subTest(series=profile["series"], actual=actual):
                    device=VersionDevice(profile)
                    device.config["wireless"]["home"]["macaddr"]="02:12:34:56:78:90"
                    original=device.call
                    def call(obj, method, args):
                        value,error=original(obj,method,args)
                        if obj=="network.device" and not error:
                            state=device.unwrap(value);state["macaddr"]="02:00:00:00:00:01"
                            return device.wrap(state),error
                        return value,error
                    device.lua.globals().bridge_call=call
                    device.lua.globals().bridge_kernel_mac=lambda _:actual
                    result=json.loads(device.execute(dict(action="status",radio="radio7",section="home",mode="ap")))
                    self.assertTrue(result["ok"])
                    self.assertEqual(result["result"]["ready"],actual=="02:12:34:56:78:90")

    def test_lan_recovery_unlocks_after_native_apply_reply_before_connect(self):
        import time
        from modules.capture_services.lan_client_recovery import RenewalGate,client_plan
        from tests.test_lan_client_recovery import review,adapter,NEW
        device,channel,event,stage,verify=CheckedApplyTests().fixture()
        gate=RenewalGate(client_plan(review(adapter()),NEW))
        original_call=channel.call
        def call(action,**values):
            if action=="checked_apply":self.assertFalse(gate.payload()["can_renew"])
            return original_call(action,**values)
        channel.call=call
        original_connect=channel.connect
        observed=[]
        def connect(host,timeout):
            if "checked_apply" in channel.calls:
                observed.append(gate.payload()["can_renew"])
                self.assertTrue(observed[-1])
            return original_connect(host,timeout)
        channel.connect=connect
        result=run_checked(channel,stage,verify,event,candidates=("new",),original_host="old",client_recovery=gate)
        self.assertTrue(result["applied"]);self.assertTrue(observed)
        self.assertFalse(gate.payload()["can_renew"])

    def test_lost_apply_reply_does_not_unlock_client_recovery(self):
        from modules.capture_services.lan_client_recovery import RenewalGate,client_plan
        from tests.test_lan_client_recovery import review,adapter,NEW
        device,channel,event,stage,verify=CheckedApplyTests().fixture()
        gate=RenewalGate(client_plan(review(adapter()),NEW));channel.lost_reply=True
        original_connect=channel.connect
        def connect(host,timeout):
            self.assertFalse(gate.payload()["can_renew"])
            return original_connect(host,timeout)
        channel.connect=connect
        result=run_checked(channel,stage,verify,event,candidates=("new",),original_host="old",client_recovery=gate)
        self.assertTrue(result["applied"])
