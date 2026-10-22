"""Real Lua identity transactions, pending-change scans and stable Qt input."""
import copy
import json
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from tests.test_wireless_setup import LuaDevice, LuaRuntime, TARGET
from modules.capture_services.openwrt.signal_manager import SignalError
from modules.capture_services.openwrt.wireless_identity import generate_mac, parse_profiles, valid_mac
from modules.capture_services.openwrt.wireless_identity_profiles import PROFILES
from modules.capture_services.openwrt.wireless_setup_manager import WirelessSetupManager
from modules.capture_services.openwrt.wireless_setup_models import WirelessRequest, validate_request


class IdentityDevice(LuaDevice):
    def __init__(self):
        super().__init__()
        self.ignore_mac = False

    def call(self, obj, method, arguments):
        args = self.unwrap(arguments)
        if obj == "network.device":
            section = args.get("name", "").removeprefix("iface-")
            row = self.config["wireless"].get(section, {})
            mac = "02:00:00:00:00:10" if self.ignore_mac else row.get("macaddr", "02:00:00:00:00:10")
            return self.wrap(dict(up=True, macaddr=mac)), None
        return super().call(obj, method, arguments)


class IdentityModelTests(unittest.TestCase):
    def test_reference_profiles_and_generation(self):
        source = Path(__file__).resolve().parent / "fixtures" / "identity_profiles.txt"
        profiles, rejected = parse_profiles(source.read_text(encoding="utf-8-sig"))
        self.assertFalse(rejected)
        self.assertEqual(tuple((p.label, p.hostname, p.prefix) for p in profiles), PROFILES)
        self.assertEqual(len(PROFILES), 153)
        for label, host, prefix in PROFILES:
            address = generate_mac(prefix)
            self.assertTrue(valid_mac(address))
            self.assertTrue(address.startswith(prefix + ":"))
            self.assertNotIn(" ", host)
        for _ in range(100):
            address = generate_mac()
            self.assertEqual(int(address[:2], 16) & 3, 2)

    def test_invalid_identity_is_rejected(self):
        for options in (dict(identity_action="set", macaddr="FF:11:22:33:44:55"),
                        dict(identity_action="set", macaddr="00:00:00:00:00:00"),
                        dict(identity_action="keep", macaddr="02:11:22:33:44:55"),
                        dict(identity_action="set", macaddr="02:11:22:33:44:55", hostname=7),
                        dict(identity_action="set", macaddr="02:11:22:33:44:55", hostname="bad;name"),
                        dict(identity_action="set", macaddr="02:11:22:33:44:55", hostname="phone", bridge_mode="wds", network="lan")):
            with self.subTest(options=options), self.assertRaises(ValueError):
                validate_request(WirelessRequest("sta", "radio7", "Upstream", "test-password", options=options))


@unittest.skipUnless(LuaRuntime, "Lua runtime unavailable")
class IdentityTransactionTests(unittest.TestCase):
    def setUp(self):
        self.device = IdentityDevice()
        self.manager = WirelessSetupManager(self.device)
        self.cancel = threading.Event()
        self.message = lambda *_: None

    def create(self, **options):
        proof = self.manager.verify(TARGET, "test-password", self.cancel, self.message)
        request = WirelessRequest("sta", "radio7", TARGET.ssid, "test-password", verification=proof["token"],
            options=dict(encryption="psk2", bridge_mode="routed", proto="dhcp", zone="wan", **options))
        value = self.manager.save(request, self.cancel, self.message)
        return replace(request, section=value["section"], password="", verification=""), value

    def test_set_edit_restore_original_and_preserve_target(self):
        request, value = self.create(identity_action="set", macaddr="02:31:22:33:44:55", hostname="Phone")
        wifi = self.device.config["wireless"][value["section"]]
        self.assertEqual(wifi["macaddr"], "02:31:22:33:44:55")
        self.assertEqual(self.device.config["network"][value["network"]]["hostname"], "Phone")
        self.assertNotIn("macaddr", self.device.config["wireless"]["home"])
        self.assertNotIn("hostname", self.device.config["network"]["lan"])
        self.assertFalse(json.loads(wifi["rm_identity_backup"])["mac"])
        edited = replace(request, options=dict(request.options, macaddr="02:31:22:33:44:66", hostname="Phone2"))
        self.manager.save(edited, self.cancel, self.message)
        options = dict(encryption="psk2", bridge_mode="routed", proto="dhcp", zone="wan", identity_action="restore")
        self.manager.save(replace(request, options=options), self.cancel, self.message)
        wifi = self.device.config["wireless"][value["section"]]
        self.assertNotIn("macaddr", wifi)
        self.assertNotIn("rm_identity_backup", wifi)
        self.assertNotIn("hostname", self.device.config["network"][value["network"]])

    def test_restore_existing_explicit_values(self):
        request, value = self.create()
        self.device.config["wireless"][value["section"]]["macaddr"] = "02:31:00:00:00:01"
        self.device.config["network"][value["network"]]["hostname"] = "original"
        updated = replace(request, options=dict(request.options, identity_action="set", macaddr="02:31:00:00:00:02", hostname="changed"))
        self.manager.save(updated, self.cancel, self.message)
        restored = replace(request, options=dict(request.options, identity_action="restore"))
        self.manager.save(restored, self.cancel, self.message)
        self.assertEqual(self.device.config["wireless"][value["section"]]["macaddr"], "02:31:00:00:00:01")
        self.assertEqual(self.device.config["network"][value["network"]]["hostname"], "original")

    def test_duplicate_is_rejected_and_transaction_rolled_back(self):
        request, value = self.create()
        self.device.config["wireless"]["home"]["macaddr"] = "02:31:00:00:00:02"
        original = copy.deepcopy(self.device.config)
        updated = replace(request, options=dict(request.options, identity_action="set", macaddr="02:31:00:00:00:02"))
        with self.assertRaises(SignalError) as error:
            self.manager.save(updated, self.cancel, self.message)
        self.assertEqual(error.exception.code, "identity_duplicate")
        self.assertEqual(self.device.config, original)
        self.assertFalse(self.device.sessions)

    def test_runtime_mac_must_match_before_confirmation(self):
        request, value = self.create(identity_action="set", macaddr="02:31:22:33:44:55")
        self.device.ignore_mac = True
        status = self.manager._call("status", self.cancel, radio="radio7", section=value["section"], mode="sta",
                                    bridge_mode="routed", expected_mac=request.options["macaddr"])
        self.assertFalse(status["ready"])
        self.assertTrue(status["identity_pending"])

    def test_external_edit_is_not_overwritten(self):
        request, value = self.create(identity_action="set", macaddr="02:31:22:33:44:55", hostname="Phone")
        self.device.config["network"][value["network"]]["hostname"] = "external"
        original = copy.deepcopy(self.device.config)
        with self.assertRaises(SignalError) as error:
            self.manager.save(replace(request, options=dict(encryption="psk2", identity_action="restore")), self.cancel, self.message)
        self.assertEqual(error.exception.code, "identity_changed")
        self.assertEqual(self.device.config, original)

    def test_cancelled_apply_restores_all_configs(self):
        request, value = self.create()
        original = copy.deepcopy(self.device.config)
        with patch.object(self.manager, "_wait_ready", side_effect=InterruptedError("cancel")):
            with self.assertRaises(InterruptedError):
                self.manager.save(replace(request, options=dict(request.options, identity_action="set", macaddr="02:31:22:33:44:55")), self.cancel, self.message)
        self.assertEqual(self.device.config, original)
        self.assertFalse(self.device.sessions)

    def test_pending_config_allows_read_only_scan_inspection_only(self):
        self.device.lua.execute('require("uci").cursor().changes=function() return {wireless={dirty=true}} end')
        with self.assertRaises(SignalError) as error:
            self.manager.inspect(self.cancel)
        self.assertEqual(error.exception.code, "pending")
        env = self.manager.inspect(self.cancel, readonly=True)
        self.assertTrue(env.ready)
        self.assertNotIn(("uci", "apply"), self.device.calls)
        self.device.lua.execute('require("uci").cursor().changes=function() return {} end')
        self.assertTrue(self.manager.inspect(self.cancel).ready)
