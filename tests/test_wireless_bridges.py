"""Exercise real Lua bridge staging, rollback, edits and restoration offline."""
import copy
import json
import threading
import unittest
from dataclasses import replace
from unittest.mock import patch
from tests.test_wireless_setup import LuaDevice, LuaRuntime, TARGET
from modules.capture_services.openwrt.wireless_setup_manager import WirelessSetupManager
from modules.capture_services.openwrt.wireless_setup_models import WirelessRequest

class RuntimeDevice(LuaDevice):
    def __init__(self):
        super().__init__()
        self.relay_running=True
        self.bridge_member=True
        self.overlap=False
    def call(self,obj,method,args):
        values=self.unwrap(args)
        if obj.startswith("network.interface."):
            network=obj.split(".")[-1]
            if network=="lan":
                return self.wrap({"up":True,"l3_device":"br-lan","ipv4-address":[{"address":"192.168.8.1","mask":24}]}),None
            section=next((key for key,row in self.config["wireless"].items() if row.get("mode")=="sta" and row.get("network")==network),"")
            return self.wrap({"up":True,"l3_device":"iface-"+section,"ipv4-address":[{"address":"192.168.8.22" if self.overlap else "192.168.1.22","mask":24}]}),None
        if obj=="network.device" and values.get("name")=="br-lan":
            members=["iface-"+key for key,row in self.config["wireless"].items() if row.get("mode")=="sta" and row.get("network")=="lan"] if self.bridge_member else []
            return self.wrap({"up":True,"bridge-members":members}),None
        if obj=="service":
            section=next((key for key,row in self.config["wireless"].items() if row.get("mode")=="sta"),"")
            return self.wrap({"relayd":{"instances":{"relay":{"running":self.relay_running,"command":["/usr/sbin/relayd","-I","br-lan","-I","iface-"+section]}}}}),None
        return super().call(obj,method,args)

@unittest.skipUnless(LuaRuntime, "Lua runtime unavailable")
class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.device=RuntimeDevice()
        self.device.config["dhcp"]={"lan": {".type":"dhcp","interface":"lan","ignore":"0","ra":"server","dhcpv6":"server"}}
        self.device.config["firewall"]["zone_lan"].update(input="ACCEPT",output="ACCEPT",forward="ACCEPT")
        self.manager=WirelessSetupManager(self.device)
        self.cancel=threading.Event()
        self.message=lambda *_:None

    def create(self,mode):
        proof=self.manager.verify(TARGET,"test-password",self.cancel,self.message)
        options=dict(encryption="psk2",channel="36",bssid=TARGET.bssid,bridge_mode=mode,network="lan",proto="dhcp",zone="lan" if mode=="relayd" else "wan")
        request=WirelessRequest("sta","radio7",TARGET.ssid,"test-password",options=options,verification=proof["token"])
        value=self.manager.save(request,self.cancel,self.message)
        return request,value

    def test_relay_uses_separate_uplink_and_preserves_dhcp_backup(self):
        request,value=self.create("relayd")
        wifi=self.device.config["wireless"][value["section"]]
        relay=self.device.config["network"][wifi["rm_bridge"]]
        self.assertEqual(relay["proto"],"relay")
        self.assertEqual(relay["network"],["lan",value["network"]])
        self.assertEqual(self.device.config["dhcp"]["lan"]["ignore"],"1")
        self.assertEqual(json.loads(relay["rm_dhcp_backup"])[0]["values"]["ignore"],"0")
        self.assertIn(value["network"],self.device.config["firewall"]["zone_lan"]["network"])
        self.assertNotIn(value["network"],self.device.config["firewall"]["zone_wan"]["network"])
        original=relay["rm_dhcp_backup"]
        edited=replace(request,section=value["section"],password="")
        with patch.object(self.manager,"_wait_ready"):self.manager.save(edited,self.cancel,self.message)
        self.assertEqual(self.device.config["network"][wifi["rm_bridge"]]["rm_dhcp_backup"],original)

    def test_relay_to_routed_restores_dhcp_and_removes_owned_bridge(self):
        request,value=self.create("relayd")
        owned=self.device.config["wireless"][value["section"]]["rm_bridge"]
        request=replace(request,section=value["section"],password="",options=dict(encryption="psk2",bridge_mode="routed",proto="dhcp",zone="wan"))
        with patch.object(self.manager,"_wait_ready"):self.manager.save(request,self.cancel,self.message)
        self.assertNotIn(owned,self.device.config["network"])
        self.assertEqual(self.device.config["dhcp"]["lan"],{".type":"dhcp","interface":"lan","ignore":"0","ra":"server","dhcpv6":"server"})
        self.assertNotIn("rm_bridge",self.device.config["wireless"][value["section"]])

    def test_wds_connects_existing_lan_then_can_restore_routed(self):
        request,value=self.create("wds")
        wifi=self.device.config["wireless"][value["section"]]
        self.assertEqual((wifi["network"],wifi["wds"]),("lan","1"))
        self.assertEqual(self.device.config["dhcp"]["lan"]["ignore"],"1")
        request=replace(request,section=value["section"],password="",options=dict(encryption="psk2",bridge_mode="routed",proto="dhcp",zone="wan"))
        with patch.object(self.manager,"_wait_ready"):updated=self.manager.save(request,self.cancel,self.message)
        self.assertNotEqual(updated["network"],"lan")
        self.assertEqual(self.device.config["dhcp"]["lan"]["ignore"],"0")

    def test_bridge_failure_restores_all_four_configs(self):
        proof=self.manager.verify(TARGET,"test-password",self.cancel,self.message)
        original=copy.deepcopy(self.device.config)
        request=WirelessRequest("sta","radio7",TARGET.ssid,"test-password",verification=proof["token"],options=dict(encryption="psk2",bridge_mode="relayd",network="lan",zone="lan"))
        with patch.object(self.manager,"_wait_ready",side_effect=InterruptedError("cancel")):
            with self.assertRaises(InterruptedError):self.manager.save(request,self.cancel,self.message)
        self.assertEqual(self.device.config,original)
        self.assertFalse(self.device.sessions)

    def test_non_lan_firewall_policy_is_rejected_and_restored(self):
        proof=self.manager.verify(TARGET,"test-password",self.cancel,self.message)
        original=copy.deepcopy(self.device.config)
        request=WirelessRequest("sta","radio7",TARGET.ssid,"test-password",verification=proof["token"],options=dict(encryption="psk2",bridge_mode="relayd",network="lan",zone="wan"))
        with self.assertRaisesRegex(Exception,"."):self.manager.save(request,self.cancel,self.message)
        self.assertEqual(self.device.config,original)

    def test_authentication_alone_does_not_confirm_relay_or_wds(self):
        for mode in ("relayd","wds"):
            with self.subTest(mode=mode):
                self.setUp()
                request,value=self.create(mode)
                self.device.relay_running=False
                self.device.bridge_member=False
                status=self.manager._call("status",self.cancel,radio="radio7",section=value["section"],mode="sta",bridge_mode=mode,network=value["network"],downstream="lan")
                self.assertFalse(status["ready"])

    def test_overlapping_subnets_restore_before_confirm(self):
        proof=self.manager.verify(TARGET,"test-password",self.cancel,self.message)
        original=copy.deepcopy(self.device.config)
        self.device.overlap=True
        request=WirelessRequest("sta","radio7",TARGET.ssid,"test-password",verification=proof["token"],options=dict(encryption="psk2",bridge_mode="relayd",network="lan",zone="lan"))
        with self.assertRaisesRegex(Exception,"."):self.manager.save(request,self.cancel,self.message)
        self.assertEqual(self.device.config,original)
        self.assertFalse(self.device.sessions)

    def test_new_dhcp_section_is_removed_when_leaving_bridge(self):
        self.device.config["dhcp"]={}
        request,value=self.create("wds")
        self.assertEqual(len(self.device.config["dhcp"]),1)
        request=replace(request,section=value["section"],password="",options=dict(encryption="psk2",bridge_mode="routed",proto="dhcp",zone="wan"))
        self.manager.save(request,self.cancel,self.message)
        self.assertEqual(self.device.config["dhcp"],{})

    def test_ap_multi_network_binding_cipher_and_mac_access_survive_save(self):
        self.device.config["network"]["guest"]={".type":"interface","proto":"static","ipaddr":"192.168.9.1"}
        self.device.config["wireless"]["home"]["network"]=["lan","guest"]
        request=WirelessRequest("ap","radio7","Renamed",section="home",options=dict(encryption="psk2+ccmp",macfilter="allow",maclist="02:11:22:33:44:55",ieee80211r="1",distance="100"))
        self.manager.save(request,self.cancel,self.message)
        home=self.device.config["wireless"]["home"]
        self.assertEqual(home["network"],["lan","guest"])
        self.assertEqual(home["key"],"original-password")
        self.assertEqual(home["maclist"],["02:11:22:33:44:55"])
        self.assertEqual(self.device.config["wireless"]["radio7"]["distance"],"100")

    def test_owe_needs_no_password_and_removes_old_key(self):
        request=WirelessRequest("ap","radio7","OpenEncryption",section="home",options=dict(encryption="owe"))
        self.manager.save(request,self.cancel,self.message)
        self.assertNotIn("key",self.device.config["wireless"]["home"])

    def test_bridge_switches_restore_and_reapply_dhcp(self):
        for mode in ("wds","relayd"):
            with self.subTest(mode=mode):
                self.setUp()
                request,value=self.create(mode)
                self.manager.toggle(value["section"],False,self.cancel)
                self.assertEqual(self.device.config["dhcp"]["lan"]["ignore"],"0")
                self.assertEqual(self.device.config["wireless"][value["section"]]["disabled"],"1")
                self.manager.toggle(value["section"],True,self.cancel)
                self.assertEqual(self.device.config["dhcp"]["lan"]["ignore"],"1")
                self.assertEqual(self.device.config["wireless"][value["section"]]["disabled"],"0")
                self.assertNotIn("rm_suspended",self.device.config["wireless"][value["section"]])

    def test_edit_suspended_bridge_reenables_dhcp_protection(self):
        for mode in ("wds","relayd"):
            with self.subTest(mode=mode):
                self.setUp()
                request,value=self.create(mode)
                self.manager.toggle(value["section"],False,self.cancel)
                edited=replace(request,section=value["section"],password="")
                self.manager.save(edited,self.cancel,self.message)
                self.assertEqual(self.device.config["dhcp"]["lan"]["ignore"],"1")
                self.assertNotIn("rm_suspended",self.device.config["wireless"][value["section"]])

    def test_leaving_suspended_bridge_preserves_restored_dhcp(self):
        request,value=self.create("relayd")
        self.manager.toggle(value["section"],False,self.cancel)
        edited=replace(request,section=value["section"],password="",options=dict(encryption="psk2",bridge_mode="routed",proto="dhcp",zone="wan"))
        self.manager.save(edited,self.cancel,self.message)
        self.assertEqual(self.device.config["dhcp"]["lan"]["ignore"],"0")

    def test_legacy_switch_entry_delegates_managed_bridge_transaction(self):
        from modules.capture_services.openwrt.signal_models import RouterSignal,SignalInventory
        from modules.capture_services.openwrt.signal_manager import RouterSignalManager
        signal=RouterSignal("managed","Upstream","sta","radio7","5g",False,False,"active",managed_bridge=True)
        inventory=SignalInventory((signal,),"a"*64)
        manager=RouterSignalManager(self.device)
        with patch("modules.capture_services.openwrt.wireless_setup_manager.WirelessSetupManager") as native,patch.object(manager,"read",return_value=inventory):
            manager.change(inventory,"managed",False,self.cancel)
        native.return_value.toggle.assert_called_once_with("managed",False,self.cancel,expected_revision="a"*64)

    def test_external_dhcp_change_is_not_silently_overwritten(self):
        request,value=self.create("wds")
        self.device.config["dhcp"]["lan"]["ignore"]="0"
        original=copy.deepcopy(self.device.config)
        with self.assertRaises(Exception):
            self.manager.save(replace(request,section=value["section"],password=""),self.cancel,self.message)
        self.assertEqual(self.device.config,original)

    def test_second_bridge_cannot_claim_the_same_lan(self):
        self.create("relayd")
        original=copy.deepcopy(self.device.config)
        proof=self.manager.verify(TARGET,"test-password",self.cancel,self.message)
        request=WirelessRequest("sta","radio7",TARGET.ssid,"test-password",verification=proof["token"],options=dict(encryption="psk2",bridge_mode="wds",network="lan"))
        with self.assertRaises(Exception):self.manager.save(request,self.cancel,self.message)
        self.assertEqual(self.device.config,original)

    def test_restore_waits_for_settled_exact_runtime(self):
        original_call=self.manager._call
        runtime_reads=[]
        def call(action,*args,**kwargs):
            result=original_call(action,*args,**kwargs)
            if action=="runtime":
                runtime_reads.append(result)
                if len(runtime_reads)==1:
                    return dict(result,pending=True,active=result["active"]+["unrestored-temp"])
            return result
        cleanup=threading.Event()
        with patch.object(self.manager,"_call",side_effect=call),patch("modules.capture_services.openwrt.wireless_setup_manager.threading.Event",return_value=cleanup),patch.object(cleanup,"wait",return_value=False):
            self.manager.verify(TARGET,"test-password",self.cancel,self.message)
        self.assertGreaterEqual(len(runtime_reads),2)
