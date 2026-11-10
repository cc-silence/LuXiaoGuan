"""Regressions for OpenWrt single-address CIDR lists and legacy LAN settings."""
import copy
import json
import unittest
from tests.test_router_policy import PolicyDevice
from modules.capture_services.openwrt.router_policy_plan import build_plan


class LanCidrPolicyTests(unittest.TestCase):
    def device(self, address, netmask=None, legacy=False):
        d=PolicyDevice(legacy)
        row=d.config["network"]["lan"]
        row["ipaddr"]=copy.deepcopy(address)
        row.pop("netmask",None)
        if netmask is not None:row["netmask"]=netmask
        d.files["/etc/config/network"]=json.dumps(d.config["network"],sort_keys=True)
        return d

    def test_new_and_old_formats_use_existing_target_and_preserve_unrelated_settings(self):
        formats=[("192.168.8.1","255.255.255.0"),
                 (["192.168.8.1/24"],None),
                 (["192.168.8.1"],"255.255.255.0")]
        for legacy in (True,False):
            for address,mask in formats:
                with self.subTest(legacy=legacy,address=address):
                    d=self.device(address,mask,legacy)
                    before=copy.deepcopy(d.config)
                    snapshot=d.policy.read(d.cancel)
                    plan=build_plan(snapshot,"initial",dict(items=["lan"]))
                    self.assertEqual(d.config,before)
                    self.assertEqual(plan["new_address"],"192.168.11.1")
                    d.policy._transaction(snapshot,plan["operations"],d.cancel)
                    expected=copy.deepcopy(before)
                    expected["network"]["lan"].update(ipaddr="192.168.11.1",netmask="255.255.255.0")
                    self.assertEqual(d.config,expected)

    def test_special_and_invalid_list_configurations_stop_before_writes(self):
        for address,mask,gateway in [([],None,None),
                (["192.168.8.1/24","192.168.9.1/24"],None,None),
                (["invalid/24"],None,None),(["192.168.8.1/33"],None,None),
                (["192.168.8.1"],None,None),([42],None,None),
                (["192.168.8.1/24"],None,"192.168.8.254"),
                ("192.168.8.1","255.255.255.0","192.168.8.254")]:
            with self.subTest(address=address,gateway=gateway):
                d=self.device(address,mask)
                if gateway:d.config["network"]["lan"]["gateway"]=gateway
                before=copy.deepcopy(d.config)
                with self.assertRaises(ValueError):
                    build_plan(d.policy.read(d.cancel),"initial",dict(items=["lan"]))
                self.assertEqual(d.config,before)
                self.assertFalse(d.commits)

    def test_new_format_still_rejects_upstream_subnet_conflicts(self):
        d=self.device(["192.168.8.1/24"])
        with self.assertRaises(ValueError):
            build_plan(d.policy.read(d.cancel),"initial",dict(items=["lan"],lan_ip="10.2.3.1"))
        self.assertFalse(d.commits)

    def test_original_cidr_list_runtime_is_verified_after_restore(self):
        for address,mask in [(["192.168.8.1/24"],None),
                             ("192.168.8.1","255.255.255.0")]:
            with self.subTest(address=address):
                d=self.device(address,mask)
                op=dict(action="set",config="network",section="lan",values=dict(ipaddr=address))
                from unittest.mock import patch
                original=d.call
                def call(obj,method,args):
                    if obj=="network.interface.lan":
                        return d.wrap(dict(up=True,pending=False,**d.interfaces[0])),None
                    return original(obj,method,args)
                with patch.object(d.lua.globals(),"bridge_call",call):
                    result=d.policy._call("policy_applied",d.cancel,operations=[op])
                    self.assertTrue(result["ready"])
                    d.interfaces[0]["ipv4-address"][0]["address"]="192.168.99.1"
                    result=d.policy._call("policy_applied",d.cancel,operations=[op])
                    self.assertFalse(result["ready"])

    def test_saved_address_type_must_match_before_runtime_is_checked(self):
        d=self.device(["192.168.8.1/24"])
        op=dict(action="set",config="network",section="lan",values=dict(ipaddr="192.168.8.1"))
        self.assertFalse(d.policy._call("policy_applied",d.cancel,operations=[op])["ready"])

    def test_common_lan_editor_save_preserves_cidr_list_and_other_settings(self):
        from modules.capture_services.openwrt.common_settings import read_settings,save_settings
        for address,mask in [(["192.168.8.1/24"],None),("192.168.8.1","255.255.255.0")]:
            with self.subTest(address=address):
                d=self.device(address,mask)
                review=read_settings(d,"network",d.cancel)
                self.assertEqual(review["values"]["ipaddr"],address)
                values=dict(review["values"],auto="1")
                before=copy.deepcopy(d.config)
                result=save_settings(d,review,values,d.cancel)
                self.assertTrue(result["saved"])
                before["network"]["lan"].update(values)
                self.assertEqual(d.config,before)

    def test_full_initial_plan_accepts_cidr_without_mutating_snapshot(self):
        from tests.test_router_policy import environment
        d=self.device(["192.168.8.1/24"])
        snapshot=d.policy.read(d.cancel);before=copy.deepcopy(snapshot)
        plan=build_plan(snapshot,"initial",environment=environment())
        self.assertEqual(snapshot,before)
        self.assertEqual(plan["new_address"],"192.168.11.1")
        self.assertTrue(plan["new_ap"])
        self.assertFalse(d.commits)

    def test_checked_apply_failure_restores_original_cidr_configuration(self):
        from unittest.mock import Mock,patch
        from tests.test_checked_apply import fast_deadlines
        import ipaddress
        d=self.device(["192.168.8.1/24"])
        original=d.call
        def call(obj,method,args):
            if obj=="network.interface.lan":
                row=d.config["network"]["lan"]
                addresses=row["ipaddr"]
                if isinstance(addresses,str):addresses=[addresses]
                state=[ipaddress.IPv4Interface(v if "/" in v else v+"/"+row["netmask"]) for v in addresses]
                return d.wrap(dict(up=True,pending=False,proto="static",device="br-lan",
                    **{"ipv4-address":[dict(address=str(v.ip),mask=v.network.prefixlen) for v in state]})),None
            return original(obj,method,args)
        d.lua.globals().bridge_call=call
        review=d.policy.preview("initial",dict(items=["lan"]),d.cancel)
        before=copy.deepcopy(d.config)
        factory=d.policy._apply_channel_factory
        def channel(*args,**kwargs):
            remote=factory(*args,**kwargs);base=remote.call
            def remote_call(action,**values):
                result=base(action,**values)
                if action=="policy_applied" and d.config["network"]["lan"]["ipaddr"]!=before["network"]["lan"]["ipaddr"]:
                    return dict(ready=False)
                return result
            remote.call=remote_call
            return remote
        with patch.object(d.policy,"_apply_channel_factory",side_effect=channel),fast_deadlines():
            from modules.capture_services.openwrt.checked_apply import ApplyRestored
            with self.assertRaises(RuntimeError) as failure:d.policy.apply(review,None,None,d.cancel,Mock())
        self.assertIsInstance(failure.exception.__cause__,ApplyRestored)
        self.assertEqual(d.config,before)
        self.assertIn(("uci","rollback"),d.calls)
        self.assertNotIn(("uci","confirm"),d.calls)
