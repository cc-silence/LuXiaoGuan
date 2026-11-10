"""Offline WPS persistence and real command-builder contracts."""
import shlex
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from modules.config_services.capture_settings_manager import CaptureSettingsManager
from modules.capture_services.openwrt.models import AccessPointInfo, OpenWrtConnectionState
from modules.capture_services.openwrt.wps_capture import OpenWrtWpsCapture
from modules.main_pages.packet_capture_page import PacketCapturePage

MAC = "02:11:22:33:44:55"
SOURCE = "02:AA:BB:CC:DD:EE"

class WpsConfigurationFlowTests(unittest.TestCase):
    def test_dialog_save_reload_and_cancel_preserve_address(self):
        from tests.test_standalone import APP
        from modules.main_pages.packet_capture.openwrt.wps_dialog import AdvancedWpsDialog
        with TemporaryDirectory() as folder:
            for split in (False, True):
                manager = CaptureSettingsManager(Path(folder) / "settings.json")
                if split:
                    manager._split = True
                    manager._primary_dir = Path(folder) / "primary"
                    manager._mirror_dir = Path(folder) / "mirror"
                    manager._written = {}
                data = manager.load()
                dialog = AdvancedWpsDialog(data["openwrt"]["wps_defaults"])
                try:
                    dialog.spoof_mac_enabled.setChecked(True)
                    dialog.spoof_mac.setText(SOURCE.lower())
                    dialog.mode.setCurrentIndex(dialog.mode.findData("quick"))
                    dialog.accept()
                    self.assertEqual(dialog.result(), 1)
                    data["openwrt"]["wps_defaults"] = dialog.get_settings()
                    manager.save(data)
                finally:
                    dialog.deleteLater()
                    APP.processEvents()
                reloaded = manager.load()["openwrt"]["wps_defaults"]
                self.assertTrue(reloaded["spoof_mac_enabled"])
                self.assertEqual(reloaded["spoof_mac"], SOURCE.lower())
                self.assertEqual(reloaded["mode"], "quick")
                reopened = AdvancedWpsDialog(reloaded)
                try:
                    self.assertEqual(reopened.get_settings(), reloaded)
                    reopened.spoof_mac.clear()
                    reopened.reject()
                    self.assertEqual(reopened.result(), 0)
                    self.assertEqual(manager.load()["openwrt"]["wps_defaults"], reloaded)
                finally:
                    reopened.deleteLater()
                    APP.processEvents()

    def test_invalid_batch_prompts_once_before_task_or_confirmation(self):
        page = Mock()
        page.capture_service.openwrt_snapshot = NS(
            connection_state=OpenWrtConnectionState.CONNECTED,
            access_points=tuple(NS(bssid=f"02:11:22:33:44:{n:02X}", selected=True)
                                for n in range(19)))
        page.openwrt_tab.connection_settings.return_value = {
            "wps_defaults": {"spoof_mac_enabled": True, "spoof_mac": "invalid"}}
        with patch("modules.main_pages.packet_capture_page.MessageDialog") as message, patch("modules.main_pages.packet_capture_page.ConfirmDialog") as confirm:
            PacketCapturePage._start_openwrt_wps(page, "second")
        message.assert_called_once()
        message.return_value.exec.assert_called_once()
        confirm.assert_not_called()
        page.capture_service.capture_openwrt_wps_async.assert_not_called()

    def run_actual_builder(self, folder, enabled, tail, downloads=None):
        manager = Mock()
        manager.is_running.return_value = False
        manager.read_log_tail.return_value = tail
        capture = OpenWrtWpsCapture(Mock(), manager)
        workspace = NS(local_dir=Path(folder), remote_dir="/tmp/owned")
        with patch("modules.capture_services.openwrt.wps_session.session_directory", return_value=Path(folder)/"state"), patch.object(capture, "_download_wpc", side_effect=downloads, return_value=""):
            result = capture.run_targets(
                workspace, "ownedmon", "owned-session",
                (AccessPointInfo(MAC, ssid="fixture", channel=1, wps_supported=True),),
                "offline", {"spoof_mac_enabled": enabled,
                           "spoof_mac": SOURCE.lower() if enabled else ""},
                folder, Mock())[0]
        return result, manager

    def test_valid_and_disabled_options_reach_reaver_with_correct_source(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled), TemporaryDirectory() as folder:
                result, manager = self.run_actual_builder(folder, enabled, "")
                manager.start.assert_called_once()
                args = shlex.split(manager.start.call_args.args[1])
                self.assertIn("reaver", args)
                self.assertEqual(args[args.index("-b")+1], MAC)
                self.assertEqual(args[args.index("-K")+1], "1")
                if enabled:
                    self.assertEqual(args[args.index("-m")+1], SOURCE)
                else:
                    self.assertNotIn("-m", args)
                self.assertIn("Reaver", result.note)

    def test_pin_only_reports_end_reason_without_recovered_secret(self):
        from modules.capture_services.backends.openwrt_backend import _emit_wps_message
        with TemporaryDirectory() as folder:
            result, _ = self.run_actual_builder(folder, True, "[+] WPS PIN: '12345670'")
            self.assertTrue(result.pin_ok)
            self.assertFalse(result.psk_ok)
            self.assertIn("Reaver", result.note)
            message = Mock()
            _emit_wps_message(message, result)
            self.assertNotIn("Reaver", message.call_args.args[0])
            self.assertIn("PIN", message.call_args.args[0])
            self.assertNotIn("12345670", message.call_args.args[0])

    def test_service_start_message_matches_mode_and_shared_options(self):
        from modules.capture_services.capture_service import CaptureService
        from modules.capture_services.openwrt.models import OpenWrtSessionSnapshot
        for mode in ("second", "offline", "normal"):
            service = Mock()
            service._shutting_down = False
            service._masked_target.return_value = "fixture"
            service.task_controller.busy = False
            service.openwrt_backend.snapshot = OpenWrtSessionSnapshot(
                connection_state=OpenWrtConnectionState.CONNECTED,
                access_points=(AccessPointInfo(MAC, ssid="fixture", channel=1),))
            settings = {"wps_defaults": {"spoof_mac_enabled": True, "spoof_mac": SOURCE}}
            self.assertTrue(CaptureService.capture_openwrt_wps_async(service, settings, (MAC,), mode))
            text = service.messageGenerated.emit.call_args.args[0]
            self.assertEqual("Pixie Dust" in text, mode == "offline")
            worker = service._start_openwrt_worker.call_args.args[1]
            worker(None, None, None)
            self.assertEqual(service.openwrt_backend.run_wps.call_args.args[1]["wps_defaults"], settings["wps_defaults"])

    def test_checkpoint_survives_final_download_failure(self):
        with TemporaryDirectory() as folder:
            result, _ = self.run_actual_builder(folder, False, "", ["saved.wpc", ""])
        self.assertEqual(result.wpc_file, "saved.wpc")

    def test_quick_settings_defaults_clamp_and_dialog_round_trip(self):
        from tests.test_standalone import APP
        from modules.main_pages.packet_capture.openwrt.quick_wps_dialog import QuickWpsDialog
        with TemporaryDirectory() as folder:
            manager = CaptureSettingsManager(Path(folder) / "settings.json")
            data = manager.load()
            self.assertEqual(data["openwrt"]["wps_defaults"]["quick_seconds"], 60)
            self.assertEqual(data["openwrt"]["wps_defaults"]["quick_rounds"], 1)
            data["openwrt"]["wps_defaults"]["quick_seconds"] = 9999
            data["openwrt"]["wps_defaults"]["quick_rounds"] = 0
            manager.save(data)
            saved = manager.load()["openwrt"]["wps_defaults"]
            self.assertEqual(saved["quick_seconds"], 3600)
            self.assertEqual(saved["quick_rounds"], 1)
            calls = []
            dialog = QuickWpsDialog(saved, None, clear_callback=lambda: calls.append(True))
            try:
                self.assertEqual(dialog.get_settings(),
                                 {"quick_seconds": 3600, "quick_rounds": 1})
                dialog.seconds.setValue(0)
                dialog.rounds.setValue(20)
                self.assertEqual(dialog.get_settings(),
                                 {"quick_seconds": 0, "quick_rounds": 20})
                dialog.clear_button.click()
                self.assertTrue(calls)
            finally:
                dialog.deleteLater()
                APP.processEvents()

    def test_remembered_pin_is_tried_before_generated_candidates(self):
        from modules.capture_services.openwrt.wps_defaults import DefaultPinCandidate
        manager = Mock(); manager.is_running.return_value = False
        manager.read_log_tail.return_value = "[+] Quitting after 1 crack attempts"
        capture = OpenWrtWpsCapture(Mock(), manager)
        candidates = (DefaultPinCandidate("11111111", "a"), DefaultPinCandidate("22222222", "b"))
        with TemporaryDirectory() as folder, patch(
                "modules.capture_services.openwrt.wps_quick.default_pin_candidates",
                return_value=candidates):
            capture.run_targets(
                NS(local_dir=Path(folder), remote_dir="/tmp/owned"), "mon", "s",
                (AccessPointInfo(MAC, channel=1, wps_supported=True),), "second",
                {"remembered_pin": "99999999"}, folder, Mock())
        first = shlex.split(manager.start.call_args_list[0].args[1])
        self.assertEqual(first[first.index("-p")+1], "99999999")
        self.assertEqual(first[first.index("-g")+1], "1")
        second = shlex.split(manager.start.call_args_list[1].args[1])
        self.assertEqual(second[second.index("-p")+1], "11111111")
        self.assertEqual(second[second.index("-g")+1], "1")

    def test_remembered_pin_is_verified_first_in_normal_mode(self):
        manager = Mock(); manager.is_running.return_value = False
        manager.read_log_tail.return_value = "WPS PIN: '99999999'\nWPA PSK: 'Secret'"
        capture = OpenWrtWpsCapture(Mock(), manager)
        with TemporaryDirectory() as folder:
            result = capture.run_targets(
                NS(local_dir=Path(folder), remote_dir="/tmp/owned"), "mon", "s",
                (AccessPointInfo(MAC, channel=1, wps_supported=True),), "normal",
                {"remembered_pin": "99999999", "attack_seconds": 30}, folder, Mock())[0]
        self.assertTrue(result.psk_ok)
        self.assertEqual(manager.start.call_count, 1)
        first = shlex.split(manager.start.call_args.args[1])
        self.assertEqual(first[first.index("-p")+1], "99999999")
        self.assertNotIn("-K", first)

    def test_pin_only_result_is_saved_and_callback_carries_pin(self):
        from modules.capture_services.backends.openwrt_backend import _remember_wps_pin
        from modules.capture_services.openwrt.wps_models import WpsTargetResult
        result = WpsTargetResult(MAC, "fixture", mode="second", pin_ok=True, pin="12345670")
        messages, pins = [], []
        with patch("modules.capture_services.openwrt.wps_remembered_pin.write_pin") as write:
            saved = _remember_wps_pin(
                result, lambda text, level: messages.append((text, level)), pins.append)
        write.assert_called_once_with(MAC, "12345670")
        self.assertTrue(saved.pin_saved)
        self.assertEqual(pins[0]["pin"], "12345670")
        self.assertTrue(pins[0]["saved"])
        self.assertNotIn("12345670", repr(messages))

    def test_credential_failure_still_hands_pin_to_user(self):
        from modules.capture_services.backends.openwrt_backend import _remember_wps_pin
        from modules.capture_services.openwrt.wps_models import WpsTargetResult
        result = WpsTargetResult(MAC, "fixture", mode="second", pin_ok=True, pin="12345670")
        messages, pins = [], []
        with patch("modules.capture_services.openwrt.wps_remembered_pin.write_pin",
                   side_effect=RuntimeError("locked")):
            saved = _remember_wps_pin(
                result, lambda text, level: messages.append((text, level)), pins.append)
        self.assertFalse(saved.pin_saved)
        self.assertEqual(pins[0]["pin"], "12345670")
        self.assertFalse(pins[0]["saved"])
        self.assertTrue(any("保存失败" in text for text, _level in messages))

    def test_password_success_does_not_store_a_pin(self):
        from modules.capture_services.backends.openwrt_backend import _remember_wps_pin
        from modules.capture_services.openwrt.wps_models import WpsTargetResult
        result = WpsTargetResult(MAC, "fixture", mode="second",
                                 pin_ok=True, psk_ok=True, pin="12345670")
        pins = []
        with patch("modules.capture_services.openwrt.wps_remembered_pin.write_pin") as write:
            saved = _remember_wps_pin(result, None, pins.append)
        write.assert_not_called()
        self.assertFalse(saved.pin_saved)
        self.assertEqual(pins, [])

    def test_remembered_pin_credential_target_is_keyed_by_bssid(self):
        from modules.capture_services.openwrt import wps_remembered_pin as remembered
        with patch.object(remembered, "read_generic_password", return_value="12345670") as read:
            self.assertEqual(remembered.read_pin("54:a0:50:75:d2:40"), "12345670")
        read.assert_called_once_with("RouterManager WPS PIN|54:A0:50:75:D2:40")
        with self.assertRaises(ValueError):
            remembered.write_pin(MAC, "1234")
