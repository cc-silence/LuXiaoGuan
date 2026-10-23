"""Pinned official option contracts, real Lua transactions, and UI capability gates."""
import copy
import json
from pathlib import Path
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from tests.test_wireless_setup import LuaDevice, LuaRuntime, TARGET
from tests.test_wireless_bridges import RuntimeDevice
from tests.test_save_only import PersistenceDevice
from tests.test_wireless_ui import APP, environment
from modules.capture_services.openwrt.wireless_setup_manager import WirelessSetupManager
from modules.capture_services.openwrt.wireless_setup_models import WirelessRequest
from modules.capture_services.openwrt.wireless_capabilities import option_supported
from modules.capture_services.openwrt.signal_manager import SignalError
from modules.main_pages.packet_capture.openwrt.signal_setup_dialogs import AccessPointSetupDialog
from modules.main_pages.packet_capture.openwrt.signal_setup_editor import configure_ap, ap_request

PROFILES = json.loads((Path(__file__).parent / "fixtures/official_wireless_setup_contracts.json").read_text(encoding="utf-8"))


class VersionMixin:
    def __init__(self, profile):
        self.profile = copy.deepcopy(profile)
        self.status_error = None
        self.status_value = "ENABLED"
        self.legacy_freq = 5180
        self.wireless_mode = "Master"
        self.runtime_ssid = None
        self.native_link_up = True
        super().__init__()

    def call(self, obj, method, arguments):
        if obj == "network.wireless" and method == "get_validate":
            self.calls.append((obj, method))
            return self.wrap(self.profile["get_validate"]), None
        if obj.startswith("hostapd."):
            self.calls.append((obj, method))
            if method == "get_status":
                if self.status_error is not None:
                    return None, self.status_error
                if self.profile["ap_status"] == "legacy":
                    return None, 3
                section = obj.removeprefix("hostapd.iface-")
                ssid = self.runtime_ssid if self.runtime_ssid is not None else self.config["wireless"].get(section, {}).get("ssid", "")
                return self.wrap({"status": self.status_value, "ssid": ssid}), None
            if method == "get_clients":
                return self.wrap({"freq": self.legacy_freq, "clients": {}}), None
        value, error = super().call(obj, method, arguments)
        if obj == "iwinfo" and method == "info" and not error:
            data = self.unwrap(value)
            section = self.unwrap(arguments).get("device", "").removeprefix("iface-")
            ssid = self.runtime_ssid if self.runtime_ssid is not None else self.config["wireless"].get(section, {}).get("ssid", "")
            data.update(mode=self.wireless_mode, frequency=5180, ssid=ssid)
            return self.wrap(data), None
        if obj == "network.device" and not error:
            data = self.unwrap(value)
            data["up"] = self.native_link_up
            return self.wrap(data), None
        return value, error


class VersionDevice(VersionMixin, LuaDevice):
    pass


class VersionBridgeDevice(VersionMixin, RuntimeDevice):
    pass


class VersionPersistenceDevice(VersionMixin, PersistenceDevice):
    pass


@unittest.skipUnless(LuaRuntime, "Test-only Lua runtime unavailable")
class OfficialWirelessContractTests(unittest.TestCase):
    def manager(self, profile, *, bridge=False):
        device = (VersionBridgeDevice if bridge else VersionDevice)(profile)
        return device, WirelessSetupManager(device), threading.Event()


    def test_relay_dns_readback_rejects_missing_or_changed_saved_values(self):
        from dataclasses import asdict
        for profile in PROFILES:
            for dns in ("1.1.1.1 2001:4860:4860::8888", ""):
                device, manager, cancel = self.manager(profile)
                request = WirelessRequest("sta", "radio7", TARGET.ssid, "test-password",
                    options={"encryption": "psk2", "proto": "dhcp", "zone": "wan", "dns": dns})
                result = manager.save(request, cancel, Mock())
                network = device.config["network"][result["network"]]
                good = copy.deepcopy(network)
                for key, bad in (("dns", ["8.8.8.8"]), ("peerdns", "1" if dns else "0")):
                    with self.subTest(series=profile["series"], route=profile["route"], dns=dns, key=key):
                        network.clear()
                        network.update(copy.deepcopy(good))
                        network[key] = bad
                        with self.assertRaises(SignalError) as error:
                            manager._call("saved_status", cancel, values=asdict(request),
                                section=result["section"], network=result["network"])
                        self.assertEqual(error.exception.code, "unconfirmed")

    def test_relay_dns_confirmation_failure_rolls_back_before_confirm(self):
        for profile in PROFILES:
            device, manager, cancel = self.manager(profile)
            original = copy.deepcopy(device.config)
            call = manager._call
            def corrupt(action, event, **values):
                if action == "saved_status":
                    device.config["network"][values["network"]]["peerdns"] = "1"
                return call(action, event, **values)
            manager._call = corrupt
            with self.subTest(series=profile["series"], route=profile["route"]):
                with self.assertRaises(SignalError):
                    manager.save(WirelessRequest("sta", "radio7", TARGET.ssid, "test-password",
                        options={"encryption": "psk2", "proto": "dhcp", "zone": "wan", "dns": "1.1.1.1"}),
                        cancel, Mock())
                self.assertEqual(device.config, original)
                self.assertIn(("uci", "rollback"), device.calls)
                self.assertNotIn(("uci", "confirm"), device.calls)


    def test_relay_dns_readback_accepts_native_list_and_legacy_string(self):
        from dataclasses import asdict
        for profile in PROFILES:
            device, manager, cancel = self.manager(profile)
            request = WirelessRequest("sta", "radio7", TARGET.ssid, "test-password",
                options={"encryption": "psk2", "proto": "dhcp", "zone": "wan",
                         "dns": "1.1.1.1 2001:4860:4860::8888"})
            result = manager.save(request, cancel, Mock())
            network = device.config["network"][result["network"]]
            for value in (["1.1.1.1", "2001:4860:4860::8888"], "1.1.1.1  2001:4860:4860::8888"):
                with self.subTest(series=profile["series"], route=profile["route"], value=value):
                    network["dns"] = value
                    self.assertTrue(manager._call("saved_status", cancel, values=asdict(request),
                        section=result["section"], network=result["network"])["saved"])

    def test_omitted_dns_preserves_existing_configuration_in_both_save_paths(self):
        for profile in PROFILES:
            for apply in (False, True):
                with self.subTest(series=profile["series"], route=profile["route"], apply=apply):
                    device = VersionDevice(profile) if apply else VersionPersistenceDevice(profile)
                    manager = WirelessSetupManager(device)
                    cancel = threading.Event()
                    request = WirelessRequest("sta", "radio7", TARGET.ssid, "test-password",
                        options={"encryption": "psk2", "proto": "dhcp", "zone": "wan", "dns": "1.1.1.1"})
                    result = manager.save(request, cancel, Mock(), apply=apply)
                    before = copy.deepcopy(device.config["network"][result["network"]])
                    manager.save(WirelessRequest("sta", "radio7", TARGET.ssid, section=result["section"],
                        options={"encryption": "psk2", "proto": "dhcp", "zone": "wan"}),
                        cancel, Mock(), apply=apply)
                    after = device.config["network"][result["network"]]
                    self.assertEqual(after.get("dns"), before.get("dns"))
                    self.assertEqual(after.get("peerdns"), before.get("peerdns"))


    def test_relay_saved_zone_must_match_requested_membership(self):
        from dataclasses import asdict
        for profile in PROFILES:
            device, manager, cancel = self.manager(profile)
            request = WirelessRequest("sta", "radio7", TARGET.ssid, "test-password",
                options={"encryption": "psk2", "proto": "dhcp", "zone": "wan"})
            result = manager.save(request, cancel, Mock())
            network = result["network"]
            good = copy.deepcopy(device.config["firewall"])
            cases = ("missing", "wrong", "duplicate")
            for kind in cases:
                device.config["firewall"] = copy.deepcopy(good)
                zones = device.config["firewall"]
                if kind != "duplicate":
                    zones["zone_wan"]["network"].remove(network)
                if kind != "missing":
                    zones["zone_lan"]["network"].append(network)
                with self.subTest(series=profile["series"], route=profile["route"], kind=kind):
                    with self.assertRaises(SignalError) as error:
                        manager._call("saved_status", cancel, values=asdict(request),
                            section=result["section"], network=network)
                    self.assertEqual(error.exception.code, "unconfirmed")

    def test_relay_zone_readback_accepts_native_and_legacy_membership(self):
        from dataclasses import asdict
        for profile in PROFILES:
            device, manager, cancel = self.manager(profile)
            request = WirelessRequest("sta", "radio7", TARGET.ssid, "test-password",
                options={"encryption": "psk2", "proto": "dhcp", "zone": "wan"})
            result = manager.save(request, cancel, Mock())
            for kind in ("list", "string"):
                with self.subTest(series=profile["series"], route=profile["route"], kind=kind):
                    if kind == "string":
                        for zone in device.config["firewall"].values():
                            zone["network"] = " ".join(zone["network"])
                    self.assertTrue(manager._call("saved_status", cancel, values=asdict(request),
                        section=result["section"], network=result["network"])["saved"])

    def test_supported_series_and_two_newest_routes_are_recorded(self):
        self.assertEqual({p["series"] for p in PROFILES}, {"19.07", "21.02", "22.03", "23.05", "24.10", "25.12"})
        self.assertEqual({p["route"] for p in PROFILES if p["series"] == "25.12"}, {"shell", "ucode"})
        for profile in PROFILES:
            self.assertEqual(len(profile["commit"]), 40)
            self.assertTrue(profile["source"])
            for source in profile["source"]:
                self.assertEqual(len(source["sha256"]), 64)
                self.assertTrue(source["url"].startswith("https://raw.githubusercontent.com/openwrt/"))

    def test_native_inspection_returns_only_the_scoped_option_whitelist(self):
        for profile in PROFILES:
            with self.subTest(series=profile["series"], route=profile["route"]):
                device, manager, cancel = self.manager(profile)
                radio = manager.inspect(cancel).data["radios"][0]
                self.assertTrue(option_supported(radio, "beacon_int"))
                self.assertEqual(option_supported(radio, "skip_inactivity_poll"), profile["series"] != "19.07")
                self.assertNotIn("key", radio["option_support"]["interface"])
                self.assertNotIn("beacon_int", radio["option_support"]["interface"])
                self.assertNotIn(("uci", "apply"), device.calls)

    def test_each_series_edits_native_ap_and_writes_beacon_to_radio_only(self):
        for profile in PROFILES:
            with self.subTest(series=profile["series"], route=profile["route"]):
                device, manager, cancel = self.manager(profile)
                request = WirelessRequest("ap", "radio7", "Renamed", section="home", options={
                    "encryption": "psk2", "beacon_int": "120", "dtim_period": "3",
                    "max_inactivity": "86400", "disassoc_low_ack": "0"})
                manager.save(request, cancel, Mock())
                self.assertEqual(device.config["wireless"]["radio7"]["beacon_int"], "120")
                self.assertNotIn("beacon_int", device.config["wireless"]["home"])
                self.assertEqual(device.config["wireless"]["home"]["dtim_period"], "3")
                self.assertEqual(device.config["wireless"]["home"]["key"], "original-password")

    def test_each_series_creates_and_saves_without_applying(self):
        for profile in PROFILES:
            with self.subTest(series=profile["series"], route=profile["route"]):
                device, manager, cancel = self.manager(profile)
                device = VersionPersistenceDevice(profile)
                manager = WirelessSetupManager(device)
                request = WirelessRequest("ap", "radio7", "New", options={"encryption": "none", "beacon_int": "150"})
                result = manager.save(request, cancel, Mock(), apply=False)
                self.assertEqual(device.config["wireless"][result["section"]]["disabled"], "1")
                self.assertEqual(device.config["wireless"]["radio7"]["beacon_int"], "150")
                self.assertNotIn(("uci", "apply"), device.calls)

    def test_old_unsupported_option_is_rejected_before_any_write_or_session(self):
        device, manager, cancel = self.manager(PROFILES[0])
        original = copy.deepcopy(device.config)
        request = WirelessRequest("ap", "radio7", "Home", section="home",
            options={"encryption": "psk2", "skip_inactivity_poll": "1"})
        for apply in (False, True):
            with self.assertRaises(SignalError) as error:
                manager.save(request, cancel, Mock(), apply=apply)
            self.assertEqual(error.exception.code, "capability")
        self.assertEqual(device.config, original)
        self.assertNotIn(("session", "create"), device.calls)

    def test_unknown_schema_never_enables_or_writes_guessed_options(self):
        device, manager, cancel = self.manager(PROFILES[-1])
        device.profile["get_validate"] = {}
        request = WirelessRequest("ap", "radio7", "Home", section="home", options={"encryption": "psk2"})
        with self.assertRaises(SignalError):
            manager.save(request, cancel, Mock())
        self.assertNotIn(("session", "create"), device.calls)

    def test_modern_disabled_or_permission_error_does_not_use_legacy_fallback(self):
        device, _, _ = self.manager(PROFILES[1])
        payload = dict(action="status", radio="radio7", section="home", mode="ap")
        for error in (None, 2, 4, 6):
            device.status_error = error
            device.status_value = "DISABLED"
            self.assertFalse(json.loads(device.execute(payload))["result"]["ready"])
        self.assertNotIn(("hostapd.iface-home", "get_clients"), device.calls)

    def test_old_ap_requires_matching_frequency_and_real_master_mode(self):
        device, _, _ = self.manager(PROFILES[0])
        payload = dict(action="status", radio="radio7", section="home", mode="ap")
        self.assertTrue(json.loads(device.execute(payload))["result"]["ready"])
        for frequency, mode in ((0, "Master"), (2412, "Master"), (5180, "Client")):
            device.legacy_freq, device.wireless_mode = frequency, mode
            self.assertFalse(json.loads(device.execute(payload))["result"]["ready"])

    def test_wds_and_ipv4_relay_keep_legacy_and_modern_lan_structure(self):
        for profile in PROFILES:
            for mode in ("wds", "relayd"):
                with self.subTest(series=profile["series"], route=profile["route"], mode=mode):
                    device, manager, cancel = self.manager(profile, bridge=True)
                    lan = device.config["network"]["lan"]
                    lan.update({"type": "bridge", "ifname": "eth0.1"} if profile["series"] in ("19.07", "21.02") else {"device": "br-lan"})
                    original = copy.deepcopy(lan)
                    device.config["dhcp"] = {"lan": {".type": "dhcp", "interface": "lan", "ignore": "0", "ra": "server", "dhcpv6": "server"}}
                    device.config["firewall"]["zone_lan"].update(input="ACCEPT", output="ACCEPT", forward="ACCEPT")
                    request = WirelessRequest("sta", "radio7", TARGET.ssid, "test-password", options={
                        "encryption": "psk2", "channel": "36", "bssid": TARGET.bssid, "bridge_mode": mode,
                        "network": "lan", "proto": "dhcp", "zone": "lan" if mode == "relayd" else "wan"})
                    result = manager.save(request, cancel, Mock())
                    self.assertIn(result["section"], device.config["wireless"])
                    self.assertEqual(device.config["network"]["lan"], original)

    def test_missing_capability_blocks_relay_preverification_before_apply(self):
        device, manager, cancel = self.manager(PROFILES[0])
        device.profile["get_validate"] = {}
        with self.assertRaises(SignalError) as error:
            manager.verify(TARGET, "test-password", cancel, Mock())
        self.assertEqual(error.exception.code, "capability")
        self.assertNotIn(("session", "create"), device.calls)

    def test_generated_default_mode_also_requires_schema_support(self):
        device, manager, cancel = self.manager(PROFILES[1])
        device.config["wireless"]["radio7"].pop("htmode")
        device.profile["get_validate"]["radio7"]["device"].pop("htmode")
        request = WirelessRequest("ap", "radio7", "Home", section="home", options={"encryption": "psk2"})
        with self.assertRaises(SignalError):
            manager.save(request, cancel, Mock())
        self.assertNotIn(("session", "create"), device.calls)

    def test_malformed_parameter_descriptors_do_not_claim_support(self):
        device, manager, cancel = self.manager(PROFILES[1])
        schema = device.profile["get_validate"]["radio7"]["interface"]
        schema["skip_inactivity_poll"] = {"unexpected": "bool"}
        radio = manager.inspect(cancel).data["radios"][0]
        self.assertFalse(option_supported(radio, "skip_inactivity_poll"))

    def test_cancel_after_apply_restores_both_radio_and_interface_on_each_route(self):
        for profile in PROFILES:
            with self.subTest(series=profile["series"], route=profile["route"]):
                device, manager, cancel = self.manager(profile)
                original = copy.deepcopy(device.config)
                call = device.call
                def cancelled(obj, method, args):
                    result = call(obj, method, args)
                    if (obj, method) == ("uci", "apply"):
                        cancel.set()
                    return result
                device.lua.globals().bridge_call = cancelled
                request = WirelessRequest("ap", "radio7", "Changed", section="home",
                    options={"encryption": "psk2", "beacon_int": "125", "dtim_period": "4"})
                with self.assertRaises(InterruptedError):
                    manager.save(request, cancel, Mock())
                self.assertEqual(device.config, original)
                self.assertFalse(device.sessions)

    def test_stale_running_ap_name_is_not_confirmation_for_new_settings(self):
        for profile in PROFILES:
            with self.subTest(series=profile["series"], route=profile["route"]):
                device, _, _ = self.manager(profile)
                device.runtime_ssid = "Old-unapplied-name"
                payload = dict(action="status", radio="radio7", section="home", mode="ap")
                self.assertFalse(json.loads(device.execute(payload))["result"]["ready"])

    def test_ap_ready_requires_actual_native_link_up_on_each_route(self):
        for profile in PROFILES:
            with self.subTest(series=profile["series"], route=profile["route"]):
                device, _, _ = self.manager(profile)
                device.native_link_up = False
                payload = dict(action="status", radio="radio7", section="home", mode="ap")
                self.assertFalse(json.loads(device.execute(payload))["result"]["ready"])

    def test_wrong_radio_or_malformed_schema_does_not_create_any_session(self):
        for schema in (None, {}, {"device": [], "interface": []},
                       {"device": {"htmode": True}, "interface": {"ssid": 1, "mode": False}}):
            with self.subTest(schema=schema):
                device, manager, cancel = self.manager(PROFILES[-1])
                device.profile["get_validate"] = {"radio7": schema} if schema is not None else {"other_radio": {}}
                with self.assertRaises(SignalError):
                    manager.save(WirelessRequest("ap", "radio7", "Home", section="home",
                        options={"encryption": "psk2"}), cancel, Mock())
                self.assertNotIn(("session", "create"), device.calls)


class WirelessCapabilityEditorTests(unittest.TestCase):
    def test_each_profile_controls_match_schema_and_keep_missing_values(self):
        for profile in PROFILES:
            with self.subTest(series=profile["series"], route=profile["route"]):
                env = environment()
                schema = profile["get_validate"]["radio7"]
                radio = env.data["radios"][0]
                radio["option_support"] = {scope: {k: True for k in values} for scope, values in schema.items()}
                radio["beacon_int"] = "120"
                env.data["rows"][0]["beacon_int"] = "777"
                dialog = AccessPointSetupDialog("radio7", "5G", row=SimpleNamespace(section="home", ssid="Home"))
                try:
                    configure_ap(dialog, env)
                    self.assertEqual(dialog.beacon.text(), "120")
                    self.assertEqual(dialog.expert_fields.controls["skip_inactivity_poll"].isEnabled(), profile["series"] != "19.07")
                    request = ap_request(dialog)
                    self.assertEqual(request.options["beacon_int"], "120")
                    self.assertNotIn("skip_inactivity_poll", request.options)
                finally:
                    dialog.reject()
                    dialog.deleteLater()
        APP.processEvents()

    def test_reconfigure_does_not_reenable_intrinsically_unavailable_bandwidth(self):
        env = environment()
        radio = env.data["radios"][0]
        radio["htmode"] = "vendor-unknown"
        dialog = AccessPointSetupDialog("radio7", "5G")
        try:
            configure_ap(dialog, env)
            self.assertFalse(dialog.bandwidth_combo.isEnabled())
            radio["option_support"]["interface"]["skip_inactivity_poll"] = False
            configure_ap(dialog, env)
            self.assertFalse(dialog.expert_fields.controls["skip_inactivity_poll"].isEnabled())
            radio["option_support"]["interface"]["skip_inactivity_poll"] = True
            configure_ap(dialog, env)
            field = dialog.expert_fields.controls["skip_inactivity_poll"]
            self.assertTrue(field.isEnabled())
            self.assertIn("300", dialog.expert_fields.controls["max_inactivity"].toolTip())
        finally:
            dialog.reject()
            dialog.deleteLater()

    def test_relay_edit_does_not_clear_unsupported_saved_bssid(self):
        from modules.main_pages.packet_capture.openwrt.signal_setup_dialogs import RelaySetupDialog
        from modules.main_pages.packet_capture.openwrt.signal_setup_editor import configure_relay, relay_request
        env = environment()
        env.data["rows"].append(dict(section="upper", ssid="Upstream", device="radio7", mode="sta",
            network="wan", encryption="psk2", bssid=TARGET.bssid, macaddr="02:11:22:33:44:55"))
        radio = env.data["radios"][0]
        radio["option_support"]["interface"].update(bssid=False, wds=False)
        dialog = RelaySetupDialog("Upstream", radio_name="radio7",
            row=SimpleNamespace(section="upper", ssid="Upstream", running="active"))
        try:
            configure_relay(dialog, env)
            self.assertFalse(dialog.lock_bssid.isEnabled())
            self.assertFalse(dialog.bridge_mode.model().item(dialog.bridge_mode.findData("wds")).isEnabled())
            request = relay_request(dialog, TARGET, "proof")
            self.assertNotIn("bssid", request.options)
            self.assertNotIn("identity_action", request.options)
        finally:
            dialog.reject()
            dialog.deleteLater()

    def test_multicast_switch_selects_exact_supported_key_without_rewriting_legacy(self):
        for profile in PROFILES:
            with self.subTest(series=profile["series"], route=profile["route"]):
                env = environment()
                schema = profile["get_validate"]["radio7"]
                env.data["radios"][0]["option_support"] = {scope: dict.fromkeys(values, True) for scope, values in schema.items()}
                env.data["rows"][0]["multicast_to_unicast"] = "1"
                dialog = AccessPointSetupDialog("radio7", "5G", row=SimpleNamespace(section="home", ssid="Home"))
                try:
                    configure_ap(dialog, env)
                    controls = dialog.expert_fields.controls
                    modern = "multicast_to_unicast_all" in schema["interface"]
                    if modern:
                        self.assertTrue(controls["multicast_to_unicast"].isHidden())
                        self.assertFalse(controls["multicast_to_unicast_all"].isHidden())
                        controls["multicast_to_unicast_all"].click()
                        request = ap_request(dialog)
                        self.assertEqual(request.options["multicast_to_unicast_all"], "1")
                        self.assertNotIn("multicast_to_unicast", request.options)
                    elif "multicast_to_unicast" in schema["interface"]:
                        self.assertFalse(controls["multicast_to_unicast"].isHidden())
                        controls["multicast_to_unicast"].click()
                        self.assertEqual(ap_request(dialog).options["multicast_to_unicast"], "0")
                    else:
                        self.assertFalse(controls["multicast_to_unicast"].isEnabled())
                finally:
                    dialog.reject()
                    dialog.deleteLater()

    def test_disabled_basic_fields_never_overwrite_stored_values(self):
        env = environment()
        env.data["radios"][0]["option_support"]["interface"]["wmm"] = False
        env.data["rows"][0]["wmm"] = "0"
        dialog = AccessPointSetupDialog("radio7", "5G", row=SimpleNamespace(section="home", ssid="Home"))
        try:
            configure_ap(dialog, env)
            self.assertFalse(dialog.wmm.isEnabled())
            self.assertNotIn("wmm", ap_request(dialog).options)
        finally:
            dialog.reject()
            dialog.deleteLater()
