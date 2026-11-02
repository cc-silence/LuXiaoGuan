"""Startup evidence survives polling/export; cancellation retains recovery protection."""
import json
import threading
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace
from modules.capture_services.openwrt.operation_diagnostics import OperationDiagnostics, sanitized_operation_evidence
from modules.capture_services.openwrt.wireless_readiness_evidence import WirelessReadinessEvidence
from modules.capture_services.openwrt.wireless_setup_manager import WirelessSetupManager
from modules.capture_services.configuration_progress import reporting, phase
from tests.test_standalone import APP
from modules.main_pages.packet_capture.openwrt.router_task_progress import RouterTaskProgressDialog
from tests.test_wireless_version_contracts import PROFILES, VersionDevice
from tests.test_wireless_setup import LuaRuntime


class ReadinessEvidenceTests(unittest.TestCase):
    def test_repeated_state_is_deduplicated_and_failure_is_retained_after_command_noise(self):
        journal=OperationDiagnostics()
        evidence=WirelessReadinessEvidence(journal,"ap")
        with self.assertRaises(RuntimeError):
            with evidence.observing():
                for _ in range(90):
                    evidence.sample(dict(ready=False,reason="wireless_ap_pending",ap_state="DFS",
                                         radio_up=True,interface_up=True,frequency=5500,
                                         ssid="private-name",key="private-password"))
                raise RuntimeError("private-error-output")
        for _ in range(150):journal.record("command",operation="probe",status="completed")
        exported=sanitized_operation_evidence(journal.snapshot(),SimpleNamespace(text=lambda value: value))
        rows=exported["configuration"]
        self.assertEqual(len(rows),2)
        final=exported["configuration_failures"][-1]
        self.assertEqual(final["ap_state"],"DFS")
        self.assertEqual(final["frequency"],5500)
        self.assertEqual(final["verify_attempts"],90)
        self.assertEqual(final["reason"],"wireless_ap_pending")
        self.assertNotIn("private",json.dumps(exported))

    def test_changing_states_have_a_hard_bound_and_final_keeps_latest_observation(self):
        journal=OperationDiagnostics();evidence=WirelessReadinessEvidence(journal,"ap")
        with evidence.observing():
            for i in range(100):evidence.sample(dict(ready=False,radio_up=bool(i%2)))
            evidence.sample(dict(ready=True,ap_state="ENABLED",frequency=5180))
        rows=journal.snapshot()["configuration"]
        self.assertEqual(len(rows),13)
        self.assertEqual(rows[-1]["reason"],"verified")
        self.assertEqual(rows[-1]["ap_state"],"ENABLED")

    def test_unknown_strings_and_non_numeric_fields_are_not_retained(self):
        journal=OperationDiagnostics();evidence=WirelessReadinessEvidence(journal,"ap")
        with evidence.observing():
            evidence.sample(dict(reason="secret-name",ap_state="secret-name",radio_up="secret",
                                 native_error="secret",frequency=float("inf")))
        self.assertNotIn("secret",json.dumps(journal.snapshot()))

    def test_readiness_wait_records_state_and_allows_cooperative_cancel(self):
        runner=Mock();runner.operation_diagnostics=OperationDiagnostics()
        manager=WirelessSetupManager(runner)
        request=SimpleNamespace(mode="ap",radio="radio-test",options={})
        phases=[]
        with patch.object(manager,"_call",return_value=dict(ready=True,ap_state="ENABLED",frequency=5180)):
            with reporting(phases.append):manager._wait_ready(request,"section",threading.Event())
        self.assertFalse(phases[0]["uncancellable"])
        self.assertEqual(runner.operation_diagnostics.snapshot()["configuration"][-1]["status"],"completed")
        event=threading.Event();event.set()
        with self.assertRaises(InterruptedError):manager._wait_ready(request,"section",event)
        self.assertEqual(runner.operation_diagnostics.snapshot()["configuration"][-1]["status"],"cancelled")

    def test_ui_cancel_during_actual_wait_restores_original_configuration(self):
        import copy
        from tests.test_wireless_setup import LuaDevice
        from modules.capture_services.openwrt.wireless_setup_models import WirelessRequest
        if not LuaRuntime:self.skipTest("Lua unavailable")
        device=LuaDevice();manager=WirelessSetupManager(device);event=threading.Event()
        before=copy.deepcopy(device.config)
        dialog=RouterTaskProgressDialog("Apply",event.set)
        self.addCleanup(dialog.finish)
        original=manager._call
        def call(action,cancel,**kwargs):
            if action=="status" and cancel is event:
                dialog.reject()
                return dict(ready=False,reason="wireless_ap_pending")
            return original(action,cancel,**kwargs)
        request=WirelessRequest("ap","radio7","Home",section="home",options={"encryption":"psk2"})
        with patch.object(manager,"_call",side_effect=call), reporting(dialog.update_progress):
            with self.assertRaises(InterruptedError):manager.save(request,event,Mock())
        self.assertTrue(event.is_set())
        self.assertEqual(device.config,before)
        self.assertIn(("uci","rollback"),device.calls)
        self.assertNotIn(("uci","confirm"),device.calls)

    def test_cancellation_protection_tracks_stage_instead_of_latching(self):
        cancelled=Mock();dialog=RouterTaskProgressDialog("Apply",cancelled)
        self.addCleanup(dialog.finish)
        with reporting(dialog.update_progress):phase("Write")
        dialog.reject();cancelled.assert_not_called()
        with reporting(dialog.update_progress):phase("Verify",cancellable=True)
        self.assertTrue(dialog.cancel_button.isEnabled())
        dialog.reject();cancelled.assert_called_once_with()
        self.assertTrue(dialog.running)
        with reporting(dialog.update_progress):phase("Restore")
        self.assertFalse(dialog.cancel_button.isEnabled())
        self.assertTrue(dialog.running)


@unittest.skipUnless(LuaRuntime,"Lua unavailable")
class NativeReadinessObservationTests(unittest.TestCase):
    def test_native_dfs_fields_and_channel_are_logged_without_names(self):
        device=VersionDevice(next(p for p in PROFILES if p["series"]=="23.05"))
        original=device.call
        def call(obj,method,args):
            if obj.startswith("hostapd.") and method=="get_status":
                return device.wrap(dict(status="DFS",freq=5500,channel=100,ssid="private-name",
                                        dfs=dict(cac_active=1,cac_seconds=60))),None
            return original(obj,method,args)
        device.lua.globals().bridge_call=call
        value=json.loads(device.execute(dict(action="status",radio="radio7",section="home",mode="ap")))["result"]
        self.assertFalse(value["ready"])
        self.assertEqual((value["ap_state"],value["channel"],value["cac_active"],value["cac_seconds"]),
                         ("DFS",100,True,60))
        journal=OperationDiagnostics()
        with WirelessReadinessEvidence(journal,"ap").observing() as trace:trace.sample(value)
        final=journal.snapshot()["configuration"][-1]
        self.assertEqual(final["cac_seconds"],60)
        self.assertNotIn("private-name",json.dumps(final))

    def test_status_observations_do_not_change_readiness_or_expose_configuration(self):
        for profile in PROFILES:
            with self.subTest(series=profile["series"]):
                device=VersionDevice(profile)
                for state,ready in ((("ENABLED",True),) if profile["ap_status"]=="legacy" else (("DISABLED",False),("DFS",False),("ENABLED",True))):
                    device.status_value=state
                    value=json.loads(device.execute(dict(action="status",radio="radio7",section="home",mode="ap")))["result"]
                    self.assertEqual(value["ready"],ready)
                    if profile["ap_status"]!="legacy":
                        self.assertEqual(value["ap_state"],state)
                    self.assertTrue(value["radio_up"])
                    self.assertTrue(value["interface_present"])
                    self.assertNotIn("ssid",value)
                    self.assertNotIn("key",value)
