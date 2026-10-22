"""Direct save has one apply and preserves an existing bridge after failure."""
import copy
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QWidget
from tests.test_standalone import APP
from tests.test_wireless_ui import environment
from tests.test_wireless_bridges import RuntimeDevice
from tests.test_wireless_setup import LuaRuntime, TARGET
from modules.capture_services.openwrt.signal_manager import SignalError
from modules.capture_services.openwrt.wireless_setup_models import WirelessState, WirelessRequest, WirelessTaskResult, StoredWirelessPassword
from modules.capture_services.openwrt.wireless_setup_manager import WirelessSetupManager
from modules.main_pages.packet_capture.openwrt.signal_setup_actions import SignalSetupActions


class Setup(QObject):
    completed = Signal(object)
    snapshotChanged = Signal(object)

    def __init__(self):
        super().__init__()
        self.snapshot = WirelessState(environment=environment())
        self.identity = object()
        self._connection_identity = Mock(side_effect=lambda: self.identity)
        self.verify = Mock(side_effect=AssertionError("Preverification must not run"))
        self.save = Mock(return_value=True)
        self.read_password = Mock(return_value=True)
        self.cancel = Mock()


class DirectUiTests(unittest.TestCase):
    def actions(self):
        page = QWidget()
        dialogs = []
        panels = SimpleNamespace(page=page, _show=dialogs.append, empty_hint=Mock())
        service = Setup()
        actions = SignalSetupActions(panels, service)
        self.addCleanup(page.deleteLater)
        self.addCleanup(actions.dispose)
        return actions, service, dialogs

    def test_join_opens_settings_without_remote_trial_and_save_directly_applies(self):
        actions, service, dialogs = self.actions()
        actions.join(TARGET)
        self.assertEqual(len(dialogs), 1)
        dialog = dialogs[0]
        self.addCleanup(dialog.deleteLater)
        service.verify.assert_not_called()
        service.save.assert_not_called()
        dialog.password.edit.setText("test-password")
        with patch.object(actions, "_confirm", return_value=True):
            dialog.save_button.click()
        service.verify.assert_not_called()
        service.save.assert_called_once()
        request = service.save.call_args.args[0]
        self.assertEqual((request.radio, request.ssid, request.password), (TARGET.radio, TARGET.ssid, "test-password"))
        self.assertEqual(request.verification, "")
        service.completed.emit(WirelessTaskResult("save", {}))

    def test_real_action_binding_reveals_stored_password_without_rewriting(self):
        actions, service, dialogs = self.actions()
        env = service.snapshot.environment
        env.data["rows"][0]["has_key"] = True
        row = SimpleNamespace(section="home", running="active")
        actions.relay(TARGET, row=row)
        dialog = dialogs[0]
        self.addCleanup(dialog.deleteLater)
        dialog.password.toggle.click()
        service.read_password.assert_called_once_with("home", env.data["revision"])
        secret = StoredWirelessPassword("stored-test-password")
        service.completed.emit(WirelessTaskResult("password", secret))
        self.assertEqual(dialog.password.edit.text(), "stored-test-password")
        self.assertEqual(dialog.password.submitted_text(), "")
        secret.clear()
        dialog.reject()
        self.assertEqual(dialog.password.edit.text(), "")


    def test_save_refuses_router_changed_while_editor_is_open(self):
        actions, service, dialogs = self.actions()
        first = object()
        service._connection_identity = Mock(return_value=first)
        actions.join(TARGET)
        dialog = dialogs[0]
        self.addCleanup(dialog.deleteLater)
        dialog.password.edit.setText("test-password")
        service._connection_identity.return_value = object()
        with patch.object(actions, "_confirm", return_value=True):
            dialog.save_button.click()
        service.save.assert_not_called()
        self.assertFalse(dialog._busy)

    def test_save_refuses_router_changed_during_confirmation(self):
        actions, service, dialogs = self.actions()
        service._connection_identity = Mock(return_value=object())
        actions.join(TARGET)
        dialog = dialogs[0]
        self.addCleanup(dialog.deleteLater)
        dialog.password.edit.setText("test-password")
        def switch(*_):
            service._connection_identity.return_value = object()
            return True
        with patch.object(actions, "_confirm", side_effect=switch):
            dialog.save_button.click()
        service.save.assert_not_called()

@unittest.skipUnless(LuaRuntime, "Test-only Lua runtime unavailable")
class DirectRecoveryTests(unittest.TestCase):
    def test_new_connection_failure_restores_existing_bridge_and_all_configs(self):
        device = RuntimeDevice()
        manager = WirelessSetupManager(device)
        event = threading.Event()
        original_request = WirelessRequest("sta", "radio7", TARGET.ssid, "original-uplink-password",
            options={"encryption": "psk2", "bssid": TARGET.bssid, "proto": "dhcp", "zone": "wan"})
        existing = manager.save(original_request, event, Mock())
        before = copy.deepcopy(device.config)
        device.calls.clear()
        request = WirelessRequest("sta", "radio7", "Another-uplink", "wrong-password",
            options={"encryption": "psk2", "bssid": TARGET.bssid, "proto": "dhcp", "zone": "wan"})
        with patch.object(manager, "_wait_ready", side_effect=SignalError("authentication", "Connection failed")):
            with self.assertRaises(SignalError):
                manager.save(request, event, Mock())
        self.assertEqual(device.config, before)
        self.assertEqual(device.config["wireless"][existing["section"]]["key"], "original-uplink-password")
        self.assertEqual(device.calls.count(("uci", "apply")), 1)
        self.assertEqual(device.calls.count(("uci", "rollback")), 1)
        self.assertNotIn(("uci", "confirm"), device.calls)
        self.assertFalse(device.sessions)

    def test_restore_does_not_report_success_when_original_station_is_not_connected(self):
        manager = WirelessSetupManager(None)
        env = SimpleNamespace(data={"revision": "a"*192, "dhcp_revision": "b"*64,
            "rows": [{"section": "upper", "device": "radio0", "mode": "sta"}],
            "active": ["upper"], "radios": [{"name": "radio0", "runtime_up": True}]})
        def call(action, *_args, **_kwargs):
            if action == "revision": return {"revision": "a"*192, "dhcp_revision": "b"*64}
            if action == "runtime": return {"active": ["upper"], "pending": False, "up": True}
            if action == "status": return {"ready": False}
            return {}
        manager._call = Mock(side_effect=call)
        clock = iter((0, 0, 0, 30))
        with patch("modules.capture_services.openwrt.wireless_setup_manager.time.monotonic", side_effect=lambda: next(clock)), patch(
                "modules.capture_services.openwrt.wireless_setup_manager.threading.Event", return_value=SimpleNamespace(wait=Mock())):
            with self.assertRaises(SignalError) as caught:
                manager._restore("sid", env, "radio0")
        self.assertEqual(caught.exception.code, "recovery")
        self.assertTrue(any(call.args[0] == "status" for call in manager._call.call_args_list))
