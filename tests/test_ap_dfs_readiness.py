"""Native Lua CAC evidence and virtual-time regression for startup decisions."""
from dataclasses import replace
from types import SimpleNamespace
import json
import threading
import unittest
from unittest.mock import Mock, patch

from tests.test_ap_kernel_readiness import KernelApDevice
from tests.test_wireless_setup import LuaRuntime
from modules.capture_services.openwrt.ap_startup_wait import ApStartupWait
from modules.capture_services.openwrt.signal_manager import RouterSignalManager, SignalError
from modules.capture_services.openwrt.signal_models import RouterSignal, SignalInventory
from modules.capture_services.openwrt.wireless_setup_manager import WirelessSetupManager
from modules.capture_services.openwrt.signal_recovery import wait_for_recovery

START = "DFS-CAC-START freq=5220 chan=44 sec_chan=1, width=2, seg0=50, seg1=0, cac_time=60s"


def log(message=START, stamp=1000, iface="iface-home"):
    return f"Thu Jan 1 00:00:00 1970 [{stamp:.3f}] daemon.notice hostapd: {iface}: {message}\n"


class DfsDevice(KernelApDevice):
    def __init__(self):
        super().__init__()
        self.kernel_carrier = "0"
        self.log_text = log()
        self.now = 1010
        self.phy = {"iface-home": "phy2", "phy2-ap0": "phy2", "other": "phy9"}
        self.log_reads = 0
        self.lua.globals().bridge_dfs_now = lambda: self.now
        self.lua.globals().bridge_dfs_phy = lambda iface: self.phy.get(iface, "")
        self.lua.execute(r"""
os.time=function() return bridge_dfs_now() end
local original=io.open
io.open=function(path,mode)
 local iface=path:match("^/sys/class/net/([^/]+)/phy80211/name$")
 if not iface then return original(path,mode) end
 return {read=function() return bridge_dfs_phy(iface).."\n" end,close=function() return true end}
end
""")

    def read(self, command):
        if command.startswith("logread -t -l 128"):
            self.log_reads += 1
            return self.log_text
        return super().read(command)


@unittest.skipUnless(LuaRuntime, "Lua unavailable")
class DfsEvidenceTests(unittest.TestCase):
    def test_missing_control_object_has_cac_evidence_but_is_never_ready(self):
        device = DfsDevice()
        status = device.status()
        self.assertFalse(status["ready"])
        self.assertEqual((status["ap_state"], status["cac_active"], status["cac_seconds"],
                          status["cac_seconds_left"], status["cac_event"]),
                         ("DFS", True, 60, 50, 1000000))

    def test_stale_future_terminal_and_oversize_logs_never_extend_wait(self):
        for text in (log(stamp=900), log(stamp=1020), log()+log("DFS-CAC-COMPLETED success=1",1005),
                     log()+log("AP-DISABLED",1005), log()+log("interface state DFS->DISABLED",1005),
                     "x"*65537, log()+"\n".join(["noise"]*128), log(iface="other"),
                     log().replace("[1000.000] ", ""), log().replace("hostapd:", "application:")):
            with self.subTest(text=text[:100]):
                device = DfsDevice()
                device.log_text = text
                self.assertIsNot(device.status().get("cac_active"), True)

    def test_only_verified_same_phy_shares_cac_with_secondary_bss(self):
        device = DfsDevice()
        device.log_text = log(iface="phy2-ap0")
        self.assertTrue(device.status()["cac_active"])
        device.phy["phy2-ap0"] = "phy9"
        self.assertIsNot(device.status().get("cac_active"), True)

    def test_latest_cac_cycle_on_same_hardware_wins_over_older_own_event(self):
        device=DfsDevice()
        device.log_text=log()+log(START.replace("cac_time=60s","cac_time=600s"),1005,"phy2-ap0")
        status=device.status()
        self.assertEqual((status["cac_seconds"],status["cac_seconds_left"],status["cac_event"]),
                         (600,595,1005000))

    def test_cac_terminal_event_on_same_hardware_blocks_older_start(self):
        for message in ("DFS-CAC-COMPLETED success=1", "DFS-CAC-ABORTED", "DFS-RADAR-DETECTED freq=5220"):
            device=DfsDevice()
            device.log_text=(log()+log(message,1005,"phy2-ap0")
                             +log("interface state DFS->ENABLED",1006,"phy2-ap0")
                             +log("AP-ENABLED",1007,"phy2-ap0"))
            self.assertIsNot(device.status().get("cac_active"),True)

    def test_local_bss_stop_blocks_old_borrow_but_does_not_stop_other_bss_cac(self):
        device=DfsDevice()
        device.log_text=log(iface="phy2-ap0")+log("AP-DISABLED",1005)
        self.assertIsNot(device.status().get("cac_active"),True)
        device.log_text=log()+log("AP-DISABLED",1005,"phy2-ap0")
        self.assertTrue(device.status()["cac_active"])

    def test_fractional_second_terminal_is_never_an_active_zero_duration_cac(self):
        device=DfsDevice()
        device.log_text=log()+log("DFS-CAC-COMPLETED success=1",1010.5,"phy2-ap0")
        self.assertIsNot(device.status().get("cac_active"),True)

    def test_equal_timestamp_events_follow_router_log_order(self):
        device=DfsDevice()
        device.log_text=log()+log("DFS-CAC-COMPLETED success=1",1000,"phy2-ap0")
        self.assertIsNot(device.status().get("cac_active"),True)
        device.log_text=log("DFS-CAC-COMPLETED success=1",1000,"phy2-ap0")+log()
        self.assertTrue(device.status()["cac_active"])

    def test_control_dfs_and_native_remaining_use_same_sanitized_contract(self):
        device = DfsDevice()
        device.status_error = None
        device.status_value = "DFS"
        self.assertTrue(device.status()["cac_active"])
        device.kernel_carrier = "1"
        device.status_error = 4
        self.assertTrue(device.status()["ready"])

    def test_native_cac_fields_take_priority_over_log_fallback(self):
        for active in (0,1):
            device=DfsDevice()
            original=device.call
            def call(obj,method,args):
                if obj=="hostapd.iface-home" and method=="get_status":
                    return device.wrap({"status":"DFS","dfs":{"cac_active":active,
                        "cac_seconds":600,"cac_seconds_left":570}}),None
                return original(obj,method,args)
            device.lua.globals().bridge_call=call
            status=device.status()
            self.assertEqual(status["cac_active"],bool(active))
            self.assertEqual((status["cac_seconds"],status["cac_seconds_left"]),(600,570))
            self.assertEqual(device.log_reads,0)

    def test_inventory_and_periodic_status_keep_dfs_evidence(self):
        from modules.capture_services.openwrt.signal_commands import signal_command
        from modules.capture_services.openwrt.wireless_status_commands import STATUS_LUA
        device = DfsDevice()
        command = signal_command("read")
        body = command.split("<<'WINBOX_SIGNAL_LUA'\n",1)[1].rsplit("\nWINBOX_SIGNAL_LUA",1)[0]
        device._output=[]
        device.lua.globals().arg=device.wrap({1:json.dumps({"action":"read"})})
        device.lua.execute(body)
        data=json.loads("".join(device._output))
        row=next(row for row in data["result"]["rows"] if row["section"]=="home")
        self.assertEqual(row["running"],"inactive")
        self.assertTrue(row["readiness"]["cac_active"])
        device._output=[]
        device.lua.execute(STATUS_LUA)
        data=json.loads("".join(device._output))
        current=next(row for row in data["result"]["runtime"] if row["section"]=="home")
        self.assertTrue(current["readiness"]["cac_active"])
        self.assertEqual(current["readiness"]["cac_seconds_left"],50)

    def test_cac_proof_is_kept_in_export_without_raw_logs(self):
        from modules.capture_services.openwrt.operation_diagnostics import OperationDiagnostics
        from modules.capture_services.openwrt.wireless_readiness_evidence import WirelessReadinessEvidence
        journal=OperationDiagnostics()
        with WirelessReadinessEvidence(journal,"ap").observing() as evidence:
            evidence.sample(DfsDevice().status())
        final=journal.snapshot()["configuration"][-1]
        self.assertEqual(final["cac_seconds_left"],50)
        self.assertNotIn("hostapd:",json.dumps(final))

    def test_parent_enable_waits_for_actual_ap_then_confirms_within_native_rollback(self):
        device=DfsDevice()
        device.config["wireless"]["radio7"]["disabled"]="1"
        device.config["wireless"]["home"]["disabled"]="1"
        clock=[0.0]
        cancel=Mock(spec=threading.Event)
        cancel.is_set.return_value=False
        def wait(seconds):
            clock[0]+=seconds
            device.now=1000+clock[0]
            if clock[0]>=65:
                device.kernel_carrier="1"
            return False
        cancel.wait.side_effect=wait
        device.now=1000
        with patch("time.monotonic",side_effect=lambda:clock[0]):
            WirelessSetupManager(device).toggle("home",True,cancel,enable_parent=True,
                                               expected_revision=device.revision()[:64])
        self.assertEqual(clock[0],65)
        self.assertIn(("uci","confirm"),device.calls)
        self.assertNotIn(("uci","rollback"),device.calls)


class DfsWaitPolicyTests(unittest.TestCase):
    def test_normal_wait_unchanged_and_stale_repeats_cannot_keep_extending(self):
        clock=[0.0]
        with patch("time.monotonic",side_effect=lambda:clock[0]), patch(
                "modules.capture_services.openwrt.ap_startup_wait.phase"):
            wait=ApStartupWait()
            self.assertEqual(wait.deadline,45)
            wait.observe({"cac_active":False,"cac_seconds":60})
            self.assertEqual(wait.deadline,45)
            proof={"cac_active":True,"cac_seconds":60,"cac_seconds_left":50,"cac_event":1000}
            wait.observe(proof,"ap")
            self.assertEqual(wait.deadline,60)
            clock[0]=30
            wait.observe(proof,"ap")
            self.assertEqual(wait.deadline,60)
            proof["cac_event"]=2000
            wait.observe(proof,"ap")
            self.assertEqual(wait.deadline,90)

    def test_missing_countdown_after_inactive_still_cannot_renew_forever(self):
        clock=[0.0]
        with patch("time.monotonic",side_effect=lambda:clock[0]),patch(
                "modules.capture_services.openwrt.ap_startup_wait.phase"):
            wait=ApStartupWait()
            wait.observe({"cac_active":True,"cac_seconds":60},"ap")
            clock[0]=10
            wait.observe({"cac_active":False},"ap")
            clock[0]=20
            wait.observe({"cac_active":True,"cac_seconds":60},"ap")
            self.assertEqual(wait.deadline,90)
            clock[0]=30
            wait.observe({"cac_active":True,"cac_seconds":60},"ap")
            self.assertEqual(wait.deadline,90)

    def test_native_rollback_and_absolute_bounds_are_never_exceeded(self):
        with patch("time.monotonic",return_value=0), patch(
                "modules.capture_services.openwrt.ap_startup_wait.phase"):
            wait=ApStartupWait(hard_deadline=80,rollback=True)
            wait.observe({"cac_active":True,"cac_seconds":600,"cac_seconds_left":600})
            self.assertEqual(wait.deadline,80)
            for proof in ({ "cac_active":True,"cac_seconds":3601},
                          {"cac_active":True,"cac_seconds":True},
                          {"cac_active":True,"cac_seconds":60,"cac_seconds_left":61}):
                other=ApStartupWait()
                other.observe(proof)
                self.assertEqual(other.deadline,45)

    def fixture(self):
        row=RouterSignal("home","Home","ap","radio7","5g",True,False,"inactive")
        before=SignalInventory((row,),"a"*64)
        pending=replace(row,disabled=False,readiness={"cac_active":True,"cac_seconds":60,
                                                    "cac_seconds_left":60,"cac_event":1000})
        manager=RouterSignalManager(SimpleNamespace())
        manager._run=Mock(return_value={"committed":True})
        cancel=Mock(spec=threading.Event)
        cancel.is_set.return_value=False
        clock=[0.0]
        cancel.wait.side_effect=lambda seconds:clock.__setitem__(0,clock[0]+seconds) or False
        return manager,before,pending,cancel,clock

    def test_direct_toggle_waits_only_when_cac_proved_and_finishes_immediately_when_ready(self):
        manager,before,row,cancel,clock=self.fixture()
        active=replace(row,running="active",readiness={"cac_active":False})
        manager.read=Mock(side_effect=lambda _:SignalInventory(
            (active if clock[0]>=65 else row,),"b"*64))
        with patch("time.monotonic",side_effect=lambda:clock[0]):
            result=manager.change(before,"home",True,cancel)
        self.assertEqual(clock[0],65)
        self.assertEqual(result.rows[0].running,"active")
        manager._run.assert_called_once()

    def test_repair_waits_for_cac_without_reloading_again(self):
        manager,before,row,cancel,clock=self.fixture()
        before=replace(before,rows=(replace(row,readiness={}),))
        active=replace(row,running="active",readiness={})
        manager.read=Mock(side_effect=lambda _:SignalInventory(
            (active if clock[0]>=65 else row,),"b"*64))
        manager._run_command=Mock(return_value=SimpleNamespace(cancelled=False,timed_out=False,exit_code=0))
        with patch("time.monotonic",side_effect=lambda:clock[0]):
            self.assertEqual(manager.repair(before,"home",cancel).rows[0].running,"active")
        self.assertEqual(clock[0],65)
        manager._run_command.assert_called_once()

    def test_repair_rejects_an_already_observed_cac_without_reloading(self):
        manager,before,row,cancel,clock=self.fixture()
        before=replace(before,rows=(row,))
        manager._run_command=Mock()
        with self.assertRaises(SignalError) as error:
            manager.repair(before,"home",cancel)
        self.assertEqual(error.exception.code,"cac_active")
        manager._run_command.assert_not_called()

    def test_native_countdown_restart_gets_a_new_bounded_wait(self):
        manager,before,row,cancel,clock=self.fixture()
        active=replace(row,running="active",readiness={"cac_active":False})
        def read(event):
            remaining=(60 if clock[0]<30 else 90)-int(clock[0])
            proof={"cac_active":True,"cac_seconds":60,"cac_seconds_left":max(0,remaining)}
            return SignalInventory((active if clock[0]>=95 else replace(row,readiness=proof),),"b"*64)
        manager.read=Mock(side_effect=read)
        with patch("time.monotonic",side_effect=lambda:clock[0]):
            self.assertEqual(manager.change(before,"home",True,cancel).rows[0].running,"active")
        self.assertEqual(clock[0],95)
        manager._run.assert_called_once()

    def test_no_cac_keeps_normal_deadline_and_cancel_stops_without_restart(self):
        for cancel_now in (False,True):
            manager,before,row,cancel,clock=self.fixture()
            row=replace(row,readiness={})
            manager.read=Mock(return_value=SignalInventory((row,),"b"*64))
            if cancel_now:
                cancel.wait.side_effect=lambda seconds:True
            with patch("time.monotonic",side_effect=lambda:clock[0]):
                with self.assertRaises(SignalError) as error:
                    manager.change(before,"home",True,cancel)
            self.assertEqual(error.exception.code,"cancelled" if cancel_now else "unconfirmed")
            self.assertEqual(clock[0],0 if cancel_now else 45)
            manager._run.assert_called_once()

    def test_original_ap_recovery_uses_same_device_evidence(self):
        clock=[0.0]
        cancel=Mock(spec=threading.Event)
        cancel.is_set.return_value=False
        cancel.wait.side_effect=lambda seconds:clock.__setitem__(0,clock[0]+seconds) or False
        def observe(section,radio,timeout):
            return {"ready":clock[0]>=65, "cac_active":clock[0]<60,
                    "cac_seconds":60, "cac_seconds_left":max(0,60-int(clock[0]))}
        with patch("time.monotonic",side_effect=lambda:clock[0]):
            wait_for_recovery((("home","radio7"),),observe,cancel,45,hard_deadline=80)
        self.assertEqual(clock[0],65)

    def test_ui_reports_radar_and_does_not_offer_restart_during_cac(self):
        from modules.main_pages.packet_capture.openwrt.router_signal_page import RouterSignalPage
        _,_,row,_,_=self.fixture()
        self.assertIn("\u96f7\u8fbe\u68c0\u6d4b",RouterSignalPage._state_text(row))
        self.assertNotIn("\u539f\u56e0\u5c1a\u672a\u786e\u5b9a",RouterSignalPage._state_text(row))
        self.assertFalse(RouterSignalPage._needs_repair(row))
