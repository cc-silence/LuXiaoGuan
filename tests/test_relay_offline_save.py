"""Offline regression for saving a routed station while the upstream is unavailable."""
import threading
import unittest
from unittest.mock import Mock, patch

from tests.test_wireless_setup import LuaRuntime, TARGET
from tests.test_wireless_bridges import RuntimeDevice
from modules.capture_services.openwrt.signal_manager import SignalError
from modules.capture_services.openwrt.wireless_setup_manager import WirelessSetupManager
from modules.capture_services.openwrt.wireless_setup_models import WirelessRequest


@unittest.skipUnless(LuaRuntime, "Test-only Lua runtime unavailable")
class OfflineRelaySaveTests(unittest.TestCase):
    def setUp(self):
        self.device = RuntimeDevice()
        self.manager = WirelessSetupManager(self.device)
        self.cancel = threading.Event()
        self.request = WirelessRequest("sta", "radio7", TARGET.ssid, "test-password",
            options={"encryption": "psk2", "bssid": TARGET.bssid, "proto": "dhcp", "zone": "wan"})

    def test_disconnected_station_is_saved_and_enabled(self):
        self.device.complete = False
        result = self.manager.save(self.request, self.cancel, Mock())
        row = self.device.config["wireless"][result["section"]]
        self.assertEqual(row["ssid"], TARGET.ssid)
        self.assertEqual(row["disabled"], "0")
        self.assertEqual(row["key"], "test-password")
        self.assertIn(("uci", "confirm"), self.device.calls)
        self.assertNotIn(("uci", "rollback"), self.device.calls)

    def test_saved_configuration_failure_still_rolls_back(self):
        self.device.complete = False
        original = self.manager._call
        def call(action, *args, **kwargs):
            if action == "saved_status":
                raise SignalError("unconfirmed", "saved configuration mismatch")
            return original(action, *args, **kwargs)
        with patch.object(self.manager, "_call", side_effect=call):
            with self.assertRaises(SignalError):
                self.manager.save(self.request, self.cancel, Mock())
        self.assertIn(("uci", "rollback"), self.device.calls)
        self.assertNotIn(("uci", "confirm"), self.device.calls)


if __name__ == "__main__":
    unittest.main()
