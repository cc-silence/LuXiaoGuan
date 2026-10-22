"""Password reveal is explicit, revision-bound, ephemeral and does not rewrite keys."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import Mock
from tests.test_wireless_setup import LuaDevice, LuaRuntime
from tests.test_standalone import APP
from tests.test_wireless_ui import environment
from PySide6.QtWidgets import QWidget, QLineEdit
from modules.ui_scale_manager import UiScaleManager
from modules.capture_services.openwrt.wireless_setup_manager import WirelessSetupManager
from modules.capture_services.openwrt.signal_manager import SignalError
from modules.capture_services.openwrt.wireless_setup_models import StoredWirelessPassword, WirelessTaskResult, WirelessState, WirelessNetwork
from modules.capture_services.wireless_setup_service import WirelessSetupService
from modules.main_pages.packet_capture.openwrt.signal_setup_dialogs import RelaySetupDialog
from modules.main_pages.packet_capture.openwrt.signal_setup_editor import configure_relay, relay_request
from modules.capture_services.openwrt.wireless_identity_profiles import PROFILES


@unittest.skipUnless(LuaRuntime, "Test-only Lua runtime unavailable")
class PasswordReaderTests(unittest.TestCase):
    def test_explicit_read_keeps_secret_out_of_inventory_and_repr(self):
        device = LuaDevice()
        manager = WirelessSetupManager(SimpleNamespace(run=device.run))
        cancel = threading.Event()
        env = manager.inspect(cancel)
        self.assertNotIn("original-password", repr(env))
        result = manager.read_password("home", env.data["revision"], cancel)
        self.assertEqual(result.password, "original-password")
        self.assertNotIn("original-password", repr(WirelessTaskResult("password", result)))
        self.assertNotIn(("uci", "apply"), device.calls)
        result.clear()
        self.assertEqual(result.password, "")

    def test_password_read_refuses_changed_revision_and_missing_section(self):
        device = LuaDevice()
        manager = WirelessSetupManager(SimpleNamespace(run=device.run))
        env = manager.inspect(threading.Event())
        for section, revision in (("missing", env.data["revision"]), ("home", "f"*192)):
            with self.assertRaises(SignalError):
                manager.read_password(section, revision, threading.Event())
        self.assertNotIn(("uci", "apply"), device.calls)

    def test_result_is_cleared_after_delivery_and_never_added_to_snapshot(self):
        task = SimpleNamespace(busy=False, begin=Mock(return_value=threading.Event()), finish=Mock())
        signals = SimpleNamespace(snapshot=SimpleNamespace(connected=True), _forward_worker_message=Mock())
        owner = SimpleNamespace(_shutting_down=False, task_controller=task, _start_openwrt_worker=Mock())
        identity = object()
        delivered = []
        state = SimpleNamespace(active=False, owner=owner, signals=signals,
            snapshot=WirelessState(), _connection_identity=lambda: identity,
            snapshotChanged=SimpleNamespace(emit=Mock()),
            completed=SimpleNamespace(emit=lambda result: delivered.append(result.value.password)))
        secret = StoredWirelessPassword("test-secret")
        WirelessSetupService._start(state, "password", lambda *_: secret)
        worker = owner._start_openwrt_worker.call_args.args[1]
        finished = owner._start_openwrt_worker.call_args.args[3]
        result = worker(threading.Event())
        finished(result)
        self.assertEqual(delivered, ["test-secret"])
        self.assertEqual(secret.password, "")
        self.assertNotIn("test-secret", repr(state.snapshot))


class PasswordAndIdentityUiTests(unittest.TestCase):
    def dialog(self, light=False, editing=True):
        parent = QWidget()
        parent.scale_manager = UiScaleManager(100)
        parent.setProperty("settingsLightTheme", light)
        parent.setStyleSheet("QWidget { background: #f5f6fa; }" if light else "QWidget { background: #181b1f; }")
        row = SimpleNamespace(section="upper", running="active") if editing else None
        dialog = RelaySetupDialog("Upstream", parent, radio_name="radio7", row=row)
        env = environment()
        if editing:
            env.data["rows"].append(dict(section="upper", device="radio7", mode="sta", ssid="Upstream",
                network="wan", encryption="psk2", has_key=True, macaddr="02:11:22:33:44:55"))
        configure_relay(dialog, env)
        self.addCleanup(parent.deleteLater)
        self.addCleanup(dialog.reject)
        return dialog

    def test_eye_requests_saved_password_only_when_needed_and_unchanged_value_is_not_submitted(self):
        dialog = self.dialog()
        field = dialog.password
        request = Mock()
        field.storedPasswordRequested.connect(request)
        field.toggle.click()
        request.assert_called_once()
        self.assertEqual(field.edit.text(), "")
        field.show_stored_password("test-stored-password")
        self.assertEqual(field.edit.echoMode(), QLineEdit.Normal)
        self.assertEqual(field.submitted_text(), "")
        target = WirelessNetwork("radio7", "", "Upstream", 36, "5G", -40, "psk2")
        dialog.lock_bssid.setCurrentIndex(1)
        self.assertEqual(relay_request(dialog, target).password, "")
        field.toggle.click()
        self.assertEqual(field.edit.echoMode(), QLineEdit.Password)
        field.edit.setText("new-password")
        self.assertEqual(field.submitted_text(), "new-password")
        dialog.reject()
        self.assertEqual(field.edit.text(), "")
        self.assertIsNone(field._stored_password)

    def test_new_bridge_randomizes_profile_name_mac_and_hostname_together(self):
        dialog = self.dialog(editing=False)
        fields = dialog.identity_fields
        self.assertEqual(fields.mode.currentData(), "profile")
        first = fields.profile.currentIndex()
        mac = fields.mac.text()
        fields.regenerate.click()
        self.assertNotEqual(fields.profile.currentIndex(), first)
        self.assertNotEqual(fields.mac.text(), mac)
        label, hostname, prefix = PROFILES[fields.profile.currentData()]
        self.assertEqual(fields.hostname.text(), hostname)
        self.assertTrue(fields.mac.text().startswith(prefix))
        saved = fields.values()
        fields.refresh()
        self.assertEqual(fields.values(), saved)
        dialog.bridge_mode.setCurrentIndex(dialog.bridge_mode.findData("wds"))
        self.assertNotIn("hostname", fields.values())

    def test_disabled_dropdowns_are_visibly_different_in_both_themes(self):
        output = Path("build/password-identity-preview")
        output.mkdir(exist_ok=True)
        for light in (False, True):
            dialog = self.dialog(light)
            dialog.other_section.set_expanded(True, animate=False)
            dialog.other_tabs.stack.setCurrentIndex(1)
            dialog.show()
            for _ in range(3): APP.processEvents()
            disabled = dialog.bridge_network.grab().toImage().pixelColor(8, 8)
            self.assertFalse(dialog.bridge_network.isEnabled())
            dialog.grab().save(str(output / ("network-light.png" if light else "network-dark.png")))
            dialog.bridge_mode.setCurrentIndex(dialog.bridge_mode.findData("relayd"))
            APP.processEvents()
            enabled = dialog.bridge_network.grab().toImage().pixelColor(8, 8)
            self.assertTrue(dialog.bridge_network.isEnabled())
            self.assertNotEqual(disabled, enabled)
            dialog.other_tabs.stack.setCurrentIndex(3)
            APP.processEvents()
            self.assertFalse(dialog.identity_fields.profile.isEnabled())
            dialog.grab().save(str(output / ("identity-light.png" if light else "identity-dark.png")))
            dialog.reject()
