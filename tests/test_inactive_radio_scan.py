"""Native inactive-radio scans must not require or enable an AP."""
import copy
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
from tests.test_wireless_setup import LuaDevice,LuaRuntime
from tests import test_signal_actions_ui as ui
from tests import test_standalone as standalone
from modules.capture_services.openwrt.wireless_setup_manager import WirelessSetupManager
from modules.capture_services.openwrt.wireless_setup_models import WirelessEnvironment,WirelessState
from modules.capture_services.openwrt.signal_manager import SignalError

class InactiveRadioDevice(LuaDevice):
    def __init__(self):
        super().__init__()
        for row in self.config["wireless"].values():row["disabled"]="1"
        self.temporary_exists=False
        self.leave_temporary=False
        self.scan_devices=[]
    def read(self,command):
        if command.startswith("if test -e /sys/class/net/"):
            return "busy" if self.temporary_exists else "clear"
        return super().read(command)
    def call(self,obj,method,args):
        values=self.unwrap(args)
        if obj=="iwinfo" and method=="scan":
            self.scan_devices.append(values["device"])
            self.temporary_exists=self.leave_temporary
        return super().call(obj,method,args)

@unittest.skipUnless(LuaRuntime,"Lua runtime unavailable")
class InactiveNativeTests(unittest.TestCase):
    def setUp(self):
        self.device=InactiveRadioDevice()
        self.manager=WirelessSetupManager(self.device)
        self.event=threading.Event()
    def test_native_scan_uses_verified_phy_and_preserves_all_disabled_configuration(self):
        before=copy.deepcopy(self.device.config)
        result=self.manager._call("scan",self.event,radio="radio7",interface="",expected_phy="phy2")
        self.assertTrue(result["results"])
        self.assertEqual(self.device.scan_devices,["phy2"])
        self.assertEqual(self.device.config,before)
        self.assertFalse(self.device.sessions)
        self.assertNotIn(("uci","apply"),self.device.calls)
        self.assertFalse(self.device.temporary_exists)
    def test_stale_phy_and_preexisting_temporary_interface_never_scan(self):
        for phy,busy in (("phy1",False),("phy2",True)):
            self.device.temporary_exists=busy
            with self.assertRaises(SignalError):
                self.manager._call("scan",self.event,radio="radio7",interface="",expected_phy=phy)
        self.assertEqual(self.device.scan_devices,[])
    def test_native_failure_keeps_configuration_and_reports_cleanup_separately(self):
        before=copy.deepcopy(self.device.config)
        self.device.fail=("iwinfo","scan")
        with self.assertRaises(SignalError) as error:
            self.manager._call("scan",self.event,radio="radio7",interface="",expected_phy="phy2")
        self.assertEqual(error.exception.code,"scan_native")
        self.assertEqual(self.device.config,before)
        self.device.leave_temporary=True
        with self.assertRaises(SignalError) as error:
            self.manager._call("scan",self.event,radio="radio7",interface="",expected_phy="phy2")
        self.assertEqual(error.exception.code,"scan_recovery")

class InactiveScanTests(unittest.TestCase):
    def test_manager_scans_empty_radio_without_using_another_radio(self):
        manager=WirelessSetupManager(None)
        manager.inspect=Mock(return_value=WirelessEnvironment(data={"radios":[
            {"name":"radio0","phy":"phy0","interfaces":["wlan0"]},
            {"name":"radio1","phy":"phy1","interfaces":[],"runtime_up":False}]}))
        now=[0.]
        def scan(action,cancel,**values):
            self.assertEqual((action,values["radio"],values["interface"],values["expected_phy"]),("scan","radio1","","phy1"))
            now[0]+=6
            return {"results":[]}
        manager._call=Mock(side_effect=scan)
        with patch("modules.capture_services.openwrt.wireless_setup_manager.time.monotonic",side_effect=lambda:now[0]):
            self.assertEqual(manager.scan(5,SimpleNamespace(is_set=lambda:False,wait=lambda _:False),Mock(),radio="radio1"),())
        manager._call.assert_called_once()

class InactiveScanUiTests(unittest.TestCase):
    create_page=ui.SignalActionTests.create_page
    def test_empty_disabled_radio_scan_button_is_clickable(self):
        from pathlib import Path
        from modules.config_manager import BASE_DIR
        from dataclasses import replace
        from modules.capture_services.openwrt.signal_models import SignalInventory
        output=BASE_DIR/"build/inactive-scan-review";output.mkdir(parents=True,exist_ok=True)
        for light in (False,True):
            setup=ui.PendingSetup()
            setup.snapshot=WirelessState(environment=WirelessEnvironment(data={"radios":[
                {"name":"radio0","phy":"phy0","band":"2g","interfaces":[],"runtime_up":False,"disabled":"1"},
                {"name":"radio1","phy":"phy1","band":"5g","interfaces":[],"runtime_up":False,"disabled":"1"}]}))
            setup.scan=Mock(return_value=True);setup.cancel=Mock()
            page,service=self.create_page(light,setup=setup)
            service.snapshot=replace(service.snapshot,inventory=SignalInventory())
            page.setup_panels._setup_changed(setup.snapshot)
            page.stack.setCurrentIndex(2)
            standalone.APP.processEvents()
            button=page.setup_panels.scan_buttons["radio1"]
            self.assertTrue(button.isEnabled())
            self.assertTrue(page.setup_panels.scan_buttons["radio0"].isEnabled())
            page.grab().save(str(output/("light.png" if light else "dark.png")))
            button.click()
            setup.scan.assert_called_once_with(10,"radio1")
