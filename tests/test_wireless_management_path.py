"""Shared capture-path evidence, exact interface ownership and pre-apply warnings."""
import copy
import threading
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from tests.test_management_link import report
from tests.test_wireless_setup import LuaRuntime
from tests.test_wireless_version_contracts import PROFILES, VersionDevice
from modules.capture_services.openwrt.management_link import ManagementLink, parse_management_link
from modules.capture_services.openwrt.wireless_setup_models import WirelessRequest
from modules.capture_services.openwrt.wireless_setup_manager import WirelessSetupManager
from modules.capture_services.openwrt.command_runner import RemoteCommandResult
from modules.capture_services.openwrt.wireless_management_path import (
    ManagementConnection, match_management_signal, inspect_management_connection, management_connection_notice)


class ManagementPathTests(unittest.TestCase):
    def setUp(self):
        self.request = WirelessRequest("ap", "radio7", "Home", section="home")
        self.link = ManagementLink("wireless", "iface-home", "phy2")
        self.runtime = {"interfaces": [{"interface": "iface-home", "section": "home",
                                       "radio": "radio7", "mode": "ap"}], "target_phy": "phy2"}

    def test_same_signal_sibling_and_other_radio_are_distinguished(self):
        self.assertEqual(match_management_signal(self.request, self.link, self.runtime).risk, "same_signal")
        self.request = WirelessRequest("ap", "radio7", "Another", section="other")
        self.assertEqual(match_management_signal(self.request, self.link, self.runtime).risk, "same_radio")
        self.request = WirelessRequest("ap", "radio3", "Another", section="other")
        self.runtime["target_phy"] = "phy7"
        self.assertEqual(match_management_signal(self.request, self.link, self.runtime).risk, "other_radio")

    def test_radio_aliases_and_conflicting_phy_evidence_are_not_guessed(self):
        request = WirelessRequest("ap", "alias", "Another", section="other")
        self.assertEqual(match_management_signal(request, self.link, self.runtime).risk, "same_radio")
        self.runtime["target_phy"] = ""
        self.assertEqual(match_management_signal(request, self.link, self.runtime).risk, "unknown")
        self.runtime["target_phy"] = "phy7"
        self.assertEqual(match_management_signal(self.request, self.link, self.runtime).risk, "unknown")

    def test_ambiguity_and_missing_ownership_do_not_claim_exact_signal(self):
        for data in ({}, {"interfaces": {}}, {"interfaces": self.runtime["interfaces"] * 2},
                     {"interfaces": [{"interface": "iface-home"}]}):
            with self.subTest(data=data):
                self.assertEqual(match_management_signal(self.request, self.link, data).risk, "unknown")
        self.assertEqual(match_management_signal(self.request, self.link,
            {"interfaces": [], "target_phy": "phy2"}).risk, "same_radio")
        self.assertEqual(match_management_signal(self.request, self.link,
            {"interfaces": [], "target_phy": "phy7"}).risk, "unknown")

    def test_wired_and_unknown_capture_probe_results_are_shared(self):
        wired = parse_management_link(report())
        self.assertEqual(match_management_signal(self.request, wired, {}).kind, "wired")
        self.assertEqual(match_management_signal(self.request, ManagementLink(), self.runtime).kind, "unknown")

    def test_unknown_path_does_not_invent_device_interface_or_create_session(self):
        manager = Mock()
        manager.runner = Mock()
        with patch("modules.capture_services.openwrt.wireless_management_path.read_management_link",
                   return_value=ManagementLink()):
            value = inspect_management_connection(manager, self.request, threading.Event())
        self.assertEqual(value, ManagementConnection())
        manager._call.assert_not_called()

    def test_cancelled_probe_does_not_reach_confirmation(self):
        manager = Mock()
        with patch("modules.capture_services.openwrt.wireless_management_path.read_management_link",
                   side_effect=InterruptedError("cancel")):
            with self.assertRaises(InterruptedError):
                inspect_management_connection(manager, self.request, threading.Event())
        manager._call.assert_not_called()



    def test_cancel_between_worker_return_and_publication_discards_precheck_success(self):
        from tests.test_feedback_regressions import Owner
        from modules.capture_services.router_signal_service import RouterSignalService
        from modules.capture_services.openwrt.signal_models import SignalSnapshot
        owner = Owner()
        signals = RouterSignalService(owner)
        signals.snapshot = SignalSnapshot(connected=True, stale=False)
        setup = signals.setup
        setup.manager.management_connection = Mock(return_value=ManagementConnection("wired", "independent"))
        completed = []
        setup.completed.connect(completed.append)
        try:
            self.assertTrue(setup.check_management_connection(self.request))
            operation, event, success, failure = owner.workers.pop(0)
            result = operation(event, Mock())
            self.assertFalse(result.error)
            self.assertTrue(setup.cancel())
            success(result)
            self.assertEqual(completed[-1].code, "cancelled")
            self.assertIsNone(completed[-1].value)
            self.assertFalse(setup.active)
            self.assertFalse(owner.task_controller.busy)
        finally:
            owner.deleteLater()

    def test_read_only_precheck_does_not_queue_inventory_work_before_confirmation(self):
        from tests.test_feedback_regressions import Owner
        from modules.capture_services.router_signal_service import RouterSignalService
        from modules.capture_services.openwrt.signal_models import SignalSnapshot
        owner = Owner()
        signals = RouterSignalService(owner)
        signals.snapshot = SignalSnapshot(connected=True, stale=False)
        signals._verified = True
        setup = signals.setup
        setup.manager.management_connection = Mock(return_value=ManagementConnection("wired", "independent"))
        try:
            self.assertTrue(setup.check_management_connection(self.request))
            operation, event, success, failure = owner.workers.pop(0)
            success(operation(event, Mock()))
            from tests.test_standalone import APP
            APP.processEvents()
            self.assertFalse(owner.task_controller.busy)
            self.assertFalse(owner.workers, "Read-only precheck must not race confirmation with a refresh")
        finally:
            owner.deleteLater()

    def test_confirmed_and_uncertain_notices_remain_distinct(self):
        same = management_connection_notice(ManagementConnection("wifi", "same_signal"))
        unknown = management_connection_notice(ManagementConnection())
        self.assertNotEqual(same, unknown)
        self.assertIn("Wi-Fi", same)


class ManagementDevice(VersionDevice):
    def run(self, name, command, timeout, cancel_event=None):
        if name == "management_link_probe":
            self.calls.append((name, "read"))
            text = report(route="192.168.1.2 from 192.168.1.1 dev iface-home")
            text = text.replace("__MGLINK_END__", "iface-home|phy2|1|link||\n__MGLINK_END__")
            return RemoteCommandResult(name, 0, text)
        return super().run(name, command, timeout, cancel_event)


@unittest.skipUnless(LuaRuntime, "Test-only Lua runtime unavailable")
class ManagementPathVersionTests(unittest.TestCase):
    def test_all_supported_routes_reuse_capture_probe_without_writing(self):
        for profile in PROFILES:
            with self.subTest(series=profile["series"], route=profile["route"]):
                device = ManagementDevice(profile)
                before = copy.deepcopy(device.config)
                value = WirelessSetupManager(device).management_connection(
                    WirelessRequest("ap", "radio7", "Home", section="home"), threading.Event())
                self.assertEqual(value, ManagementConnection("wifi", "same_signal"))
                self.assertEqual(device.config, before)
                self.assertNotIn(("session", "create"), device.calls)
                self.assertNotIn(("uci", "apply"), device.calls)

    def test_down_interface_is_not_claimed_as_exact_signal(self):
        device = ManagementDevice(PROFILES[-1])
        device.native_link_up = False
        value = WirelessSetupManager(device).management_connection(
            WirelessRequest("ap", "radio7", "Home", section="home"), threading.Event())
        self.assertEqual(value.risk, "same_radio")
