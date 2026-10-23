"""Round-trip each advertised optional field through real Lua across official profiles."""
import copy
import threading
import unittest
from unittest.mock import Mock
from tests.test_wireless_setup import LuaRuntime
from tests.test_wireless_version_contracts import PROFILES, VersionDevice
from modules.capture_services.openwrt.wireless_options import AP_ADVANCED
from modules.capture_services.openwrt.wireless_setup_manager import WirelessSetupManager
from modules.capture_services.openwrt.wireless_setup_models import WirelessRequest
from modules.capture_services.openwrt.wireless_capabilities import option_supported
from modules.capture_services.openwrt.signal_manager import SignalError


@unittest.skipUnless(LuaRuntime, "Test-only Lua runtime unavailable")
class WirelessFieldRoundtripTests(unittest.TestCase):
    def test_each_advertised_field_reaches_only_its_native_scope(self):
        checked = 0
        for profile in PROFILES:
            probe = WirelessSetupManager(VersionDevice(profile)).inspect(threading.Event())
            radio = probe.data["radios"][0]
            for spec in AP_ADVANCED:
                if not option_supported(radio, spec.key):
                    continue
                with self.subTest(series=profile["series"], route=profile["route"], key=spec.key):
                    device = VersionDevice(profile)
                    original = copy.deepcopy(device.config)
                    value = {"bool": "1", "int": str(max(spec.low, 1)),
                        "hex4": "a1b2", "maclist": "02:11:22:33:44:55 02:11:22:33:44:66",
                        "enum": spec.choices[-1][1] if spec.choices else ""}[spec.kind]
                    WirelessSetupManager(device).save(WirelessRequest("ap", "radio7", "Home", section="home",
                        options={"encryption": "psk2", spec.key: value}), threading.Event(), Mock())
                    target = "radio7" if spec.scope == "radio" else "home"
                    other = "home" if spec.scope == "radio" else "radio7"
                    expected = value.split() if spec.kind == "maclist" else value
                    self.assertEqual(device.config["wireless"][target][spec.key], expected)
                    self.assertEqual(device.config["wireless"][other].get(spec.key), original["wireless"][other].get(spec.key))
                    self.assertEqual(device.config["network"], original["network"])
                    checked += 1
        self.assertGreater(checked, 100)

    def test_binary_feature_restrictions_cannot_be_bypassed_by_schema_presence(self):
        from dataclasses import replace
        for spec in AP_ADVANCED:
            if not spec.feature:
                continue
            device = VersionDevice(PROFILES[-1])
            manager = WirelessSetupManager(device)
            env = manager.inspect(threading.Event())
            env.data["features"][spec.feature] = False
            manager.inspect = Mock(return_value=env)
            value = "a1b2" if spec.kind == "hex4" else "1"
            with self.subTest(key=spec.key):
                with self.assertRaises(SignalError):
                    manager.save(WirelessRequest("ap", "radio7", "Home", section="home",
                        options={"encryption": "psk2", spec.key: value}), threading.Event(), Mock())
                self.assertNotIn(("session", "create"), device.calls)
