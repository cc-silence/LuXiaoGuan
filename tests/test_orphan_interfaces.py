"""Exercise orphan selection against the real Lua and isolated UCI persistence."""
import copy
import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from tests.test_save_only import PersistenceDevice
from tests.test_wireless_setup import LuaRuntime, WirelessRequest
from modules.capture_services.openwrt.wireless_setup_manager import WirelessSetupManager
from modules.capture_services.openwrt.orphan_interface_manager import OrphanInterfaceManager
from modules.capture_services.openwrt.signal_manager import SignalError

ORPHAN="wbnet_0123456789abcdef"

class OrphanDevice(PersistenceDevice):
    def __init__(self):
        super().__init__()
        self.ssh_client=SimpleNamespace(host_fingerprint="SHA256:offline-router")
        self.interface_states=[]
        self.dump_missing=False
        self.config["network"][ORPHAN]={".type":"interface","proto":"dhcp","rm_owner":"wb_missing"}
        self.config["network"]["wwan9"]={".type":"interface","proto":"dhcp"}
        self.config["firewall"]["zone_wan"]["network"]=["wan",ORPHAN,"wwan9"]
        self.sync_files()

    def sync_files(self):
        self.files.update({"/etc/config/"+k:json.dumps(v,sort_keys=True) for k,v in self.config.items()})

    def call(self,obj,method,values):
        if obj=="network.interface" and method=="dump":
            self.calls.append((obj,method))
            return self.wrap({} if self.dump_missing else {"interface":self.interface_states})
        return super().call(obj,method,values)


@unittest.skipUnless(LuaRuntime,"Lua runtime unavailable")
class OrphanInterfaceTests(unittest.TestCase):
    def setUp(self):
        self.device=OrphanDevice()
        self.wireless=WirelessSetupManager(self.device)
        self.manager=OrphanInterfaceManager(self.wireless)
        self.cancel=threading.Event()

    def review(self):return self.manager.preview(self.cancel)
    def cleanup(self,review=None,selected=None):
        return self.manager.cleanup(review or self.review(),selected or [ORPHAN],self.cancel,Mock())

    def test_preview_is_read_only_and_unknown_wwan_never_selected(self):
        before=copy.deepcopy(self.device.files)
        rows={r["section"]:r for r in self.review()["rows"]}
        self.assertTrue(rows[ORPHAN]["eligible"])
        self.assertFalse(rows["wwan9"]["eligible"])
        self.assertIn("unowned",rows["wwan9"]["reasons"])
        self.assertEqual(self.device.files,before)
        self.assertFalse(self.device.commits)
        self.assertFalse(self.device.sessions)
        self.assertNotIn("original-password",json.dumps(rows))
        with self.assertRaises(SignalError):self.cleanup(selected=["wwan9"])
        self.assertFalse(self.device.commits)

    def test_selected_cleanup_preserves_every_other_configuration_and_never_applies(self):
        before=copy.deepcopy(self.device.config)
        result=self.cleanup()
        self.assertEqual(result["removed"],[ORPHAN])
        del before["network"][ORPHAN]
        before["firewall"]["zone_wan"]["network"]=["wan","wwan9"]
        self.assertEqual(self.device.config,before)
        self.assertFalse(result["applied"])
        self.assertNotIn(("uci","apply"),self.device.calls)
        self.assertNotIn(("uci","commit"),self.device.calls)
        self.assertFalse(self.device.sessions)
        self.assertFalse(any("rouxiaoguan-save" in p for p in self.device.files))

    def test_active_references_physical_bindings_and_owner_are_protected(self):
        cases=(
            ("wireless","home","network",ORPHAN,"wireless_reference"),
            ("network","route_test","interface",ORPHAN,"network_reference"),
            ("dhcp","pool_test","interface",ORPHAN,"dhcp_reference"),
            ("firewall","rule_test","src",ORPHAN,"firewall_reference"),
            ("network",ORPHAN,"device","eth0","physical_binding"),
            ("network",ORPHAN,"rm_owner","home","owner_exists"),
        )
        original=copy.deepcopy(self.device.config)
        for config,section,key,value,reason in cases:
            with self.subTest(reason=reason):
                self.device.config=copy.deepcopy(original)
                self.device.config[config].setdefault(section,{".type":"route"})[key]=value
                self.device.sync_files()
                row=next(r for r in self.review()["rows"] if r["section"]==ORPHAN)
                self.assertFalse(row["eligible"])
                self.assertIn(reason,row["reasons"])

    def test_runtime_change_and_stale_revision_block_previously_approved_cleanup(self):
        review=self.review()
        self.device.interface_states=[{"interface":ORPHAN,"up":True,"pending":False}]
        with self.assertRaises(SignalError):self.cleanup(review)
        self.assertIn(ORPHAN,self.device.config["network"])
        self.assertFalse(self.device.commits)
        self.device.interface_states=[]
        self.device.config["wireless"]["home"]["ssid"]="Changed"
        self.device.sync_files()
        with self.assertRaises(SignalError):self.cleanup(review)
        self.assertFalse(self.device.commits)

    def test_missing_runtime_dump_and_unknown_runtime_state_fail_closed(self):
        self.device.dump_missing=True
        with self.assertRaises(SignalError):self.review()
        self.device.dump_missing=False
        self.device.interface_states=[{"interface":ORPHAN,"up":False}]
        row=next(r for r in self.review()["rows"] if r["section"]==ORPHAN)
        self.assertFalse(row["eligible"])
        self.assertIn("runtime_unknown",row["reasons"])

    def test_partial_commit_failure_rolls_back_and_fingerprint_change_blocks(self):
        review=self.review()
        before=copy.deepcopy(self.device.config)
        self.device.commit_fail="firewall"
        with self.assertRaises(SignalError):self.cleanup(review)
        self.assertEqual(self.device.config,before)
        self.assertFalse(self.device.sessions)
        self.device.ssh_client.host_fingerprint="SHA256:other-router"
        count=len(self.device.commits)
        with self.assertRaises(SignalError):self.cleanup(review)
        self.assertEqual(len(self.device.commits),count)

    def test_duplicate_selection_and_cancel_do_not_commit(self):
        review=self.review()
        with self.assertRaises(SignalError):self.cleanup(review,[ORPHAN,ORPHAN])
        self.cancel.set()
        with self.assertRaises(InterruptedError):self.cleanup(review)
        self.assertFalse(self.device.commits)

if __name__=="__main__":unittest.main()
