"""Read real native signal scripts against a deterministic runtime, without a router."""
import json
import shlex
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from tests.test_wireless_setup import LuaDevice, LuaRuntime
from modules.capture_services.openwrt.signal_commands import signal_command
from modules.capture_services.openwrt.signal_manager import RouterSignalManager, SignalError
from modules.capture_services.openwrt.command_runner import RemoteCommandResult
from modules.capture_services.openwrt.wireless_setup_manager import WirelessSetupManager
from modules.capture_services.openwrt.wireless_setup_models import WirelessEnvironment
from modules.capture_services.openwrt.wireless_scan_commands import SCAN_COMMAND_TIMEOUT_SECONDS


class RuntimeDevice(LuaDevice):
    def __init__(self):
        super().__init__()
        self.pending = False
        self.duplicate = False
        self.unavailable = False
        self.drift = False
        self.info_count = 0
        self.config["wireless"]["home"]["ssid"] = "HUAWEI-10Plus"
        self.config["wireless"]["home"]["description"] = "disguised phone"
        self.config["wireless"]["radio7"]["band"] = "2g"
        self.lua.globals().bridge_pending = lambda: self.wrap({"wireless": {"home": "changed"}} if self.pending else {})
        self.lua.execute("""
package.loaded["uci"]=nil
package.preload["uci"]=function() return {cursor=function() return {
 load=function() return true end,
 changes=function() return bridge_pending() end,
 foreach=function(self,config,kind,callback) bridge_foreach(config,kind,callback); return true end
} end} end
""")

    def call(self, obj, method, arguments):
        self.calls.append((obj, method))
        if obj == "network.wireless":
            interfaces = [{"section": "home", "ifname": "phy2-ap0", "config": {"ssid": "CMCC-250"}}]
            if self.duplicate:
                interfaces.append({"section": "home", "ifname": "phy2-ap1"})
            return self.wrap({"radio7": {"up": True, "pending": False, "interfaces": interfaces}})
        if obj == "network.device":
            return self.wrap({"up": True})
        if obj == "iwinfo":
            self.info_count += 1
            if self.drift:
                self.pending = not self.pending
            if self.unavailable:
                return None
            return self.wrap({"ssid": "CMCC-250", "frequency": 5180, "channel": 36})
        if obj.startswith("hostapd."):
            return self.wrap({"clients": {}})
        return None

    def run(self, name, command, timeout, cancel_event=None):
        self._output = []
        prefix, body = command.split(" <<'WINBOX_SIGNAL_LUA'\n", 1)
        payload = json.loads(shlex.split(prefix.rsplit("; ", 1)[-1])[2])
        self.lua.globals().arg = self.wrap({1: json.dumps(payload)})
        self.lua.execute(body.rsplit("\nWINBOX_SIGNAL_LUA", 1)[0])
        return RemoteCommandResult(name, 0, "".join(self._output))


@unittest.skipUnless(LuaRuntime, "Test-only Lua runtime unavailable")
class SignalRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.device = RuntimeDevice()
        self.manager = RouterSignalManager(SimpleNamespace(run=self.device.run))
        self.cancel = threading.Event()

    def test_actual_name_and_band_ignore_disguise_and_stale_config(self):
        row = self.manager.read(self.cancel).rows[0]
        self.assertEqual((row.ssid, row.runtime_ssid, row.runtime_band), ("HUAWEI-10Plus", "CMCC-250", "5g"))
        self.assertEqual(self.device.config["wireless"]["home"]["ssid"], "HUAWEI-10Plus")

    def test_pending_is_readable_but_native_change_remains_blocked(self):
        self.device.pending = True
        result = self.manager.read(self.cancel)
        self.assertTrue(result.pending_changes)
        self.assertEqual(result.rows[0].runtime_ssid, "CMCC-250")
        output = self.device.run("change", signal_command("change", revision=result.revision, section="home", enabled=False), 20)
        self.assertEqual(json.loads(output.stdout)["code"], "pending")

    def test_details_reuse_runtime_info(self):
        result = self.manager.read(self.cancel, details=True)
        self.assertEqual(result.rows[0].details.channel, "36")
        self.assertEqual(self.device.info_count, 1)

    def test_ambiguous_interfaces_do_not_guess(self):
        self.device.duplicate = True
        row = self.manager.read(self.cancel).rows[0]
        self.assertEqual((row.running, row.runtime_ssid, row.runtime_band), ("unknown", "", ""))

    def test_missing_iwinfo_uses_running_status_name(self):
        self.device.unavailable = True
        row = self.manager.read(self.cancel).rows[0]
        self.assertEqual((row.runtime_ssid, row.runtime_band), ("CMCC-250", ""))

    def test_concurrent_pending_edit_invalidates_read(self):
        self.device.drift = True
        with self.assertRaises(SignalError) as caught:
            self.manager.read(self.cancel)
        self.assertEqual(caught.exception.code, "changed")


class ScanBudgetTests(unittest.TestCase):
    def test_timeout_classification(self):
        for result, code in ((RemoteCommandResult("scan", -1, timed_out=True), "timeout"),
                             (RemoteCommandResult("scan", -1, cancelled=True, timed_out=True), "cancelled"),
                             (RemoteCommandResult("scan", 1), "execution")):
            manager = RouterSignalManager(SimpleNamespace(run=Mock(return_value=result)))
            with self.assertRaises(SignalError) as caught:
                manager._execute("scan", "command", 20, threading.Event())
            self.assertEqual(caught.exception.code, code)

    def test_second_radio_gets_full_budget_and_partial_results_survive_timeout(self):
        manager = WirelessSetupManager(None)
        manager.inspect = Mock(return_value=WirelessEnvironment(data={"radios": [
            {"name": "radio0", "interfaces": ["wlan0"]}, {"name": "radio1", "interfaces": ["wlan1"]}]}))
        now = [0.0]
        calls = []
        def scan(action, cancel, **values):
            calls.append(values)
            if values["interface"] == "wlan0":
                now[0] += 2.36
                return {"results": [{"ssid": "Home", "bssid": "02:11:22:33:44:55", "channel": 6,
                                     "mhz": 2437, "signal": -45, "encryption": {"enabled": False}}]}
            now[0] += 20
            raise SignalError("timeout", "scan timeout")
        manager._call = scan
        cancel = SimpleNamespace(is_set=lambda: False, wait=lambda _: False)
        message = Mock()
        with patch("modules.capture_services.openwrt.wireless_setup_manager.time.monotonic", side_effect=lambda: now[0]):
            result = manager.scan(10, cancel, message)
        self.assertEqual(len(result), 1)
        self.assertEqual([v["timeout"] for v in calls], [SCAN_COMMAND_TIMEOUT_SECONDS] * 2)
        self.assertTrue(any(args[0][1] == "warning" for args in message.call_args_list))

    def test_even_slow_first_radio_does_not_starve_second(self):
        manager = WirelessSetupManager(None)
        manager.inspect = Mock(return_value=WirelessEnvironment(data={"radios": [
            {"name": "radio0", "interfaces": ["wlan0"]}, {"name": "radio1", "interfaces": ["wlan1"]}]}))
        now = [0.0]
        calls = []
        def scan(action, cancel, **values):
            calls.append(values["interface"])
            now[0] += 15
            return {"results": []}
        manager._call = scan
        with patch("modules.capture_services.openwrt.wireless_setup_manager.time.monotonic", side_effect=lambda: now[0]):
            self.assertEqual(manager.scan(5, SimpleNamespace(is_set=lambda: False, wait=lambda _: False), Mock()), ())
        self.assertEqual(calls, ["wlan0", "wlan1"])


class SelectedRadioScanTests(unittest.TestCase):
    def manager(self):
        manager = WirelessSetupManager(None)
        manager.inspect = Mock(return_value=WirelessEnvironment(data={"radios": [
            {"name": "radio0", "phy": "phy0", "interfaces": ["wlan0"]},
            {"name": "radio1", "phy": "phy1", "interfaces": ["wlan1"]},
            {"name": "radio2", "phy": "phy2", "interfaces": []}]}))
        return manager

    def test_selected_radio_alone_scans_and_owns_every_result(self):
        for radio, interface in (("radio0", "wlan0"), ("radio1", "wlan1")):
            manager = self.manager()
            now = [0.0]
            def scan(action, cancel, **values):
                self.assertEqual(values["interface"], interface)
                now[0] += 6
                return {"results": [{"ssid": "Home", "bssid": "02:11:22:33:44:55", "channel": 6,
                                     "mhz": 2437, "signal": -45, "encryption": {"enabled": False}}]}
            manager._call = Mock(side_effect=scan)
            with patch("modules.capture_services.openwrt.wireless_setup_manager.time.monotonic", side_effect=lambda: now[0]):
                result = manager.scan(5, SimpleNamespace(is_set=lambda: False, wait=lambda _: False), Mock(), radio=radio)
            self.assertEqual({n.radio for n in result}, {radio})
            manager._call.assert_called_once()

    def test_missing_and_ambiguous_radio_never_fall_back_to_another(self):
        for radio in ("missing", "radio0;reboot"):
            manager = self.manager()
            manager._call = Mock()
            with self.assertRaises(SignalError):
                manager.scan(5, threading.Event(), Mock(), radio=radio)
            manager._call.assert_not_called()
        manager = self.manager()
        env = manager.inspect.return_value
        env.data["radios"].append(dict(env.data["radios"][0]))
        manager._call = Mock()
        with self.assertRaises(SignalError):
            manager.scan(5, threading.Event(), Mock(), radio="radio0")
        manager._call.assert_not_called()

    def test_service_forwards_explicit_radio_to_worker(self):
        from modules.capture_services.wireless_setup_service import WirelessSetupService
        manager = Mock()
        captured = []
        context = SimpleNamespace(manager=manager,
            _start=lambda action, operation, **values: captured.append((action, operation, values)) or True)
        self.assertTrue(WirelessSetupService.scan(context, 10, "radio1"))
        action, operation, values = captured[0]
        self.assertEqual((action, values), ("scan", {"scan_radio": "radio1"}))
        cancel, message = threading.Event(), Mock()
        operation(cancel, message)
        manager.scan.assert_called_once_with(10, cancel, message, radio="radio1", on_results=None)


class ScanStateTests(unittest.TestCase):
    def test_scan_start_clears_old_radio_results_and_replays_selected_radio(self):
        from modules.capture_services.wireless_setup_service import WirelessSetupService
        from modules.capture_services.openwrt.wireless_setup_models import WirelessState
        task = SimpleNamespace(busy=False, begin=Mock(return_value=threading.Event()), finish=Mock())
        signals = SimpleNamespace(snapshot=SimpleNamespace(connected=True), _forward_worker_message=Mock())
        owner = SimpleNamespace(_shutting_down=False, task_controller=task, _start_openwrt_worker=Mock())
        identity = object()
        state = SimpleNamespace(active=False, owner=owner, signals=signals,
            snapshot=WirelessState(networks=("old-radio-result",)), _connection_identity=lambda: identity,
            snapshotChanged=SimpleNamespace(emit=Mock()), completed=SimpleNamespace(emit=Mock()))
        self.assertTrue(WirelessSetupService._start(state, "scan", lambda *_: (), scan_radio="radio1"))
        self.assertEqual((state.snapshot.scan_radio, state.snapshot.networks, state.snapshot.busy), ("radio1", (), True))
        operation = owner._start_openwrt_worker.call_args.args[1]
        finished = owner._start_openwrt_worker.call_args.args[3]
        finished(operation(threading.Event()))
        self.assertFalse(state.active)
        self.assertFalse(state.snapshot.busy)
        self.assertEqual(state.snapshot.scan_radio, "radio1")

    def test_service_refuses_empty_or_unsafe_radio(self):
        from modules.capture_services.wireless_setup_service import WirelessSetupService
        context = SimpleNamespace(_start=Mock())
        for radio in (None, "", "radio0;reboot"):
            self.assertFalse(WirelessSetupService.scan(context, 10, radio))
        context._start.assert_not_called()


@unittest.skipUnless(LuaRuntime, "Test-only Lua runtime unavailable")
class NativeScanOwnershipTests(unittest.TestCase):
    def test_native_scan_rechecks_radio_interface_ownership(self):
        device = LuaDevice()
        manager = WirelessSetupManager(SimpleNamespace(run=device.run))
        manager._call("scan", threading.Event(), radio="radio7", interface="iface-home")
        self.assertIn(("iwinfo", "scan"), device.calls)
        device.calls.clear()
        with self.assertRaises(SignalError):
            manager._call("scan", threading.Event(), radio="radio0", interface="iface-home")
        self.assertNotIn(("iwinfo", "scan"), device.calls)
