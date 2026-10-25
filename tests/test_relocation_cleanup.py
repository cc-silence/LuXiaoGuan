"""Whole-old-network cleanup, shared relays and rollback use the real Lua."""
import copy
import json
import threading
import unittest
from unittest.mock import Mock
from tests.test_relocation import MovingDevice
from modules.capture_services.openwrt.relocation_manager import RelocationManager
from modules.capture_services.openwrt.wireless_setup_manager import WirelessSetupManager
from modules.capture_services.openwrt.signal_manager import SignalError


class RelocationCleanupTests(unittest.TestCase):
    def setUp(self):
        self.device=MovingDevice()
        self.manager=RelocationManager(WirelessSetupManager(self.device))
        self.cancel=threading.Event()

    def seed_old_networks(self):
        for i in range(15):
            name="wwan" if not i else "wwan"+str(i)
            self.device.config["network"][name]={".type":"interface","proto":"dhcp"}
            if i<10:
                self.device.config["wireless"]["old"+str(i)]={".type":"wifi-iface","mode":"sta","network":name,"device":"radio7"}
            self.device.config["firewall"]["zone_wan"]["network"].append(name)

    def test_all_15_old_uplinks_are_deleted_without_reading_their_mac(self):
        self.seed_old_networks()
        review=self.manager.preview(self.cancel)
        self.assertEqual(len(review["removed"]),15)
        self.assertFalse(any(n.startswith("wwan") for n in review["skipped"]))
        self.manager.apply(review,self.cancel,Mock())
        self.assertFalse(any(n.startswith("wwan") for n in self.device.config["network"]))
        self.assertEqual(self.device.config["firewall"]["zone_wan"]["network"],["wan"])
        self.assertEqual(self.device.config["network"]["lan"]["ipaddr"],"192.168.8.1")
        self.assertFalse(any(r.get(".type")=="wifi-iface" for r in self.device.config["wireless"].values()))

    def test_shared_relays_routes_and_address_pools_are_removed_together(self):
        self.seed_old_networks()
        network=self.device.config["network"]
        network["relayA"]={".type":"interface","proto":"relay","network":["lan","wwan10"]}
        network["relayB"]={".type":"interface","proto":"relay","network":["lan","wwan10","wwan14"]}
        network["dependent"]={".type":"interface","proto":"dhcp","device":"@relayA"}
        network["old_route"]={".type":"route","interface":"wwan10","target":"10.0.0.0"}
        self.device.config["dhcp"]["old_pool"]={".type":"dhcp","interface":"wwan14"}
        review=self.manager.preview(self.cancel)
        self.assertTrue({"relayA","relayB","dependent"}.issubset(review["removed"]))
        self.manager.apply(review,self.cancel,Mock())
        for section in ("relayA","relayB","dependent","old_route"):
            self.assertNotIn(section,self.device.config["network"])
        self.assertNotIn("old_pool",self.device.config["dhcp"])
        self.assertIn("lan",self.device.config["network"])
        self.assertIn("wan",self.device.config["network"])

    def test_orphan_relay_restores_lan_address_assignment_before_removal(self):
        original={"ignore":"0","ra":"server","dhcpv6":"server"}
        backup=json.dumps([dict(section="lan",interface="lan",values=original)])
        self.device.config["dhcp"]["lan"]=dict(original,**{".type":"dhcp","interface":"lan","ignore":"1","ra":"disabled","dhcpv6":"disabled","ndp":"disabled"})
        self.device.config["network"]["oldrelay"]={".type":"interface","proto":"relay","network":["lan","wwan10"],"rm_dhcp_backup":backup}
        review=self.manager.preview(self.cancel)
        self.manager.apply(review,self.cancel,Mock())
        self.assertNotIn("oldrelay",self.device.config["network"])
        self.assertEqual(self.device.config["dhcp"]["lan"],dict(original,**{".type":"dhcp","interface":"lan"}))

    def test_failed_confirmation_restores_all_old_networks_and_references(self):
        self.seed_old_networks()
        before=copy.deepcopy(self.device.config)
        review=self.manager.preview(self.cancel)
        original=self.manager._call
        def fail_status(action,*args,**kwargs):
            if action=="move_status":raise SignalError("transport","offline connection loss")
            return original(action,*args,**kwargs)
        self.manager._call=fail_status
        with self.assertRaises(SignalError):self.manager.apply(review,self.cancel,Mock())
        self.assertEqual(self.device.config,before)

    def test_base_lan_dependency_is_explicitly_rejected_without_deletion(self):
        self.device.config["network"]["wwan10"]={".type":"interface","proto":"dhcp"}
        self.device.config["network"]["lan"]["network"]=["wwan10"]
        with self.assertRaises(SignalError):self.manager.preview(self.cancel)
        self.assertNotIn(("uci","apply"),self.device.calls)
