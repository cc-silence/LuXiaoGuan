"""原生无线管理的离线回归，执行真实 Lua 脚本并模拟设备管理接口。"""
import copy
import hashlib
import json
from pathlib import Path
import shlex
import sys
import threading
import unittest
from unittest.mock import Mock, patch
from dataclasses import asdict, replace
from types import SimpleNamespace

# Test-only optional runtime; the application does not import this dependency.
_test_runtime = Path(__file__).resolve().parents[1] / "build" / "wireless-test-runtime"
if _test_runtime.is_dir():
    sys.path.insert(0, str(_test_runtime))
try:
    from lupa.lua51 import LuaRuntime
except ImportError:
    LuaRuntime = None

from modules.capture_services.openwrt.command_runner import RemoteCommandResult
from modules.capture_services.openwrt.signal_manager import SignalError
from modules.capture_services.openwrt.wireless_setup_commands import BOOT, wireless_command
from modules.capture_services.openwrt.wireless_setup_manager import WirelessSetupManager, supports_interfaces
from modules.capture_services.openwrt.wireless_setup_models import WirelessNetwork, WirelessRequest, parse_scan, validate_request

CAPABILITIES = """Supported interface modes:
 * managed
 * AP
Band 1:
valid interface combinations:
 * #{ managed } <= 1, #{ AP } <= 8,
   total <= 8, #channels <= 1
"""
TARGET = WirelessNetwork("radio7", "02:11:22:33:44:55", "Upstream", 36, "5G", -42, "psk2")


class LuaDevice:
    """真实 Lua 解释器，UCI 会话隔离及回滚由确定性的设备替身提供。"""
    def __init__(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.config = {
            "wireless": {"radio7": {".type": "wifi-device", "type": "mac80211", "band": "5g", "channel": "36", "htmode": "HE80"},
                         "home": {".type": "wifi-iface", "ssid": "Home", "device": "radio7", "mode": "ap", "network": "lan", "encryption": "psk2", "key": "original-password"}},
            "network": {"lan": {".type": "interface", "proto": "static", "ipaddr": "192.168.8.1"},
                        "wan": {".type": "interface", "proto": "dhcp"}},
            "firewall": {"zone_lan": {".type": "zone", "name": "lan", "network": ["lan"]},
                         "zone_wan": {".type": "zone", "name": "wan", "network": ["wan"], "masq": "1"}}
        }
        self.config["dhcp"] = {}
        self.sessions = {}
        self.calls = []
        self.applied = None
        self.backup = None
        self.serial = 0
        self.connected = True
        self.complete = True
        self.fail = None
        self._output = []
        g = self.lua.globals()
        g.bridge_json_parse = lambda value: self.wrap(json.loads(value))
        g.bridge_json_stringify = lambda value: json.dumps(self.unwrap(value))
        g.bridge_call = self.call
        g.bridge_get = self.get
        g.bridge_foreach = self.foreach
        g.bridge_read = self.read
        g.bridge_kernel_mac = self.kernel_mac
        g.bridge_write = lambda text: self._output.append(text)
        self.lua.execute("""
package.preload["luci.jsonc"]=function() return {parse=bridge_json_parse,stringify=bridge_json_stringify} end
local cursor={
 load=function() return true end,
 changes=function() return {} end,
 get=function(self,config,section,key) return bridge_get(config,section,key) end,
 get_all=function(self,config,section) return bridge_get(config,section,nil,true) end,
 foreach=function(self,config,kind,callback) bridge_foreach(config,kind,callback) end
}
package.preload["uci"]=function() return {cursor=function() return cursor end} end
package.preload["ubus"]=function() return {connect=function() return {
 call=function(self,object,method,args) return bridge_call(object,method,args) end,
 close=function() end} end} end
local native_open=io.open
io.open=function(path,mode)
 local iface=path:match("^/sys/class/net/([^/]+)/address$")
 if not iface then return native_open(path,mode) end
 local mac=bridge_kernel_mac(iface)
 if not mac then return nil end
 return {read=function() return mac..string.char(10) end,close=function() return true end}
end
io.write=bridge_write
io.popen=function(command) return {read=function() return bridge_read(command) end,close=function() return true end} end
""")

    def kernel_mac(self, iface):
        value, error=self.call("network.device", "status", self.wrap(dict(name=iface)))
        return None if error else self.unwrap(value).get("macaddr")

    def wrap(self, value):
        if isinstance(value, dict):
            return self.lua.table_from({k: self.wrap(v) for k, v in value.items()})
        if isinstance(value, (list, tuple)):
            return self.lua.table_from([self.wrap(v) for v in value])
        return value

    def unwrap(self, value):
        if hasattr(value, "items"):
            items = dict(value.items())
            if items and set(items) == set(range(1, len(items)+1)):
                return [self.unwrap(items[i]) for i in range(1, len(items)+1)]
            return {k: self.unwrap(v) for k, v in items.items()}
        return value

    def get(self, config, section, key=None, all_values=False):
        row = self.config.get(config, {}).get(section)
        if row is None:
            return None
        if all_values:
            return self.wrap(dict(row, **{".name": section, ".anonymous": False}))
        return row.get(key) if key else row[".type"]

    def foreach(self, config, kind, callback):
        for section, row in self.config.get(config, {}).items():
            if kind is None or row[".type"] == kind:
                callback(self.wrap(dict(row, **{".name": section, ".anonymous": False})))

    def revision(self):
        return "".join(hashlib.sha256(json.dumps(self.config[k], sort_keys=True).encode()).hexdigest() for k in ("wireless", "network", "firewall"))

    def read(self, command):
        if "sha256sum" in command:
            key = command.split("/etc/config/")[-1].split()[0]
            return hashlib.sha256(json.dumps(self.config[key], sort_keys=True).encode()).hexdigest() + " file"
        if command.startswith("iw phy"):
            return CAPABILITIES
        if command.startswith("wpa_cli"):
            return "bssid=" + TARGET.bssid + "\nwpa_state=" + ("COMPLETED" if self.complete else "ASSOCIATING") + "\n"
        raise AssertionError("Unexpected read")

    def call(self, obj, method, arguments):
        args = self.unwrap(arguments)
        self.calls.append((obj, method))
        if self.fail == (obj, method):
            return None, 2
        if obj == "session":
            if method == "create":
                self.serial += 1
                sid = format(self.serial, "032x")
                self.sessions[sid] = copy.deepcopy(self.config)
                return self.wrap({"ubus_rpc_session": sid}), None
            if method == "destroy":
                self.sessions.pop(args["ubus_rpc_session"], None)
            return None, None
        if obj == "uci":
            sid = args["ubus_rpc_session"]
            if method in ("set", "add", "delete"):
                data = self.sessions[sid][args["config"]]
                name = args.get("section", args.get("name"))
                if method == "add":
                    if name in data:
                        return None, 4
                    data[name] = {".type": args["type"], **args["values"]}
                elif method == "set":
                    data[name].update(args["values"])
                else:
                    if "option" in args: data[name].pop(args["option"], None)
                    else: data.pop(name, None)
            elif method == "apply":
                if self.applied:
                    return None, 7
                self.backup = copy.deepcopy(self.config)
                self.config = copy.deepcopy(self.sessions[sid])
                self.applied = sid
            elif method == "rollback":
                if not self.applied:
                    return None, 5
                if self.applied != sid:
                    return None, 6
                self.config = self.backup
                self.applied = self.backup = None
            elif method == "confirm":
                if not self.applied:return None,5
                if self.applied != sid:
                    return None, 6
                self.applied = self.backup = None
            return None, None
        if obj == "network.wireless":
            if method == "get_validate":
                from modules.capture_services.openwrt.wireless_capabilities import RADIO_SETTINGS, IFACE_SETTINGS
                return self.wrap({"radio7": {"device": dict.fromkeys(RADIO_SETTINGS, "string"),
                    "interface": dict.fromkeys(IFACE_SETTINGS, "string")}}), None
            interfaces = []
            for name, row in self.config["wireless"].items():
                if row.get(".type") == "wifi-iface" and row.get("disabled") != "1":
                    interfaces.append({"section": name, "ifname": "iface-" + name,"config":copy.deepcopy(row)})
            return self.wrap({"radio7": {"up": True, "pending": False, "interfaces": interfaces,"config":copy.deepcopy(self.config["wireless"]["radio7"])}}), None
        if obj == "network.device":
            return self.wrap({"up": True}), None
        if obj.startswith("hostapd."):
            section = obj.removeprefix("hostapd.iface-")
            return self.wrap({"status": "ENABLED", "ssid": self.config["wireless"].get(section, {}).get("ssid", "")}), None
        if obj == "iwinfo":
            values = {"phyname": {"phyname": "phy2"}, "info": {"htmodes": ["HE20", "HE40", "HE80"]},
                "freqlist": {"results": [{"channel": 36, "mhz": 5180, "restricted": False}, {"channel": 44, "mhz": 5220, "restricted": False}]},
                "txpowerlist": {"results": [{"dbm": 20, "mw": 100}]},
                "countrylist": {"results": [{"code": "US", "iso3166": "US", "country": "United States"}]},
                "scan": {"results": [{"bssid": TARGET.bssid, "ssid": TARGET.ssid, "channel": 36, "mhz": 5180,
                    "signal": 4294967254, "encryption": {"enabled": True, "authentication": ["psk"], "wpa": [2]}}]}}
            return self.wrap(values[method]), None
        return None, 4

    def execute(self, payload):
        self._output = []
        self.lua.globals().arg = self.wrap({1: json.dumps(payload)})
        self.lua.execute(BOOT)
        return "".join(self._output)

    def run(self, name, command, timeout, cancel_event=None):
        if name == "router_signal_probe":
            data = {"ok": True, "result": dict.fromkeys(("lua", "uci", "ubus", "jsonc", "sha256sum", "opkg", "root"), 1)}
            data["result"].update(apk=0, lua_bin="lua")
            output = json.dumps(data)
        elif name == "wireless_setup_probe":
            output = "\n".join(k+"=1" for k in ("iw_runtime", "wpa_cli_runtime", "uci", "ubus", "iw", "wpa_cli", "hostapd", "wpa_supplicant", "rpcd", "bus_uci", "bus_session", "bus_iwinfo", "bus_network.wireless", "iwinfo_module", "rollback", "ap_sae", "ap_acs", "ap_11ac", "ap_11ax", "ap_11be", "ap_11r", "ap_eap", "ap_owe", "sta_owe", "sta_sae", "relayd"))
        else:
            payload = json.loads(shlex.split(command.split(" <<", 1)[0])[2])
            output = self.execute(payload)
        return RemoteCommandResult(name, 0, output)


class WirelessModelTests(unittest.TestCase):
    def test_cipher_and_signed_signal(self):
        rows = [{"bssid": TARGET.bssid, "ssid": "A", "channel": 36, "mhz": 5180, "signal": 4294967254,
                 "encryption": {"enabled": True, "wpa": [2], "authentication": ["psk"]}}]
        result = parse_scan(rows, "radio7")
        self.assertEqual((result[0].encryption, result[0].power), ("psk2", -42))
        rows[0]["encryption"] = {"enabled": False}
        self.assertEqual(parse_scan(rows, "radio7")[0].encryption, "none")
        rows[0]["encryption"] = {"enabled": True, "wpa": [2], "authentication": ["eap"]}
        self.assertTrue(parse_scan(rows, "radio7")[0].reason)

    def test_limits_and_secret_repr(self):
        request = WirelessRequest("ap", "radio7", "Good", "private-password", options={"encryption": "psk2"})
        self.assertNotIn("private-password", repr(request))
        validate_request(request)
        for bad in (replace(request, ssid="x"*33), replace(request, password="short"),
                    replace(request, options={"unknown": "1"}), replace(request, options={"country": "XX;reboot"})):
            with self.assertRaises(ValueError):
                validate_request(bad)

    def test_combinations_do_not_mix_alternatives(self):
        self.assertTrue(supports_interfaces(CAPABILITIES, 2, 1))
        self.assertFalse(supports_interfaces(CAPABILITIES, 8, 1))
        self.assertFalse(supports_interfaces(CAPABILITIES, 1, 2))
        split = CAPABILITIES.split("valid interface combinations:")[0] + "valid interface combinations:\n * #{ AP } <= 8, total <= 8\n * #{ managed } <= 2, total <= 2"
        self.assertFalse(supports_interfaces(split, 1, 1))


    def test_native_probe_shell_preserves_lines_and_method_order(self):
        import os
        import subprocess
        from modules.capture_services.openwrt.wireless_setup_commands import PROBE
        bash = Path("C:/Program Files/Git/bin/bash.exe")
        if not bash.is_file():
            self.skipTest("Git Bash not installed")
        setup = """
uci(){ :; }
iw(){ :; }
wpa_cli(){ :; }
rpcd(){ :; }
ubus(){
 if [ "$1" = "-v" ];then printf '%s\\n' '"rollback" "confirm" "apply"';
 else printf '%s\\n' "$2";fi
}
hostapd(){ case "$1" in -vsae|-vacs) return 0;; *) return 1;;esac; }
wpa_supplicant(){ return 1; }
"""
        result = subprocess.run([str(bash), "--noprofile", "--norc"], input=setup+PROBE,
            capture_output=True, text=True, encoding="utf-8", timeout=8,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        self.assertEqual(result.returncode, 0, result.stderr)
        flags = dict(line.split("=", 1) for line in result.stdout.splitlines())
        self.assertEqual(flags["ap_sae"], "1")
        self.assertEqual(flags["ap_acs"], "1")
        self.assertEqual(flags["ap_11ax"], "0")
        self.assertEqual(flags["sta_sae"], "0")
        self.assertEqual(flags["rollback"], "1")

    def test_long_secret_command_uses_stdin(self):
        from modules.capture_services.openwrt.command_runner import _command_request
        command = wireless_command("stage", values=asdict(WirelessRequest("ap", "radio7", "A", "secret-password")))
        request, payload = _command_request(command)
        self.assertNotIn("secret-password", request)
        self.assertIn(b"secret-password", payload)


@unittest.skipUnless(LuaRuntime, "Test-only Lua runtime unavailable")
class NativeWirelessTests(unittest.TestCase):
    def setUp(self):
        self.device = LuaDevice()
        self.runner = SimpleNamespace(run=self.device.run, ssh_client=SimpleNamespace(host_fingerprint="device-one"))
        self.manager = WirelessSetupManager(self.runner)
        self.event = threading.Event()
        self.message = Mock()

    def test_environment_never_exports_password(self):
        env = self.manager.inspect(self.event)
        self.assertTrue(env.ready)
        self.assertEqual(env.data["radios"][0]["phy"], "phy2")
        self.assertNotIn("original-password", repr(env))
        result = self.manager._call("scan", self.event, radio="radio7", interface="iface-home")
        self.assertEqual(parse_scan(result["results"], "radio7")[0].ssid, TARGET.ssid)

    def test_verification_restores_before_success_and_requires_no_ip(self):
        original = copy.deepcopy(self.device.config)
        proof = self.manager.verify(TARGET, "upstream-password", self.event, self.message)
        self.assertTrue(proof["token"])
        self.assertEqual(self.device.config, original)
        self.assertFalse(self.device.sessions)
        self.assertIn(("uci", "rollback"), self.device.calls)
        self.assertNotIn(("uci", "confirm"), self.device.calls)

    def test_save_ap_preserves_other_radio_values_and_original_password(self):
        env = self.manager.inspect(self.event)
        request = WirelessRequest("ap", "radio7", "Renamed", section="home", options={"encryption": "psk2", "network": "lan"}, revision=env.data["revision"])
        self.manager.save(request, self.event, self.message)
        self.assertEqual(self.device.config["wireless"]["home"]["key"], "original-password")
        self.assertEqual(self.device.config["wireless"]["radio7"]["htmode"], "HE80")
        self.assertIn(("uci", "confirm"), self.device.calls)
        self.assertFalse(self.device.sessions)

    def test_new_open_ap_has_no_key_and_no_delete_missing_option(self):
        request = WirelessRequest("ap", "radio7", "Guest", options={"encryption": "none", "network": "lan"})
        result = self.manager.save(request, self.event, self.message)
        self.assertNotIn("key", self.device.config["wireless"][result["section"]])

    def test_new_sta_saves_once_without_preverification_and_sets_dhcp_wan_and_remark(self):
        request = WirelessRequest("sta", "radio7", TARGET.ssid, "upstream-password",
            label="Upstairs", options={"encryption": "psk2", "bssid": TARGET.bssid, "zone": "wan", "proto": "dhcp"})
        result = self.manager.save(request, self.event, self.message)
        self.assertEqual(self.device.calls.count(("uci", "apply")), 1)
        self.assertNotIn(("uci", "rollback"), self.device.calls)
        self.assertEqual(self.device.config["network"][result["network"]]["proto"], "dhcp")
        self.assertIn(result["network"], self.device.config["firewall"]["zone_wan"]["network"])
        self.assertEqual(self.device.config["wireless"][result["section"]]["description"], "Upstairs")
        self.assertNotEqual(self.device.config["wireless"]["home"].get("disabled"), "1")

    def test_failed_apply_restores_and_destroys_session(self):
        original = copy.deepcopy(self.device.config)
        self.device.fail = ("uci", "apply")
        with self.assertRaises(SignalError):
            self.manager.verify(TARGET, "upstream-password", self.event, self.message)
        self.assertEqual(original, self.device.config)
        self.assertFalse(self.device.sessions)

    def test_cancelled_after_apply_rolls_back(self):
        original = copy.deepcopy(self.device.config)
        def cancel(*_args, **_kwargs):
            self.event.set()
            raise InterruptedError("cancel")
        with patch.object(self.manager, "_wait_ready", side_effect=cancel):
            with self.assertRaises(InterruptedError):
                self.manager.verify(TARGET, "upstream-password", self.event, self.message)
        self.assertEqual(original, self.device.config)
        self.assertFalse(self.device.sessions)

    def test_failure_to_restore_does_not_issue_proof(self):
        self.device.fail = ("uci", "rollback")
        with self.assertRaises(SignalError) as result:
            self.manager.verify(TARGET, "upstream-password", self.event, self.message)
        self.assertEqual(result.exception.code, "recovery")
        self.assertIsNone(self.manager._proof)

    def test_expired_editor_is_rejected_and_new_password_saves_directly(self):
        env = self.manager.inspect(self.event)
        self.device.config["wireless"]["home"]["ssid"] = "Other editor"
        request = WirelessRequest("ap", "radio7", "Edit", "new-password", options={"encryption": "psk2", "network": "lan"}, revision=env.data["revision"])
        with self.assertRaises(SignalError):
            self.manager.save(request, self.event, self.message)
        request = WirelessRequest("sta", "radio7", TARGET.ssid, "different-password", options={"encryption": "psk2", "bssid": TARGET.bssid})
        result = self.manager.save(request, self.event, self.message)
        self.assertEqual(self.device.config["wireless"][result["section"]]["key"], "different-password")

    def test_scan_runs_native_reader_with_bounded_duration_and_deduplicates(self):
        class ClockCancel:
            elapsed = 0.0
            def is_set(self):
                return False
            def wait(self, timeout):
                self.elapsed += max(timeout, .01)
                return False
        clock = ClockCancel()
        with patch("modules.capture_services.openwrt.wireless_setup_manager.time.monotonic", side_effect=lambda: clock.elapsed):
            result = self.manager.scan(5, clock, self.message)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].bssid, TARGET.bssid)
        self.assertLessEqual(clock.elapsed, 5.01)
        self.assertNotIn(("uci", "apply"), self.device.calls)

    def test_install_only_approved_missing_helpers(self):
        from modules.capture_services.openwrt.wireless_setup_models import WirelessEnvironment
        ready = self.manager.inspect(self.event)
        missing = WirelessEnvironment(missing=("wpa-cli",))
        with patch.object(self.manager, "inspect", side_effect=(missing, ready)), patch(
                "modules.capture_services.openwrt.dependency_manager.OpenWrtDependencyManager._install_online") as install:
            result = self.manager.install(("wpa-cli",), None, self.event, self.message)
        install.assert_called_once_with(("wpa-cli",), self.event, self.message)
        self.assertTrue(result.ready)

    def test_install_rejects_wireless_stack_replacement_and_changed_list(self):
        from modules.capture_services.openwrt.wireless_setup_models import WirelessEnvironment
        with patch.object(self.manager, "inspect", return_value=WirelessEnvironment(missing=("wpad-full",))):
            with self.assertRaises(SignalError):
                self.manager.install(("wpad-full",), None, self.event, self.message)
        with patch.object(self.manager, "inspect", return_value=WirelessEnvironment(missing=("wpa-cli", "iw"))):
            with self.assertRaises(SignalError):
                self.manager.install(("wpa-cli",), None, self.event, self.message)

    def test_new_rpc_plugin_reloads_only_when_not_registered(self):
        from modules.capture_services.openwrt.wireless_setup_models import WirelessEnvironment
        ready = self.manager.inspect(self.event)
        missing = WirelessEnvironment(missing=("rpcd-mod-iwinfo",))
        unloaded = WirelessEnvironment(issues=("not loaded",), data={"reload_needed": True})
        with patch.object(self.manager, "inspect", side_effect=(missing, unloaded, ready)), patch(
                "modules.capture_services.openwrt.dependency_manager.OpenWrtDependencyManager._install_online"), patch.object(
                self.manager.signal, "_execute") as run:
            self.manager.install(("rpcd-mod-iwinfo",), None, self.event, self.message)
        self.assertEqual(run.call_count, 1)
        self.assertIn("/etc/init.d/rpcd restart", run.call_args.args[1])
        self.assertNotIn("wifi ", run.call_args.args[1])

    def test_advanced_values_reach_native_config(self):
        request = WirelessRequest("ap", "radio7", "Home", section="home", options={
            "encryption": "psk2", "wmm": "1", "ieee80211w": "1", "dtim_period": "2", "beacon_int": "100"})
        self.manager.save(request, self.event, self.message)
        row = self.device.config["wireless"]["home"]
        self.assertEqual((row["wmm"], row["dtim_period"]), ("1", "2"))
        self.assertNotIn("beacon_int", row)
        self.assertEqual(self.device.config["wireless"]["radio7"]["beacon_int"], "100")

    def test_mixed_network_uses_supported_wpa2_without_replacing_stack(self):
        env = self.manager.inspect(self.event)
        data = copy.deepcopy(env.data)
        data["features"]["sta_sae"] = False
        before = copy.deepcopy(self.device.config)
        with patch.object(self.manager, "inspect", return_value=replace(env, data=data)):
            result = self.manager.verify(replace(TARGET, encryption="sae-mixed"), "upstream-password", self.event, self.message)
        self.assertEqual(result["network"].encryption, "psk2")
        self.assertEqual(self.device.config, before)

    def test_unsupported_wpa3_is_rejected_before_apply(self):
        env = self.manager.inspect(self.event)
        data = copy.deepcopy(env.data)
        data["features"].update(sta_sae=False, ap_sae=False)
        with patch.object(self.manager, "inspect", return_value=replace(env, data=data)):
            with self.assertRaises(SignalError):
                self.manager.verify(replace(TARGET, encryption="sae"), "upstream-password", self.event, self.message)
            with self.assertRaises(SignalError):
                self.manager.save(WirelessRequest("ap", "radio7", "New", "new-password", options={"encryption": "sae"}), self.event, self.message)
        self.assertNotIn(("uci", "apply"), self.device.calls)

    def test_upstream_channel_is_used_then_restored_and_saved(self):
        target = replace(TARGET, channel=44)
        seen = []
        original_wait = self.manager._wait_ready
        def observe(request, section, cancel, bssid="", **kwargs):
            seen.append(self.device.config["wireless"]["radio7"]["channel"])
            return original_wait(request, section, cancel, bssid, **kwargs)
        with patch.object(self.manager, "_wait_ready", side_effect=observe):
            result = self.manager.verify(target, "upstream-password", self.event, self.message)
        self.assertEqual(seen, ["44"])
        self.assertEqual(self.device.config["wireless"]["radio7"]["channel"], "36")
        request = WirelessRequest("sta", "radio7", target.ssid, "upstream-password", verification=result["token"],
            options={"encryption": "psk2", "bssid": target.bssid, "channel": "44", "zone": "wan"})
        self.manager.save(request, self.event, self.message)
        self.assertEqual(self.device.config["wireless"]["radio7"]["channel"], "44")
        self.assertNotEqual(self.device.config["wireless"]["home"].get("disabled"), "1")


if __name__ == "__main__":
    unittest.main()
