"""Source-backed regressions for compatibility audit findings."""
import unittest
from tests.test_router_policy import PolicyDevice, LuaRuntime
from modules.capture_services.openwrt.router_policy_plan import build_plan


@unittest.skipUnless(LuaRuntime,"Lua runtime unavailable")
class CompatibilityFixTests(unittest.TestCase):
    def test_disabled_lan_is_read_and_blocks_boot_off_without_writing(self):
        for legacy in (True,False):
            device=PolicyDevice(legacy)
            device.config["network"]["lan"]["disabled"]="1"
            snapshot=device.policy.read(device.cancel)
            self.assertEqual(snapshot["configs"]["network"]["lan"]["disabled"],"1")
            with self.assertRaises(ValueError):build_plan(snapshot,"boot",dict(enabled=False))
            self.assertTrue(build_plan(snapshot,"boot",dict(enabled=True))["boot_enabled"])
            self.assertFalse(device.commits)

    def test_enabled_and_default_lan_remain_usable(self):
        device=PolicyDevice()
        for value in (None,"0"):
            if value is None:device.config["network"]["lan"].pop("disabled",None)
            else:device.config["network"]["lan"]["disabled"]=value
            snapshot=device.policy.read(device.cancel)
            self.assertFalse(build_plan(snapshot,"boot",dict(enabled=False))["boot_enabled"])

    def test_firewall_limits_follow_actual_engine_and_verified_series(self):
        for version,limit in (("19.07.10",14),("21.02.7",11),("22.03.7",11),("23.05.5",11),("24.10.0",11),("25.12.0",11)):
            device=PolicyDevice();device.board["release"]["version"]=version
            device.flags.update(firewall3=True,firewall4=False)
            for length in (limit,limit+1):
                device.config["firewall"]["zone_wan"]["name"]="z"*length
                snapshot=device.policy.read(device.cancel)
                self.assertTrue(snapshot["capabilities"]["firewall3"])
                if length==limit:
                    plan=build_plan(snapshot,"initial",dict(items=["web"]))
                    self.assertTrue(plan["operations"])
                else:
                    with self.assertRaises(ValueError):build_plan(snapshot,"initial",dict(items=["web"]))
            self.assertFalse(device.commits)
        device=PolicyDevice();device.board["release"]["version"]="21.02.7"
        device.config["firewall"]["zone_wan"]["name"]="upstream_custom"
        snapshot=device.policy.read(device.cancel)
        self.assertTrue(snapshot["capabilities"]["firewall4"])
        self.assertTrue(build_plan(snapshot,"initial",dict(items=["web"]))["operations"])

    def test_unknown_or_ambiguous_firewall_never_guesses(self):
        for fw3,fw4 in ((False,False),(True,True)):
            device=PolicyDevice();device.flags.update(firewall3=fw3,firewall4=fw4)
            with self.assertRaises(ValueError):device.policy.preview("initial",dict(items=["web"]),device.cancel)
            self.assertFalse(device.commits)
        device=PolicyDevice();device.flags.update(firewall3=True,firewall4=False)
        device.board["release"]["version"]="unknown"
        with self.assertRaises(ValueError):device.policy.preview("initial",dict(items=["web"]),device.cancel)

    def test_firewall_engine_change_invalidates_approved_review(self):
        from unittest.mock import Mock
        device=PolicyDevice()
        review=device.policy.preview("initial",dict(items=["web"]),device.cancel)
        device.flags.update(firewall3=True,firewall4=False)
        with self.assertRaises(RuntimeError):device.policy.apply(review,None,None,device.cancel,Mock())
        self.assertFalse(device.commits)

    def test_ipv6_and_both_default_route_formats_do_not_block_ipv4_lan(self):
        for legacy in (True,False):
            device=PolicyDevice(legacy)
            device.interfaces.append(dict(interface="wan6",route=[dict(target="2001:db8:1234::",mask=64),dict(target="::",mask=0)]))
            device.interfaces.append(dict(interface="wan",route=[dict(target="0.0.0.0",mask=0),dict(target="0.0.0.0",mask="0")]))
            plan=device.policy.preview("initial",dict(items=["lan"],lan_ip="192.168.77.1"),device.cancel)
            self.assertEqual(plan["new_address"],"192.168.77.1")
            self.assertFalse(device.commits)

    def test_ipv4_route_overlap_is_still_blocked_in_dual_stack(self):
        device=PolicyDevice()
        device.interfaces.append(dict(interface="wan6",route=[dict(target="2001:db8::",mask=32)]))
        device.interfaces.append(dict(interface="wan",route=[dict(target="192.168.77.0",mask=24)]))
        with self.assertRaises(ValueError):device.policy.preview("initial",dict(items=["lan"],lan_ip="192.168.77.1"),device.cancel)
        self.assertFalse(device.commits)

    def test_malformed_route_is_rejected_instead_of_ignored(self):
        for route in (dict(target="bad",mask=24),dict(target="192.168.22.0",mask=99),dict(target="2001:db8::",mask=129)):
            device=PolicyDevice();device.interfaces.append(dict(interface="wan",route=[route]))
            with self.assertRaises(ValueError):device.policy.preview("initial",dict(items=["lan"],lan_ip="192.168.77.1"),device.cancel)
            self.assertFalse(device.commits)

    def test_actual_validation_failure_is_in_diagnostics_without_network_values(self):
        import json,threading
        from unittest.mock import Mock
        from modules.capture_services.management_diagnostics import ManagementTrace
        from modules.capture_services.openwrt.operation_diagnostics import OperationDiagnostics
        cases=(("boot",dict(enabled=False)),("initial",dict(items=["web"])),("initial",dict(items=["lan"],lan_ip="192.168.77.1")))
        for kind,options in cases:
            device=PolicyDevice();device.operation_diagnostics=OperationDiagnostics()
            if kind=="boot":device.config["network"]["lan"]["disabled"]="1"
            elif options["items"]==["web"]:
                device.flags.update(firewall3=True,firewall4=False);device.board["release"]["version"]="21.02.7"
                device.config["firewall"]["zone_wan"]["name"]="private_zone_long"
            else:device.interfaces.append(dict(interface="wan",route=[dict(target="private-route",mask=24)]))
            with self.assertRaises(ValueError):
                ManagementTrace(device,"policy_preview",dict(kind=kind)).execute(lambda e,r:device.policy.preview(kind,options,e),threading.Event(),Mock())
            data=device.operation_diagnostics.snapshot()
            row=data["management_failures"][-1]
            self.assertEqual(row["status"],"failed");self.assertEqual(row["reason"],"invalid_data")
            self.assertRegex(row["code_site"],r"^(router_policy_plan|upstream_firewall_zones):[0-9]+$")
            self.assertNotIn("private",json.dumps(data))
