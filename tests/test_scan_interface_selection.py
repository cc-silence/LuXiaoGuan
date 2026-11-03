"""Three radios: a vanished first AP must not hide a live same-radio STA."""
import copy
import threading
import unittest
from tests.test_wireless_setup import LuaDevice,LuaRuntime
from modules.capture_services.openwrt.wireless_setup_manager import WirelessSetupManager
from modules.capture_services.openwrt.signal_manager import SignalError


class ThreeRadioDevice(LuaDevice):
    def __init__(self):
        super().__init__()
        self.status={
            "radio0":{"up":True,"pending":False,"interfaces":[{"ifname":"wlan0"}]},
            "radio1":{"up":True,"pending":False,"interfaces":[{"ifname":"wlan1"},{"ifname":"wlan1-1"}]},
            "radio2":{"up":True,"pending":False,"interfaces":[{"ifname":"wlan2"}]}}
        self.missing={"wlan1"}
        self.down=set()
        self.permission=set()
        self.scanned=[]

    def call(self,obj,method,args):
        values=self.unwrap(args)
        if obj=="network.wireless" and method=="status":
            return self.wrap(self.status),None
        if obj=="network.device" and method=="status":
            iface=values["name"]
            if iface in self.permission:return None,6
            if iface in self.missing:return None,2
            return self.wrap({"up":iface not in self.down}),None
        if obj=="iwinfo" and method=="scan":
            self.scanned.append(values["device"])
        return super().call(obj,method,args)


@unittest.skipUnless(LuaRuntime,"Lua unavailable")
class ScanInterfaceSelectionTests(unittest.TestCase):
    def call(self,device,interface="wlan1",radio="radio1"):
        return WirelessSetupManager(device)._call("scan",threading.Event(),radio=radio,interface=interface)

    def test_missing_first_ap_scans_live_sta_on_the_same_radio(self):
        device=ThreeRadioDevice();before=copy.deepcopy(device.config)
        value=self.call(device)
        self.assertTrue(value["results"])
        self.assertEqual(device.scanned,["wlan1-1"])
        self.assertEqual(device.config,before)
        self.assertFalse(device.sessions)

    def test_active_first_interface_is_preserved_and_other_radios_are_not_borrowed(self):
        device=ThreeRadioDevice();device.missing.clear()
        self.call(device)
        self.assertEqual(device.scanned,["wlan1"])
        device=ThreeRadioDevice();device.missing.add("wlan1-1")
        with self.assertRaises(SignalError) as error:self.call(device)
        self.assertEqual(error.exception.code,"scan_no_interface")
        self.assertEqual(device.scanned,[])

    def test_ambiguous_wrong_radio_pending_and_permission_never_scan(self):
        for kind in ("duplicate","wrong","pending","permission"):
            device=ThreeRadioDevice()
            if kind=="duplicate":device.status["radio2"]["interfaces"].append({"ifname":"wlan1-1"})
            if kind=="pending":device.status["radio1"]["pending"]=True
            if kind=="permission":device.permission.add("wlan1")
            interface="wlan0" if kind=="wrong" else "wlan1"
            with self.assertRaises(SignalError):self.call(device,interface)
            self.assertEqual(device.scanned,[])

    def test_preferred_interface_removed_from_fresh_status_uses_only_same_radio(self):
        device=ThreeRadioDevice()
        device.status["radio1"]["interfaces"]=[{"ifname":"wlan1-1"}]
        value=self.call(device)
        self.assertTrue(value["results"])
        self.assertEqual(device.scanned,["wlan1-1"])

    def test_fallback_records_which_candidate_was_selected(self):
        value=self.call(ThreeRadioDevice())
        self.assertEqual(value["interface_selection"],dict(
            candidates=2,unavailable=1,selected_index=2,interface="wlan1-1"))
