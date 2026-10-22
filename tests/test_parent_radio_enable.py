"""Native transactions and UI confirmation for enabling a disabled parent radio."""
import copy
import threading
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch
from tests import test_wireless_setup as native
from tests import test_wireless_bridges as bridges
from tests import test_signal_actions_ui as ui
from modules.capture_services.openwrt.signal_manager import RouterSignalManager, SignalError
from modules.capture_services.openwrt.signal_models import SignalInventory
from modules.capture_services.openwrt.wireless_setup_manager import WirelessSetupManager
from modules.capture_services.openwrt.wireless_setup_models import WirelessRequest
from modules.capture_services.router_signal_service import RouterSignalService


class ParentDevice(bridges.RuntimeDevice):
    def call(self, obj, method, arguments):
        if obj == "network.wireless" and self.config["wireless"]["radio7"].get("disabled") == "1":
            self.calls.append((obj, method))
            return self.wrap({"radio7": {"up": False, "pending": False, "interfaces": []}}), None
        return super().call(obj, method, arguments)


@unittest.skipUnless(native.LuaRuntime, "Lua runtime unavailable")
class ParentTransactionTests(unittest.TestCase):
    def setUp(self):
        self.device = ParentDevice()
        self.manager = WirelessSetupManager(self.device)
        self.cancel = threading.Event()

    def disable_parent(self, child_disabled=True):
        self.device.config["wireless"]["radio7"]["disabled"] = "1"
        self.device.config["wireless"]["home"]["disabled"] = "1" if child_disabled else "0"
        self.device.config["wireless"]["other"] = dict(self.device.config["wireless"]["home"], ssid="Other", disabled="1")

    def enable(self, section="home", **kwargs):
        return self.manager.toggle(section, True, self.cancel, enable_parent=True,
                                   expected_revision=self.device.revision()[:64], **kwargs)

    def test_ap_both_disabled_and_parent_only_disabled(self):
        for disabled in (True, False):
            with self.subTest(child_disabled=disabled):
                self.setUp()
                self.disable_parent(disabled)
                original = copy.deepcopy(self.device.config)
                self.enable()
                expected = copy.deepcopy(original)
                expected["wireless"]["radio7"]["disabled"] = "0"
                expected["wireless"]["home"]["disabled"] = "0"
                self.assertEqual(self.device.config, expected)
                self.assertIn(("uci", "confirm"), self.device.calls)
                self.assertFalse(self.device.sessions)

    def test_external_sta_enables_without_rewriting_credentials_or_network(self):
        self.disable_parent()
        self.device.config["wireless"]["home"].update(mode="sta", network="wan")
        original = copy.deepcopy(self.device.config)
        self.enable()
        expected = copy.deepcopy(original)
        expected["wireless"]["radio7"]["disabled"] = "0"
        expected["wireless"]["home"]["disabled"] = "0"
        self.assertEqual(self.device.config, expected)

    def test_wds_and_relayd_restore_bridge_protection_when_parent_is_disabled(self):
        for mode in ("wds", "relayd"):
            with self.subTest(mode=mode):
                self.setUp()
                self.device.config["dhcp"] = {"lan": {".type": "dhcp", "interface": "lan", "ignore": "0", "ra": "server", "dhcpv6": "server"}}
                self.device.config["firewall"]["zone_lan"].update(input="ACCEPT", output="ACCEPT", forward="ACCEPT")
                request = WirelessRequest("sta", "radio7", native.TARGET.ssid, "test-password", options={
                    "encryption": "psk2", "bridge_mode": mode, "network": "lan", "proto": "dhcp",
                    "zone": "lan" if mode == "relayd" else "wan"})
                value = self.manager.save(request, self.cancel, lambda *_: None)
                section = value["section"]
                self.manager.toggle(section, False, self.cancel)
                self.device.config["wireless"]["radio7"]["disabled"] = "1"
                original = copy.deepcopy(self.device.config)
                with patch.object(self.manager, "_wait_ready", side_effect=InterruptedError("cancelled")):
                    with self.assertRaises(InterruptedError):
                        self.enable(section)
                self.assertEqual(self.device.config, original)
                self.enable(section)
                self.device.config["wireless"]["radio7"]["disabled"] = "1"
                original = copy.deepcopy(self.device.config)
                self.enable(section)
                original["wireless"]["radio7"]["disabled"] = "0"
                self.assertEqual(self.device.config, original)
                self.assertEqual(self.device.config["wireless"]["radio7"]["disabled"], "0")
                self.assertEqual(self.device.config["wireless"][section]["disabled"], "0")
                self.assertEqual(self.device.config["dhcp"]["lan"]["ignore"], "1")
                self.assertNotIn("rm_suspended", self.device.config["wireless"][section])

    def test_failure_or_cancel_rolls_back_parent_and_child(self):
        for error in (InterruptedError("cancelled"), SignalError("authentication", "not ready")):
            self.setUp()
            self.disable_parent()
            original = copy.deepcopy(self.device.config)
            with patch.object(self.manager, "_wait_ready", side_effect=error):
                with self.assertRaises(type(error)):
                    self.enable()
            self.assertEqual(self.device.config, original)
            self.assertIn(("uci", "rollback"), self.device.calls)
            self.assertFalse(self.device.sessions)

    def test_saved_parent_state_is_verified_before_confirmation(self):
        self.disable_parent()
        original = copy.deepcopy(self.device.config)
        def lose_parent(*args, **kwargs):
            self.device.config["wireless"]["radio7"]["disabled"] = "1"
        with patch.object(self.manager, "_wait_ready", side_effect=lose_parent):
            with self.assertRaises(SignalError):
                self.enable()
        self.assertEqual(self.device.config, original)
        self.assertNotIn(("uci", "confirm"), self.device.calls)

    def test_stale_revision_and_missing_parent_consent_do_not_apply(self):
        self.disable_parent()
        original = copy.deepcopy(self.device.config)
        with self.assertRaises(SignalError):
            self.manager.toggle("home", True, self.cancel, enable_parent=True, expected_revision="f"*64)
        with self.assertRaises(SignalError):
            self.manager.toggle("home", True, self.cancel)
        self.assertEqual(self.device.config, original)
        self.assertNotIn(("uci", "apply"), self.device.calls)

    def test_signal_manager_routes_parent_only_enable_through_rollback_transaction(self):
        row = replace(ui.Service().snapshot.inventory.rows[0], section="home", radio="radio7", disabled=False, radio_disabled=True, running="inactive")
        inventory = SignalInventory((row,), self.device.revision()[:64])
        self.device.config["wireless"]["radio7"]["disabled"] = "1"
        inventory = replace(inventory, revision=self.device.revision()[:64])
        manager = RouterSignalManager(self.device)
        with patch.object(manager, "read", return_value="updated"):
            self.assertEqual(manager.change(inventory, "home", True, self.cancel), "updated")
        self.assertEqual(self.device.config["wireless"]["radio7"]["disabled"], "0")


class ParentButtonTests(unittest.TestCase):
    def test_click_confirms_parent_enable_and_cancel_does_nothing(self):
        from modules.common_widgets.common_dialogs import ConfirmDialog
        for light in (False, True):
            for child_disabled in (False, True):
                fixture = ui.SignalActionTests()
                page, service = fixture.create_page(light)
                # This isolated page has no main-window surface behind its transparent root.
                from PySide6.QtGui import QColor, QPalette
                palette = page.palette()
                palette.setColor(QPalette.Window, QColor("#f5f6fa" if light else "#181b1f"))
                page.setPalette(palette)
                page.setAutoFillBackground(True)
                self.addCleanup(fixture.doCleanups)
                row = replace(service.snapshot.inventory.rows[0], disabled=child_disabled, radio_disabled=True, running="inactive")
                service.snapshot = replace(service.snapshot, inventory=SignalInventory((row,), "a"*64))
                service.change = Mock(return_value=True)
                page.apply_snapshot(service.snapshot)
                widget = page._row_widgets[row.section]
                self.assertTrue(widget.button.isEnabled())
                for accepted in (False, True):
                    with patch("modules.main_pages.packet_capture.openwrt.router_signal_page.ConfirmDialog") as dialog:
                        dialog.Accepted = ConfirmDialog.Accepted
                        dialog.return_value.exec.return_value = ConfirmDialog.Accepted if accepted else ConfirmDialog.Rejected
                        widget.button.click()
                        message = dialog.call_args.args[1]
                        self.assertIn(row.radio, message)
                        self.assertIn("\u7236\u7f51\u5361", message)
                        self.assertIn("\u5176\u4ed6", message)
                    if accepted: service.change.assert_called_once_with(row.section, True, confirmed=True)
                    else: service.change.assert_not_called()
                output = native.Path(__file__).parent / "signal-actions-preview" / ("parent-enable-light.png" if light else "parent-enable-dark.png")
                page.grab().save(str(output))

    def test_service_accepts_parent_only_enable_but_rejects_unconfirmed_or_stale(self):
        row = replace(ui.Service().snapshot.inventory.rows[0], disabled=False, radio_disabled=True)
        from types import SimpleNamespace
        service = SimpleNamespace(snapshot=replace(ui.Service().snapshot, inventory=SignalInventory((row,), "a"*64)), _start=Mock(return_value=True))
        self.assertFalse(RouterSignalService.change(service, row.section, True))
        self.assertTrue(RouterSignalService.change(service, row.section, True, confirmed=True))
        service.snapshot = replace(service.snapshot, stale=True)
        self.assertFalse(RouterSignalService.change(service, row.section, True, confirmed=True))
