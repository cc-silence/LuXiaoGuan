"""Regressions for all three common-settings audit findings."""
import copy,json,threading,unittest
from unittest.mock import Mock,patch
from tests.test_router_policy import PolicyDevice,LuaRuntime
from modules.capture_services.openwrt.router_policy_plan import build_plan
from modules.capture_services.openwrt.common_settings import save_settings
from modules.capture_services.openwrt.signal_manager import SignalError


@unittest.skipUnless(LuaRuntime,"Lua runtime unavailable")
class BootAndFirewallFixTests(unittest.TestCase):
    def snapshot(self,legacy=False):
        d=PolicyDevice(legacy);return d,d.policy.read(d.cancel)

    def test_boot_off_reads_and_rejects_lan_auto_zero_without_writing(self):
        for legacy in (False,True):
            d=PolicyDevice(legacy);d.config["network"]["lan"]["auto"]="0"
            d.files["/etc/config/network"]=json.dumps(d.config["network"],sort_keys=True)
            s=d.policy.read(d.cancel)
            self.assertEqual(s["configs"]["network"]["lan"]["auto"],"0")
            with self.assertRaises(ValueError):build_plan(s,"boot",dict(enabled=False))
            self.assertFalse(d.commits)
            self.assertTrue(build_plan(s,"boot",dict(enabled=True))["boot_enabled"])

    def test_default_or_enabled_lan_keeps_boot_toggle_available(self):
        d,s=self.snapshot()
        for auto in (None,"1"):
            if auto is None:s["configs"]["network"]["lan"].pop("auto",None)
            else:s["configs"]["network"]["lan"]["auto"]=auto
            self.assertFalse(build_plan(s,"boot",dict(enabled=False))["boot_enabled"])

    def test_custom_upstream_zone_gets_all_protections_with_real_transactions(self):
        for legacy in (False,True):
            d=PolicyDevice(legacy)
            d.config["firewall"]["zone_wan"]["network"]=["wan"]
            d.config["firewall"]["custom"]={".type":"zone","name":"uplink_custom","network":"wwan"}
            d.files["/etc/config/firewall"]=json.dumps(d.config["firewall"],sort_keys=True)
            before=copy.deepcopy(d.config)
            s=d.policy.read(d.cancel);plan=build_plan(s,"initial",dict(items=["web","ping","igmp"]))
            d.policy._transaction(s,plan["operations"],d.cancel)
            rules=[r for r in d.config["firewall"].values() if r.get("name","").startswith("Luxiaoguan initial ")]
            self.assertEqual(len(rules),8)
            for zone in ("wan","uplink_custom"):
                selected=[r for r in rules if r["src"]==zone]
                self.assertEqual(len(selected),4)
                self.assertEqual({r["target"] for r in selected},{"DROP"})
                self.assertIn(["echo-request"],[r.get("icmp_type") for r in selected])
            self.assertEqual(d.config["network"],before["network"])
            self.assertEqual(d.config["wireless"],before["wireless"])
            self.assertEqual(d.config["firewall"]["zone_lan"],before["firewall"]["zone_lan"])
            next_plan=build_plan(d.policy.read(d.cancel),"initial",dict(items=["web","ping","igmp"]))
            self.assertFalse([o for o in next_plan["operations"] if o["action"]=="add"])

    def test_renamed_wan_zone_and_legacy_scalar_membership(self):
        d,s=self.snapshot();zone=s["configs"]["firewall"]["zone_wan"]
        zone.update(name="external",network="wan wwan")
        plan=build_plan(s,"initial",dict(items=["web"]))
        rules=[o for o in plan["operations"] if o["action"]=="add"]
        self.assertEqual(len(rules),1);self.assertEqual(rules[0]["values"]["src"],"external")

    def test_multiple_station_zones_are_covered_once_each(self):
        d,s=self.snapshot();s["configs"]["wireless"]["second"]=dict(s["configs"]["wireless"]["station"],network="backup")
        s["configs"]["network"]["backup"]={".type":"interface","proto":"dhcp"}
        s["configs"]["firewall"]["backup"]={".type":"zone","name":"backup","network":["backup"]}
        plan=build_plan(s,"initial",dict(items=["web"]))
        self.assertEqual({o["values"]["src"] for o in plan["operations"] if o["action"]=="add"},{"wan","backup"})

    def test_unzoned_or_ambiguous_upstream_is_rejected(self):
        for ambiguous in (False,True):
            d,s=self.snapshot()
            if ambiguous:s["configs"]["firewall"]["duplicate"]={".type":"zone","name":"other","network":["wwan"]}
            else:s["configs"]["firewall"]["zone_wan"]["network"]=["wan"]
            with self.assertRaises(ValueError):build_plan(s,"initial",dict(items=["web"]))
            self.assertFalse(d.commits)

    def test_shared_local_network_or_zone_never_blocks_local_management(self):
        for mode in ("lan_member","guest_member","direct_lan","physical_zone"):
            d,s=self.snapshot();zone=s["configs"]["firewall"]["zone_wan"]
            if mode=="lan_member":zone["network"].append("lan")
            elif mode=="guest_member":
                zone["network"].append("guest")
                s["configs"]["wireless"]["guest_ap"]={".type":"wifi-iface","mode":"ap","network":"guest"}
            elif mode=="direct_lan":s["configs"]["wireless"]["station"]["network"]="lan"
            else:zone["device"]=["br-lan"]
            with self.subTest(mode=mode),self.assertRaises(ValueError):build_plan(s,"initial",dict(items=["web"]))

    def test_implicit_zone_network_name_is_supported(self):
        d,s=self.snapshot()
        s["configs"]["firewall"]["zone_wan"]["network"]=["wan"]
        s["configs"]["firewall"]["wwan"]={".type":"zone","name":"wwan"}
        plan=build_plan(s,"initial",dict(items=["web"]))
        self.assertEqual({o["values"]["src"] for o in plan["operations"] if o["action"]=="add"},{"wan","wwan"})

    def test_duplicate_zone_names_or_extra_matching_are_rejected(self):
        for key in ("duplicate","extra_src"):
            d,s=self.snapshot()
            if key=="duplicate":s["configs"]["firewall"]["duplicate"]={".type":"zone","name":"wan","network":["other"]}
            else:s["configs"]["firewall"]["zone_wan"]["extra_src"]="-i br-lan"
            with self.subTest(key=key),self.assertRaises(ValueError):build_plan(s,"initial",dict(items=["web"]))

    def test_live_default_route_identifies_additional_wired_upstream(self):
        d,s=self.snapshot()
        s["configs"]["network"]["wired"]={".type":"interface","proto":"dhcp"}
        s["configs"]["firewall"]["wired"]={".type":"zone","name":"wired","network":["wired"]}
        s["runtime"]["interface"].append(dict(interface="wired",route=[dict(target="0.0.0.0",mask=0)]))
        plan=build_plan(s,"initial",dict(items=["web"]))
        self.assertEqual({o["values"]["src"] for o in plan["operations"] if o["action"]=="add"},{"wan","wired"})


class UncertainSaveFixTests(unittest.TestCase):
    def save(self,config,outcome,stage_error=None):
        manager=Mock();calls=[]
        def call(action,*args,**kwargs):
            calls.append(action)
            if action=="session":return dict(sid="a"*32)
            if action=="common_stage" and stage_error is not None:raise stage_error
            if action=="common_commit":
                if isinstance(outcome,Exception):raise outcome
                return outcome
            return {}
        manager._call.side_effect=call
        review=dict(config=config,section="lan" if config=="network" else "cfg01",token="a"*64,values={})
        values=dict(proto="static",auto="1",ipaddr="192.168.2.1",netmask="255.255.255.0") if config=="network" else dict(hostname="Router",timezone="UTC0",zonename="Etc/UTC")
        error=None
        with patch("modules.capture_services.openwrt.common_settings._manager",return_value=manager):
            try:save_settings(Mock(),review,values,threading.Event())
            except Exception as exc:error=exc
        return calls,error

    def test_uncertain_commit_keeps_session_and_never_retries(self):
        for config in ("network","system"):
            for code in ("timeout","transport","disconnected","response","save_recovery"):
                with self.subTest(config=config,code=code):
                    calls,error=self.save(config,SignalError(code,"injected"))
                    self.assertIsNotNone(error);self.assertNotIn("discard_session",calls)
                    self.assertEqual(calls.count("common_commit"),1)

    def test_malformed_result_or_unexpected_commit_failure_keeps_session(self):
        for outcome in (None,{},dict(saved=True,applied=True),OSError("injected")):
            calls,error=self.save("network",outcome)
            self.assertIsInstance(error,RuntimeError);self.assertNotIn("discard_session",calls)

    def test_verified_failure_and_stage_failure_still_release_session(self):
        calls,error=self.save("network",SignalError("save_commit","rollback verified"))
        self.assertIsInstance(error,SignalError);self.assertEqual(calls[-1],"discard_session")
        calls,error=self.save("network",{},stage_error=SignalError("changed","external change"))
        self.assertIsInstance(error,SignalError);self.assertNotIn("common_commit",calls)
        self.assertEqual(calls[-1],"discard_session")

    def test_success_still_releases_session(self):
        calls,error=self.save("network",dict(saved=True,applied=False))
        self.assertIsNone(error);self.assertEqual(calls[-1],"discard_session")
