"""Real Lua relocation transactions against an isolated OpenWrt substitute."""
import copy
import json
import threading
import unittest
from unittest.mock import Mock, patch
from tests.test_wireless_setup import LuaDevice, LuaRuntime
from modules.capture_services.openwrt.wireless_setup_manager import WirelessSetupManager
from modules.capture_services.openwrt.relocation_manager import RelocationManager
from modules.capture_services.openwrt.signal_manager import SignalError


class MovingDevice(LuaDevice):
    def __init__(self):
        super().__init__()
        self.config["system"] = {"main": {".type": "system", "hostname": "OldHome", "timezone": "UTC"}}
        self.config["network"]["lan"]["device"] = "br-lan"
        self.config["network"]["wan"].update(device="eth1", hostname="old-person", clientid="old-client")
        self.config["network"]["lan_dev"] = {".type": "device", "name": "br-lan", "type": "bridge", "ports": ["lan1", "lan2"]}
        self.original = copy.deepcopy(self.config)
        self.break_status = False

    def read(self, command):
        if command.startswith("test ! -e /sys/class/net/"):
            return "1\n"
        if command.startswith("cat /proc/sys/kernel/hostname"):
            return self.config["system"]["main"]["hostname"] + "\n"
        return super().read(command)

    def call(self, obj, method, arguments):
        args = self.unwrap(arguments)
        if obj == "network.device" and method == "status":
            if self.break_status:
                return None, 4
            device = args.get("name")
            if isinstance(device, str) and device.startswith('iface-'):
                row = self.config['wireless'].get(device[6:], {})
                return self.wrap(dict(up=True, macaddr=row.get('macaddr', '00:11:22:33:44:66'))), None
            if device is None:
                return self.wrap({"br-lan": {"macaddr": "00:11:22:33:44:55"}, "eth1": {"macaddr": "00:11:22:33:44:56"}}), None
            for row in self.config["network"].values():
                if row.get("name") == device and row.get("macaddr"):
                    return self.wrap({"up": True, "macaddr": row["macaddr"]}), None
            if device in ("br-lan", "eth1"):
                return self.wrap({"up": True, "macaddr": "00:11:22:33:44:" + ("55" if device == "br-lan" else "56")}), None
        return super().call(obj, method, arguments)


@unittest.skipIf(LuaRuntime is None, "Lua runtime unavailable")
class RelocationTests(unittest.TestCase):
    def setUp(self):
        self.device = MovingDevice()
        self.manager = RelocationManager(WirelessSetupManager(self.device))
        self.cancel = threading.Event()

    def test_preview_does_not_mutate_and_randomizes_identity(self):
        first = self.manager.preview(self.cancel)
        second = self.manager.preview(self.cancel)
        self.assertEqual(self.device.config, self.device.original)
        self.assertEqual(first["token"], second["token"])
        self.assertNotEqual(first["plan"], second["plan"])
        self.assertRegex(first["plan"]["hostname"], r"^[A-Za-z0-9][A-Za-z0-9_-]{0,62}$")
        self.assertNotIn("openwrt", first["plan"]["hostname"].lower())
        self.assertEqual(len(first["targets"]), 2)
        self.assertEqual(len(set(first["plan"]["macs"])), 2)
        for mac in first["plan"]["macs"]:
            self.assertEqual(int(mac[:2], 16) & 3, 2)
        self.assertNotIn("original-password", json.dumps(first))
        self.assertFalse(self.device.sessions)

    def test_brand_and_model_names_apply_unchanged_to_system_and_wan(self):
        for hostname in ('CMCC_G2T7', 'TP-LINK_A8K2', 'HUAWEI-Mate-60-Pro', 'vivo-X100', 'OpenWrt-1234abcd'):
            with self.subTest(hostname=hostname):
                self.setUp()
                review = self.manager.preview(self.cancel)
                review['plan']['hostname'] = hostname
                result = self.manager.apply(review, self.cancel, Mock())
                self.assertEqual(result['hostname'], hostname)
                self.assertEqual(self.device.config['system']['main']['hostname'], hostname)
                self.assertEqual(self.device.config['network']['wan']['hostname'], hostname)

    def test_invalid_hostname_is_rejected_before_config_writes(self):
        for hostname in ('', 'a'*64, '-CMCC', 'CMCC_', 'CMCC G2T7', 'a;reboot', 'a\n', 12):
            with self.subTest(hostname=hostname):
                review = self.manager.preview(self.cancel)
                review['plan']['hostname'] = hostname
                with self.assertRaises(SignalError) as error:
                    self.manager.apply(review, self.cancel, Mock())
                self.assertEqual(error.exception.code, 'identity')
                self.assertEqual(self.device.config, self.device.original)
                self.assertFalse(self.device.sessions)
                self.assertNotIn(('uci', 'apply'), self.device.calls)

    def test_apply_preserves_management_system_and_radio_hardware(self):
        review = self.manager.preview(self.cancel)
        result = self.manager.apply(review, self.cancel, Mock())
        self.assertEqual(result["removed"], 0)
        hotspot = self.device.config['wireless']['home']
        expected = review['plan']['access_points'][0]
        self.assertEqual(hotspot['ssid'], expected['name'])
        self.assertEqual(hotspot['macaddr'], expected['mac'])
        self.assertEqual(hotspot['key'], 'original-password')
        self.assertNotIn('disabled', hotspot)
        self.assertEqual(self.device.config["wireless"]["radio7"], self.device.original["wireless"]["radio7"])
        self.assertEqual(self.device.config["network"]["lan"]["ipaddr"], "192.168.8.1")
        self.assertEqual(self.device.config["network"]["lan_dev"]["ports"], ["lan1", "lan2"])
        self.assertEqual(self.device.config["system"]["main"]["timezone"], "UTC")
        self.assertEqual(self.device.config["system"]["main"]["hostname"], result["hostname"])
        self.assertEqual(self.device.config["network"]["wan"]["hostname"], result["hostname"])
        self.assertNotIn("clientid", self.device.config["network"]["wan"])
        self.assertFalse(self.device.sessions)
        self.assertIsNone(self.device.backup)

    def test_stale_review_never_applies_and_preserves_external_edit(self):
        review = self.manager.preview(self.cancel)
        self.device.config["system"]["main"]["hostname"] = "External"
        with self.assertRaises(SignalError):
            self.manager.apply(review, self.cancel, Mock())
        self.assertEqual(self.device.config["system"]["main"]["hostname"], "External")
        self.assertIn("home", self.device.config["wireless"])
        self.assertNotIn(("uci", "apply"), self.device.calls)
        self.assertFalse(self.device.sessions)

    def test_status_failure_restores_exact_five_configs(self):
        review = self.manager.preview(self.cancel)
        original = self.manager._call
        def fail_status(action, *args, **kwargs):
            if action == "move_status":
                raise SignalError("transport", "simulated connection loss")
            return original(action, *args, **kwargs)
        self.manager._call = fail_status
        with self.assertRaises(SignalError):
            self.manager.apply(review, self.cancel, Mock())
        self.assertEqual(self.device.config, self.device.original)
        self.assertFalse(self.device.sessions)
        self.assertIsNone(self.device.backup)

    def test_duplicate_mac_refused_before_apply(self):
        review = self.manager.preview(self.cancel)
        review["plan"]["macs"][1] = review["plan"]["macs"][0]
        with self.assertRaises(SignalError):
            self.manager.apply(review, self.cancel, Mock())
        self.assertEqual(self.device.config, self.device.original)
        self.assertNotIn(("uci", "apply"), self.device.calls)

    def test_cancel_before_start_never_creates_session(self):
        review = self.manager.preview(self.cancel)
        self.cancel.set()
        with self.assertRaises(InterruptedError):
            self.manager.apply(review, self.cancel, Mock())
        self.assertFalse(self.device.sessions)

    def test_owned_wireless_network_and_firewall_membership_removed(self):
        self.device.config["wireless"]["uplink"] = {".type": "wifi-iface", "mode": "sta", "network": "wwan", "ssid": "OldUplink", "device": "radio7", "key": "old-password"}
        self.device.config["network"]["wwan"] = {".type": "interface", "proto": "dhcp", "rm_owner": "uplink"}
        self.device.config["firewall"]["zone_wan"]["network"] = ["wan", "wwan"]
        self.manager.apply(self.manager.preview(self.cancel), self.cancel, Mock())
        self.assertNotIn("wwan", self.device.config["network"])
        self.assertEqual(self.device.config["firewall"]["zone_wan"]["network"], ["wan"])

    def test_old_network_and_shared_relay_are_removed_together(self):
        self.device.config["wireless"]["oldsta"] = {".type":"wifi-iface", "mode":"sta", "ssid":"Old", "device":"radio7", "network":"old"}
        self.device.config["network"]["old"] = {".type": "interface", "proto": "dhcp", "rm_owner": "oldsta"}
        self.device.config["network"]["shared"] = {".type": "interface", "proto": "relay", "network": ["lan", "old"]}
        original = copy.deepcopy(self.device.config)
        review = self.manager.preview(self.cancel)
        self.assertEqual(self.device.config, original)
        self.assertEqual(review["removed"], ["old", "shared"])
        self.manager.apply(review, self.cancel, Mock())
        self.assertNotIn("old", self.device.config["network"])
        self.assertNotIn("shared", self.device.config["network"])
        self.assertEqual(self.device.config["network"]["lan"]["ipaddr"],original["network"]["lan"]["ipaddr"])

    def test_existing_interface_mac_override_follows_new_device_identity(self):
        self.device.config["network"]["lan"]["macaddr"] = "00:11:22:33:44:55"
        review = self.manager.preview(self.cancel)
        self.manager.apply(review, self.cancel, Mock())
        expected = review["plan"]["macs"][0]
        self.assertEqual(self.device.config["network"]["lan"]["macaddr"], expected)
        self.assertEqual(self.device.config["network"]["lan_dev"]["macaddr"], expected)

    def test_legacy_interface_keeps_address_and_updates_its_own_mac_option(self):
        self.device.config["network"].pop("lan_dev")
        self.device.config["network"]["lan"].pop("device")
        self.device.config["network"]["lan"].update(ifname="eth0", type="bridge")
        original = self.device.call
        def legacy_call(obj, method, args):
            if obj == "network.interface.lan":
                return self.device.wrap({"device": "br-lan", "up": True}), None
            if obj == "network.device" and self.device.unwrap(args).get("name") == "br-lan":
                mac = self.device.config["network"]["lan"].get("macaddr", "00:11:22:33:44:55")
                return self.device.wrap({"up": True, "macaddr": mac}), None
            return original(obj, method, args)
        self.device.call = legacy_call
        self.device.lua.globals().bridge_call = legacy_call
        review = self.manager.preview(self.cancel)
        self.assertTrue(review["targets"][0]["legacy"])
        self.manager.apply(review, self.cancel, Mock())
        self.assertEqual(self.device.config["network"]["lan"]["ipaddr"], "192.168.8.1")
        self.assertEqual(self.device.config["network"]["lan"]["macaddr"], review["plan"]["macs"][0])

    def test_relay_cleanup_restores_dhcp_and_removes_both_owned_interfaces(self):
        original = {"ignore": "0", "ra": "server", "dhcpv6": "server"}
        backup = json.dumps([dict(section="lan", interface="lan", values=original)])
        self.device.config["dhcp"]["lan"] = dict(original, **{".type": "dhcp", "interface": "lan", "ignore": "1", "ra": "disabled", "dhcpv6": "disabled", "ndp": "disabled"})
        self.device.config["wireless"]["uplink"] = {".type": "wifi-iface", "mode": "sta", "device": "radio7", "ssid": "OldBridge", "key": "password", "network": "wwan", "rm_bridge": "relay"}
        self.device.config["network"]["wwan"] = {".type": "interface", "proto": "dhcp", "rm_owner": "uplink"}
        self.device.config["network"]["relay"] = {".type": "interface", "proto": "relay", "network": ["lan", "wwan"], "rm_owner": "uplink", "rm_dhcp_backup": backup}
        self.manager.apply(self.manager.preview(self.cancel), self.cancel, Mock())
        self.assertNotIn("relay", self.device.config["network"])
        self.assertNotIn("wwan", self.device.config["network"])
        self.assertEqual(self.device.config["dhcp"]["lan"], dict(original, **{".type": "dhcp", "interface": "lan"}))

    def test_wrong_owner_metadata_cannot_remove_static_management_network(self):
        self.device.config["network"]["lan"]["rm_owner"] = "home"
        review = self.manager.preview(self.cancel)
        self.assertNotIn("lan",review["removed"])
        self.assertNotIn(("uci", "apply"), self.device.calls)

    def test_global_pending_system_changes_block_preview(self):
        self.device.lua.execute('require("uci").cursor().changes=function(self,config) if config=="system" then return {hostname="pending"} end return {} end')
        with self.assertRaises(SignalError) as error:
            self.manager.preview(self.cancel)
        self.assertEqual(error.exception.code, "pending")
        self.assertNotIn(("uci", "apply"), self.device.calls)

    def test_manual_wireless_only_dhcp_uplink_is_removed(self):
        self.device.config["wireless"]["uplink"] = {".type": "wifi-iface", "mode": "sta", "device": "radio7", "network": "wwan"}
        self.device.config["network"]["wwan"] = {".type": "interface", "proto": "dhcp"}
        self.manager.apply(self.manager.preview(self.cancel), self.cancel, Mock())
        self.assertNotIn("wwan", self.device.config["network"])

    def test_cancel_after_apply_restores_original_configs(self):
        review = self.manager.preview(self.cancel)
        original = self.manager._call
        def cancel_after_stage(action, *args, **kwargs):
            result = original(action, *args, **kwargs)
            if action == "move_stage":
                self.cancel.set()
            return result
        self.manager._call = cancel_after_stage
        with self.assertRaises(InterruptedError):
            self.manager.apply(review, self.cancel, Mock())
        self.assertEqual(self.device.config, self.device.original)
        self.assertFalse(self.device.sessions)

    def test_firmware_without_rollback_is_refused_before_session(self):
        original = self.device.run
        def no_rollback(name, command, timeout, cancel_event=None):
            result = original(name, command, timeout, cancel_event)
            if name == "wireless_setup_probe":
                from dataclasses import replace
                result = replace(result, stdout=result.stdout.replace("rollback=1", "rollback=0"))
            return result
        self.device.run = no_rollback
        with self.assertRaises(SignalError) as error:
            self.manager.preview(self.cancel)
        self.assertEqual(error.exception.code, "dependencies")
        self.assertFalse(self.device.sessions)
        self.assertEqual(self.device.config, self.device.original)


class RelocationNameTests(unittest.TestCase):
    def test_router_name_has_brand_and_four_random_letters_or_digits(self):
        from modules.capture_services.openwrt.relocation_identity import generate_relocation_hostname
        with patch('modules.capture_services.openwrt.relocation_identity.secrets.randbelow', return_value=0), \
             patch('modules.capture_services.openwrt.relocation_identity.secrets.choice', side_effect=['CMCC', 'G', '2', 'T', '7']):
            self.assertEqual(generate_relocation_hostname(), 'CMCC_G2T7')

    def test_phone_names_reuse_model_library_and_exclude_old_name(self):
        from modules.capture_services.openwrt.relocation_identity import generate_relocation_hostname
        with patch('modules.capture_services.openwrt.relocation_identity.secrets.randbelow', return_value=1), \
             patch('modules.capture_services.openwrt.relocation_identity.secrets.choice', side_effect=lambda names: names[0]):
            first = generate_relocation_hostname()
            second = generate_relocation_hostname(first.upper())
        self.assertNotEqual(first.casefold(), second.casefold())
        self.assertRegex(first, r'^[A-Za-z0-9][A-Za-z0-9-]{0,62}$')
        self.assertNotIn('OpenWrt', first)

    def test_router_name_collision_changes_without_retry_loop(self):
        from modules.capture_services.openwrt.relocation_identity import generate_relocation_hostname
        with patch('modules.capture_services.openwrt.relocation_identity.secrets.randbelow', return_value=0), \
             patch('modules.capture_services.openwrt.relocation_identity.secrets.choice', side_effect=['CMCC', 'G', '2', 'T', '7', '8']):
            self.assertEqual(generate_relocation_hostname('cmcc_g2t7'), 'CMCC_G2T8')
