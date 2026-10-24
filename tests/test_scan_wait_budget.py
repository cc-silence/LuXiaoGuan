"""Scan completion must fit native ubus time, even with a short collection period."""
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import unittest
from modules.capture_services.openwrt.wireless_setup_manager import WirelessSetupManager
from modules.capture_services.openwrt.wireless_setup_models import WirelessEnvironment
from modules.capture_services.openwrt.signal_manager import SignalError
from modules.capture_services.openwrt.wireless_scan_commands import SCAN_RPC_TIMEOUT_SECONDS


class ScanBudgetTests(unittest.TestCase):
    def test_scan_longer_than_old_twenty_second_limit_can_complete(self):
        manager = WirelessSetupManager(None)
        manager.inspect = Mock(return_value=WirelessEnvironment(data={"radios":[
            {"name":"radio0","phy":"phy0","interfaces":["wlan0"],"runtime_up":True}]}))
        now = [0.0]
        def scan(action, cancel, **values):
            self.assertEqual((action,values["radio"],values["interface"]),("scan","radio0","wlan0"))
            if values["timeout"] < 25:
                raise SignalError("timeout", "native scan cut off")
            self.assertGreater(values["timeout"], SCAN_RPC_TIMEOUT_SECONDS)
            now[0] += 25
            return {"results":[{"bssid":"02:00:00:00:00:01","ssid":"Live", "channel":44,"mhz":5220,"signal":-50,"encryption":{"enabled":False}}]}
        manager._call = Mock(side_effect=scan)
        cancel = NS(is_set=lambda:False,wait=lambda seconds:False)
        with patch("modules.capture_services.openwrt.wireless_setup_manager.time.monotonic", side_effect=lambda:now[0]):
            result = manager.scan(5,cancel,Mock(),radio="radio0")
        self.assertEqual(len(result),1)
        self.assertEqual(result[0].band,"5G")
        manager._call.assert_called_once()

    def test_shared_call_uses_native_scan_budget_only_for_scan(self):
        import threading
        manager = WirelessSetupManager(None)
        manager.signal._execute = Mock(return_value='{"ok":true,"result":{}}')
        cancel = threading.Event()
        manager._call("scan", cancel, radio="radio0", interface="wlan0")
        scan_timeout = manager.signal._execute.call_args.args[2]
        self.assertGreater(scan_timeout, SCAN_RPC_TIMEOUT_SECONDS)
        manager._call("revision", cancel)
        self.assertEqual(manager.signal._execute.call_args.args[2], 25)
