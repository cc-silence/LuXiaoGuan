"""An empty rpcd delta is not permission denial and never bypasses verification."""
import copy
import threading
import unittest
from unittest.mock import Mock, patch
from tests.test_wireless_setup import LuaDevice, LuaRuntime
from modules.capture_services.openwrt.wireless_setup_manager import WirelessSetupManager
from modules.capture_services.openwrt.wireless_setup_models import WirelessRequest
from modules.capture_services.openwrt.signal_manager import SignalError


class NoDataDevice(LuaDevice):
    force_no_data = False

    def kernel_mac(self,iface):
        section=iface.removeprefix("iface-")
        configured=self.config["wireless"].get(section,{}).get("macaddr")
        return configured or super().kernel_mac(iface)

    def call(self,obj,method,arguments):
        args=self.unwrap(arguments)
        if obj=="uci" and method=="apply":
            if self.force_no_data or self.sessions[args["ubus_rpc_session"]]==self.config:
                self.calls.append((obj,method))
                return None,5
        return super().call(obj,method,arguments)


@unittest.skipUnless(LuaRuntime,"Lua unavailable")
class UnchangedApplyTests(unittest.TestCase):
    def fixture(self):
        device=NoDataDevice()
        device.config["wireless"]["home"]["disabled"]="0"
        device.config["wireless"]["radio7"]["disabled"]="0"
        manager=WirelessSetupManager(device)
        request=WirelessRequest("ap","radio7","Home",section="home",options={"encryption":"psk2"})
        return device,manager,request,threading.Event()

    def test_no_change_save_verifies_running_config_and_closes_session(self):
        device,manager,request,event=self.fixture();before=copy.deepcopy(device.config)
        result=manager.save(request,event,Mock())
        self.assertEqual(result["section"],"home")
        self.assertEqual(device.config,before)
        self.assertFalse(device.sessions)
        self.assertIsNone(device.applied)
        self.assertIn(("uci","confirm"),device.calls)

    def test_changed_mac_is_applied_once_then_same_save_is_accepted_without_new_changes(self):
        device,manager,request,event=self.fixture()
        request=WirelessRequest("ap","radio7","Home",section="home",options={"encryption":"psk2","macaddr":"DE:AF:2A:EE:6B:38"})
        manager.save(request,event,Mock());manager.save(request,event,Mock())
        self.assertEqual(device.config["wireless"]["home"]["macaddr"],"DE:AF:2A:EE:6B:38")
        self.assertEqual(device.calls.count(("uci","apply")),2)
        self.assertFalse(device.sessions)

    def test_no_data_does_not_claim_unwritten_password_was_saved(self):
        device,manager,request,event=self.fixture();before=copy.deepcopy(device.config)
        device.force_no_data=True
        request=WirelessRequest("ap","radio7","Home",password="different-password",section="home",options={"encryption":"psk2"})
        with self.assertRaises(SignalError):manager.save(request,event,Mock())
        self.assertEqual(device.config,before)
        self.assertFalse(device.sessions)

    def test_no_data_does_not_skip_runtime_confirmation(self):
        device,manager,request,event=self.fixture()
        with patch.object(manager,"_wait_ready",side_effect=SignalError("authentication","AP not running")) as ready:
            with self.assertRaises(SignalError):manager.save(request,event,Mock())
        ready.assert_called_once()
        self.assertNotIn(("uci","confirm"),device.calls)
        self.assertFalse(device.sessions)
