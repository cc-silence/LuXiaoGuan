"""Do not publish toggle completion while a previously running AP is recovering."""
from dataclasses import replace
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import Mock, patch

from modules.capture_services.openwrt.signal_manager import RouterSignalManager, SignalError
from modules.capture_services.openwrt.signal_models import RouterSignal, SignalInventory


class ToggleRecoveryTests(unittest.TestCase):
    def fixture(self, enabled=False):
        station = RouterSignal("upper", "Upper", "sta", "radio7", "2g", enabled, False, "inactive" if enabled else "active")
        home = RouterSignal("home", "Home", "ap", "radio7", "2g", False, False, "active")
        before = SignalInventory((station, home), "a" * 64)
        target = replace(station, disabled=not enabled, running="active" if enabled else "inactive")
        manager = RouterSignalManager(SimpleNamespace())
        manager._run = Mock(return_value={"committed": True})
        cancel = Mock(spec=threading.Event)
        cancel.is_set.return_value = False
        cancel.wait.return_value = False
        return manager, before, target, home, cancel

    def test_enabling_and_disabling_upper_wait_for_ap_recovery(self):
        for enabled in (True, False):
            manager, before, target, home, cancel = self.fixture(enabled)
            for transient in ("unknown", "inactive"):
                with self.subTest(enabled=enabled, transient=transient):
                    recovering = SignalInventory((target, replace(home, running=transient)), "b" * 64)
                    recovered = replace(recovering, rows=(target, home))
                    manager.read = Mock(side_effect=[recovering, recovered])
                    self.assertIs(manager.change(before, "upper", enabled, cancel), recovered)
                    self.assertEqual(manager.read.call_count, 2)
                    self.assertEqual(manager._run.call_count, 1)
                    manager._run.reset_mock()

    def test_single_ap_can_become_ready_after_more_than_six_samples(self):
        manager, before, target, home, cancel = self.fixture(True)
        before = replace(before, rows=(replace(home,disabled=True,running="inactive"),))
        inactive = replace(before, rows=(replace(home,running="inactive"),))
        active = replace(before, rows=(home,))
        clock=[0.0]
        def advance(seconds):
            clock[0]+=seconds
            return False
        cancel.wait.side_effect=advance
        manager.read=Mock(side_effect=[inactive]*12+[active])
        with patch("modules.capture_services.openwrt.signal_manager.time.monotonic",side_effect=lambda:clock[0]):
            self.assertIs(manager.change(before,"home",True,cancel),active)
        self.assertEqual(manager.read.call_count,13)
        manager._run.assert_called_once()

    def test_network_reload_also_protects_original_ap_on_other_radio(self):
        manager, before, target, home, cancel = self.fixture()
        other = replace(home, section="guest", radio="radio0")
        before = replace(before, rows=before.rows + (other,))
        pending = SignalInventory((target, home, replace(other, running="inactive")), "b" * 64)
        recovered = replace(pending, rows=(target, home, other))
        manager.read = Mock(side_effect=[pending, recovered])
        self.assertIs(manager.change(before, "upper", False, cancel), recovered)
        self.assertEqual(manager.read.call_count, 2)

    def test_preexisting_down_or_disabled_ap_does_not_block_toggle(self):
        for changes in (dict(running="inactive"), dict(disabled=True), dict(radio_disabled=True)):
            manager, before, target, home, cancel = self.fixture()
            home = replace(home, **changes)
            before = replace(before, rows=(before.rows[0], home))
            result = replace(before, rows=(target, home))
            manager.read = Mock(return_value=result)
            self.assertIs(manager.change(before, "upper", False, cancel), result)
            cancel.wait.assert_not_called()

    def test_missing_or_replaced_ap_never_counts_as_recovered(self):
        for replacement in (None, dict(radio="another"), dict(mode="sta"), dict(disabled=True)):
            manager, before, target, home, cancel = self.fixture()
            rows = (target,) if replacement is None else (target, replace(home, **replacement))
            manager.read = Mock(return_value=SignalInventory(rows, "b" * 64))
            with patch("modules.capture_services.openwrt.signal_manager.time.monotonic", side_effect=[0, 0, 46, 46]):
                with self.assertRaises(SignalError) as error:
                    manager.change(before, "upper", False, cancel)
            self.assertEqual(error.exception.code, "unconfirmed")
            manager._run.assert_called_once()

    def test_cancellation_stops_recovery_without_another_reload(self):
        manager, before, target, home, cancel = self.fixture()
        manager.read = Mock(return_value=SignalInventory((target, replace(home, running="inactive")), "b" * 64))
        cancel.wait.return_value = True
        with self.assertRaises(SignalError) as error:
            manager.change(before, "upper", False, cancel)
        self.assertEqual(error.exception.code, "cancelled")
        self.assertEqual(manager.read.call_count, 1)
        manager._run.assert_called_once()

    def test_intentionally_disabled_ap_is_excluded_from_recovery(self):
        manager, before, target, home, cancel = self.fixture()
        result = replace(before, rows=(before.rows[0], replace(home, disabled=True, running="inactive")))
        manager.read = Mock(return_value=result)
        self.assertIs(manager.change(before, "home", False, cancel), result)
        cancel.wait.assert_not_called()


from tests.test_wireless_setup import LuaRuntime, TARGET
from tests.test_wireless_version_contracts import VersionBridgeDevice, PROFILES
from modules.capture_services.openwrt.wireless_setup_manager import WirelessSetupManager
from modules.capture_services.openwrt.wireless_setup_models import WirelessRequest


@unittest.skipUnless(LuaRuntime, "Lua unavailable")
class NativeToggleRecoveryTests(unittest.TestCase):
    def setup_device(self, profile):
        device = VersionBridgeDevice(profile)
        manager = WirelessSetupManager(device)
        cancel = Mock(spec=threading.Event)
        cancel.is_set.return_value = False
        cancel.wait.return_value = False
        value = manager.save(WirelessRequest("sta", "radio7", TARGET.ssid, "test-password",
                             options={"encryption": "psk2", "bridge_mode": "wds", "network": "lan", "zone": "wan"}),
                             cancel, Mock())
        return device, manager, cancel, value["section"]

    def test_transaction_confirms_only_after_original_ap_recovers(self):
        for profile in PROFILES:
            with self.subTest(series=profile["series"], route=profile["route"]):
                device, manager, cancel, station = self.setup_device(profile)
                original = device.call
                samples = []
                def call(obj, method, args):
                    if obj == "hostapd.iface-home" and method == "get_status" and device.config["wireless"][station].get("disabled") == "1":
                        samples.append(True)
                        if len(samples) == 1:
                            return device.wrap({"status": "DISABLED"}), None
                    if obj == "uci" and method == "confirm":
                        self.assertGreaterEqual(len(samples), 2)
                    return original(obj, method, args)
                device.lua.globals().bridge_call = call
                manager.toggle(station, False, cancel)
                self.assertGreaterEqual(len(samples), 2)
                self.assertFalse(device.sessions)

    def test_preexisting_nonrunning_ap_does_not_turn_toggle_into_repair(self):
        device, manager, cancel, station = self.setup_device(PROFILES[0])
        original = device.call
        def call(obj, method, args):
            if obj == "hostapd.iface-home" and method == "get_status":
                return device.wrap({"status": "DISABLED"}), None
            return original(obj, method, args)
        device.lua.globals().bridge_call = call
        manager.toggle(station, False, cancel)
        self.assertEqual(device.config["wireless"][station]["disabled"], "1")
        cancel.wait.assert_not_called()

    def test_ap_recovery_failure_rolls_back_without_confirming_new_config(self):
        import copy
        device, manager, cancel, station = self.setup_device(PROFILES[0])
        before = copy.deepcopy(device.config)
        original = device.call
        def call(obj, method, args):
            if obj == "hostapd.iface-home" and method == "get_status" and device.config["wireless"][station].get("disabled") == "1":
                return device.wrap({"status": "DISABLED"}), None
            return original(obj, method, args)
        device.lua.globals().bridge_call = call
        device.calls.clear()
        clock = [0.0]
        def wait(seconds):
            clock[0] += 46
            return False
        cancel.wait.side_effect = wait
        with patch("time.monotonic", side_effect=lambda: clock[0]):
            with self.assertRaises(SignalError) as error:
                manager.toggle(station, False, cancel)
        self.assertEqual(error.exception.code, "unconfirmed")
        self.assertEqual(device.config, before)
        self.assertIn(("uci", "rollback"), device.calls)
        self.assertNotIn(("uci", "confirm"), device.calls)
        self.assertFalse(device.sessions)
