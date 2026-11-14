"""Prefer verified live station interfaces over implicit radio selection."""
import copy
import threading
import unittest

from tests.test_wireless_setup import LuaDevice, LuaRuntime
from modules.capture_services.openwrt.wireless_setup_manager import WirelessSetupManager


class Device(LuaDevice):
    def __init__(self):
        super().__init__()
        self.targets = []

    def call(self, obj, method, args):
        if obj == "iwinfo" and method == "scan":
            self.targets.append(self.unwrap(args)["device"])
        return super().call(obj, method, args)


@unittest.skipUnless(LuaRuntime, "Lua runtime unavailable")
class BridgeNativeTargetTests(unittest.TestCase):
    def test_active_bridge_uses_verified_station_without_changing_config(self):
        device = Device()
        device.config["wireless"]["upstream"] = {".type": "wifi-iface", "device": "radio7", "mode": "sta", "network": "wan"}
        before = copy.deepcopy(device.config)
        result = WirelessSetupManager(device)._call("scan", threading.Event(), radio="radio7", interface="iface-home")
        self.assertEqual(device.targets, ["iface-upstream"])
        self.assertFalse(result["interface_selection"]["radio_target"])
        self.assertEqual(device.config, before)
        self.assertNotIn(("uci", "apply"), device.calls)

    def test_inactive_bridge_keeps_existing_interface_path(self):
        device = Device()
        device.config["wireless"]["upstream"] = {".type": "wifi-iface", "device": "radio7", "mode": "sta", "disabled": "1"}
        result = WirelessSetupManager(device)._call("scan", threading.Event(), radio="radio7", interface="iface-home")
        self.assertEqual(device.targets, ["iface-home"])
        self.assertFalse(result["interface_selection"]["radio_target"])

    def test_custom_radio_name_keeps_verified_interface_during_bridge(self):
        class CustomDevice(Device):
            def call(self, obj, method, args):
                value, error = super().call(obj, method, args)
                if obj == "network.wireless" and method == "status" and error is None:
                    status = self.unwrap(value)
                    status["wifi_main"] = status.pop("radio7")
                    value = self.wrap(status)
                return value, error
        device = CustomDevice()
        device.config["wireless"]["wifi_main"] = copy.deepcopy(device.config["wireless"]["radio7"])
        device.config["wireless"]["home"]["device"] = "wifi_main"
        device.config["wireless"]["upstream"] = {".type": "wifi-iface", "device": "wifi_main", "mode": "sta"}
        result = WirelessSetupManager(device)._call("scan", threading.Event(), radio="wifi_main", interface="iface-home")
        self.assertEqual(device.targets, ["iface-upstream"])
        self.assertFalse(result["interface_selection"]["radio_target"])

    def test_foreign_radio_station_does_not_switch_target(self):
        device = Device()
        device.config["wireless"]["upstream"] = {".type": "wifi-iface", "device": "radio8", "mode": "sta"}
        WirelessSetupManager(device)._call("scan", threading.Event(), radio="radio7", interface="iface-home")
        self.assertEqual(device.targets, ["iface-home"])

    def test_forced_radio_remains_available_after_station_failure(self):
        device = Device()
        device.config["wireless"]["upstream"] = {".type": "wifi-iface", "device": "radio7", "mode": "sta"}
        result = WirelessSetupManager(device)._call("scan", threading.Event(),
            radio="radio7", interface="iface-home", force_radio=True)
        self.assertEqual(device.targets, ["radio7"])
        self.assertTrue(result["interface_selection"]["radio_target"])

    def test_second_request_revalidates_station_instead_of_reusing_name(self):
        device = Device()
        device.config["wireless"]["upstream"] = {".type": "wifi-iface", "device": "radio7", "mode": "sta"}
        manager = WirelessSetupManager(device)
        manager._call("scan", threading.Event(), radio="radio7", interface="iface-home")
        device.config["wireless"]["upstream"]["disabled"] = "1"
        manager._call("scan", threading.Event(), radio="radio7", interface="iface-home")
        self.assertEqual(device.targets, ["iface-upstream", "iface-home"])

    def test_station_disappearing_during_probe_does_not_start_scan(self):
        class ChangingDevice(Device):
            def call(self, obj, method, args):
                if obj == "iwinfo" and method == "info":
                    self.config["wireless"]["upstream"]["disabled"] = "1"
                return super().call(obj, method, args)
        device = ChangingDevice()
        device.config["wireless"]["upstream"] = {".type": "wifi-iface", "device": "radio7", "mode": "sta"}
        from modules.capture_services.openwrt.signal_manager import SignalError
        with self.assertRaises(SignalError):
            WirelessSetupManager(device)._call("scan", threading.Event(), radio="radio7", interface="iface-home")
        self.assertEqual(device.targets, [])


if __name__ == "__main__":
    unittest.main()
