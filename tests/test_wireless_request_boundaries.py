"""Boundary checks before any router inspection or configuration write."""
import threading
import unittest
from unittest.mock import Mock
from modules.capture_services.openwrt.wireless_setup_models import WirelessRequest, validate_request
from modules.capture_services.openwrt.wireless_setup_manager import WirelessSetupManager


class WirelessRequestBoundaryTests(unittest.TestCase):
    def request(self, key, value):
        mode = "sta" if key == "metric" else "ap"
        return WirelessRequest(mode, "radio7", "Home", section="home",
                               options={"encryption": "psk2", key: value})

    def test_native_decimal_strings_and_boundaries_are_accepted(self):
        for key, low, high in (("dtim_period", 1, 255), ("beacon_int", 15, 65535),
                              ("maxassoc", 1, 512), ("metric", 0, 65535), ("txpower", 0, 40)):
            for value in (str(low), str(high)):
                with self.subTest(key=key, value=value):
                    self.assertIsNotNone(validate_request(self.request(key, value)))
        self.assertIsNotNone(validate_request(self.request("channel", "auto")))
        self.assertIsNotNone(validate_request(self.request("channel", "36")))

    def test_non_native_numerals_are_rejected_before_router_access(self):
        manager = WirelessSetupManager(Mock())
        manager.inspect = Mock(side_effect=AssertionError("Must reject before inspection"))
        cases = (("dtim_period", "\u0662"), ("beacon_int", "\uff11\uff10\uff10"),
                 ("maxassoc", "\u0661\u0660"), ("metric", "\uff13\uff17"),
                 ("txpower", "\u0662\u0660"), ("channel", "\uff13\uff16"))
        for key, value in cases:
            with self.subTest(key=key, value=value):
                with self.assertRaises(ValueError):
                    manager.save(self.request(key, value), threading.Event(), Mock())
        manager.inspect.assert_not_called()

    def test_non_string_numeric_values_are_rejected_before_router_access(self):
        manager = WirelessSetupManager(Mock())
        manager.inspect = Mock(side_effect=AssertionError("Must reject before inspection"))
        for key in ("dtim_period", "beacon_int", "maxassoc", "metric", "txpower", "channel"):
            for value in (20, 20.0, True, None, [], {}):
                with self.subTest(key=key, value=value):
                    with self.assertRaises(ValueError):
                        manager.save(self.request(key, value), threading.Event(), Mock())
        manager.inspect.assert_not_called()

    def test_out_of_range_values_are_rejected(self):
        for key, values in (("dtim_period", ("0", "256")), ("beacon_int", ("14", "65536")),
                            ("maxassoc", ("0", "513")), ("metric", ("-1", "65536")),
                            ("txpower", ("-1", "41")), ("channel", ("", "-1", "3.6"))):
            for value in values:
                with self.subTest(key=key, value=value):
                    with self.assertRaises(ValueError):
                        validate_request(self.request(key, value))
