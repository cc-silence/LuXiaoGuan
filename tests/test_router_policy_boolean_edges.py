"""Common hotspot initialization uses the same native boolean contract as signals."""
import json
import unittest
from unittest.mock import Mock,patch
from tests.test_router_policy import PolicyDevice,PolicyChannel,environment,LuaRuntime
from modules.capture_services.openwrt.router_policy_plan import build_plan

@unittest.skipUnless(LuaRuntime,"Lua test runtime unavailable")
class PolicyBooleanEdgeTests(unittest.TestCase):
    def create(self,value,legacy=False):
        device=PolicyDevice(legacy)
        if value is not None:device.config["wireless"]["radio7"]["disabled"]=value
        device.files["/etc/config/wireless"]=json.dumps(device.config["wireless"],sort_keys=True)
        policy=device.policy;snapshot=policy.read(device.cancel)
        policy._apply_channel=PolicyChannel(device);policy._apply_host="192.168.8.1"
        plan=build_plan(snapshot,"initial",dict(items=["replace_ap"]),environment())
        call=policy._call;run=policy._run
        def ready(action,*args,**kwargs):
            if action=="ap_ready":
                self.assertNotIn(device.config["wireless"]["radio7"].get("disabled"),("1","true"))
                return dict(ready=True)
            return call(action,*args,**kwargs)
        def reload(name,*args,**kwargs):return "" if name=="wifi_reload" else run(name,*args,**kwargs)
        with patch.object(policy,"_call",side_effect=ready),patch.object(policy,"_run",side_effect=reload):
            result=policy._create_ap(plan,snapshot,device.cancel,Mock())
        self.assertIn("default_radio7",device.config["wireless"])
        self.assertEqual(device.config["wireless"]["home"]["key"],"original-password")
        self.assertIn(plan["new_ap"]["section"],result["configs"]["wireless"])
        return device

    def test_disabled_true_and_one_work_for_old_and_new_network_layouts(self):
        for legacy in (True,False):
            for flag in ("true","1"):
                with self.subTest(legacy=legacy,flag=flag):
                    device=self.create(flag,legacy)
                    self.assertEqual(device.config["wireless"]["radio7"]["disabled"],"0")

    def test_enabled_parent_formats_are_preserved(self):
        for flag in (None,"false","0"):
            with self.subTest(flag=flag):
                device=self.create(flag)
                self.assertEqual(device.config["wireless"]["radio7"].get("disabled"),flag)

    def test_unknown_parent_switch_never_starts_configuration_commit(self):
        device=PolicyDevice();device.config["wireless"]["radio7"]["disabled"]="unknown"
        device.files["/etc/config/wireless"]=json.dumps(device.config["wireless"],sort_keys=True)
        policy=device.policy;snapshot=policy.read(device.cancel)
        plan=build_plan(snapshot,"initial",dict(items=["replace_ap"]),environment())
        with patch.object(policy,"_transaction") as commit:
            with self.assertRaises(ValueError):policy._create_ap(plan,snapshot,device.cancel,Mock())
        commit.assert_not_called()
        self.assertFalse(device.commits)
