"""Automatic source-address preparation without device or network access."""
import shlex
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from modules.capture_services.openwrt.wps_options import (
    random_source_mac, prepare_wps_options, spoof_mac_for)
from modules.capture_services.openwrt.wps_capture import OpenWrtWpsCapture
from modules.capture_services.openwrt.models import AccessPointInfo, OpenWrtSessionSnapshot, OpenWrtConnectionState
from modules.capture_services.capture_service import CaptureService

SOURCE = "02:AA:BB:CC:DD:EE"
MAC = "02:11:22:33:44:55"
GENERATOR = "modules.capture_services.openwrt.wps_options.random_source_mac"

class WpsRandomMacTests(unittest.TestCase):
    def test_random_bits_match_locally_administered_unicast_for_all_first_bytes(self):
        for first in range(256):
            with patch("modules.capture_services.openwrt.wps_options.token_bytes", return_value=bytes([first, 1, 2, 3, 4, 5])) as entropy:
                address = random_source_mac()
            octets = bytes.fromhex(address.replace(":", ""))
            self.assertEqual(octets[0] & 3, 2)
            self.assertEqual(octets[1:], bytes([1, 2, 3, 4, 5]))
            self.assertEqual(spoof_mac_for({"spoof_mac_enabled": True, "spoof_mac": address}), address)
            entropy.assert_called_once_with(6)

    def test_preparation_is_idempotent_and_does_not_mutate_saved_settings(self):
        for blank in ("", "  ", None):
            original = {"spoof_mac_enabled": True, "spoof_mac": blank}
            with patch(GENERATOR, return_value=SOURCE) as generator:
                prepared = prepare_wps_options(original)
                self.assertEqual(prepare_wps_options(prepared), prepared)
                generator.assert_called_once()
            self.assertEqual(prepared["spoof_mac"], SOURCE)
            self.assertEqual(original["spoof_mac"], blank)

    def test_disabled_and_explicit_addresses_do_not_generate(self):
        with patch(GENERATOR) as generator:
            for address in ("", "invalid", SOURCE):
                options = {"spoof_mac_enabled": False, "spoof_mac": address}
                self.assertEqual(prepare_wps_options(options), options)
            self.assertEqual(prepare_wps_options({"spoof_mac_enabled": True, "spoof_mac": SOURCE.lower()})["spoof_mac"], SOURCE)
            with self.assertRaises(ValueError):
                prepare_wps_options({"spoof_mac_enabled": True, "spoof_mac": "invalid"})
            generator.assert_not_called()

    def test_dialog_generates_on_enable_preserves_manual_and_fills_cleared_on_accept(self):
        from tests.test_standalone import APP
        from modules.main_pages.packet_capture.openwrt.wps_dialog import AdvancedWpsDialog
        with patch(GENERATOR, return_value=SOURCE) as generator:
            dialog = AdvancedWpsDialog({})
            try:
                generator.assert_not_called()
                self.assertEqual(dialog.spoof_mac.text(), "")
                dialog.spoof_mac_enabled.setChecked(True)
                self.assertEqual(dialog.spoof_mac.text(), SOURCE)
                dialog.spoof_mac_enabled.setChecked(False)
                dialog.spoof_mac_enabled.setChecked(True)
                generator.assert_called_once()
                manual = "02:11:22:33:44:66"
                dialog.spoof_mac.setText(manual)
                dialog.spoof_mac_enabled.setChecked(False)
                dialog.spoof_mac_enabled.setChecked(True)
                self.assertEqual(dialog.spoof_mac.text(), manual)
                dialog.spoof_mac.clear()
                dialog.accept()
                self.assertEqual(dialog.result(), 1)
                self.assertEqual(dialog.get_settings()["spoof_mac"], SOURCE)
                self.assertEqual(generator.call_count, 2)
            finally:
                dialog.deleteLater(); APP.processEvents()

    def test_old_enabled_empty_dialog_is_filled_without_mutating_settings(self):
        from tests.test_standalone import APP
        from modules.main_pages.packet_capture.openwrt.wps_dialog import AdvancedWpsDialog
        options = {"spoof_mac_enabled": True, "spoof_mac": ""}
        with patch(GENERATOR, return_value=SOURCE) as generator:
            dialog = AdvancedWpsDialog(options)
            try:
                self.assertEqual(dialog.spoof_mac.text(), SOURCE)
                self.assertEqual(options["spoof_mac"], "")
                dialog.reject()
                generator.assert_called_once()
            finally:
                dialog.deleteLater(); APP.processEvents()

    def test_service_resolves_once_before_dispatching_shared_options(self):
        service = Mock()
        service._shutting_down = False
        service._masked_target.return_value = "fixture"
        service.task_controller.busy = False
        service.openwrt_backend.snapshot = OpenWrtSessionSnapshot(
            connection_state=OpenWrtConnectionState.CONNECTED,
            access_points=(AccessPointInfo(MAC),))
        original = {"wps_defaults": {"spoof_mac_enabled": True, "spoof_mac": ""}}
        with patch(GENERATOR, return_value=SOURCE) as generator:
            self.assertTrue(CaptureService.capture_openwrt_wps_async(service, original, (MAC,)))
            worker = service._start_openwrt_worker.call_args.args[1]
            worker(None, None, None)
            self.assertEqual(service.openwrt_backend.run_wps.call_args.args[1]["wps_defaults"]["spoof_mac"], SOURCE)
            generator.assert_called_once()
        self.assertEqual(original["wps_defaults"]["spoof_mac"], "")

    def test_executor_uses_one_generated_source_for_entire_batch(self):
        with TemporaryDirectory() as folder:
            manager = Mock(); manager.is_running.return_value = False
            manager.read_log_tail.return_value = ""
            capture = OpenWrtWpsCapture(Mock(), manager)
            targets = tuple(AccessPointInfo(f"02:11:22:33:44:{n:02X}", channel=1, wps_supported=True) for n in range(19))
            with patch(GENERATOR, return_value=SOURCE) as generator, patch.object(capture, "_download_wpc", return_value=""), patch("modules.capture_services.openwrt.wps_session.session_directory", return_value=Path(folder)/"state"):
                results = capture.run_targets(NS(local_dir=Path(folder), remote_dir="/tmp/owned"), "ownedmon", "owned", targets, "second", {"spoof_mac_enabled": True, "spoof_mac": "", "partial_pin": "12345670"}, folder, Mock())
            generator.assert_called_once()
            self.assertEqual(len(results), 19)
            self.assertEqual(manager.start.call_count, 38)
            for call in manager.start.call_args_list:
                args = shlex.split(call.args[1])
                self.assertEqual(args[args.index("-m")+1], SOURCE)
                if "-K" in args:
                    self.assertNotIn("-p", args)
                else:
                    self.assertEqual(args[args.index("-p")+1], "12345670")

    def test_page_passes_generated_address_to_preflight_for_old_empty_settings(self):
        from modules.main_pages.packet_capture_page import PacketCapturePage
        page = Mock()
        page.capture_service.openwrt_snapshot = NS(
            connection_state=OpenWrtConnectionState.CONNECTED,
            access_points=(NS(bssid=MAC, ssid="fixture", selected=True),))
        original = {"wps_defaults": {"spoof_mac_enabled": True, "spoof_mac": ""}}
        page.openwrt_tab.connection_settings.return_value = original
        with patch(GENERATOR, return_value=SOURCE) as generator, patch("modules.main_pages.packet_capture_page.MessageDialog") as message, patch("modules.main_pages.packet_capture_page.ConfirmDialog") as confirm, patch("modules.main_pages.packet_capture_page.CaptureSettingsManager.normalized_output_dir", return_value=Path("unused")):
            confirm.return_value.exec.return_value = confirm.Accepted
            PacketCapturePage._start_openwrt_wps(page, "second")
        generator.assert_called_once()
        message.assert_not_called()
        page.capture_service.capture_openwrt_wps_async.assert_not_called()
        page._wps_start_controller.start.assert_called_once()
        self.assertEqual(page._wps_start_controller.start.call_args.args[0]["wps_defaults"]["spoof_mac"], SOURCE)
        self.assertEqual(original["wps_defaults"]["spoof_mac"], "")

    def test_generated_dialog_address_is_saved_and_reopened_without_regeneration(self):
        from tests.test_standalone import APP
        from modules.config_services.capture_settings_manager import CaptureSettingsManager
        from modules.main_pages.packet_capture.openwrt.wps_dialog import AdvancedWpsDialog
        with TemporaryDirectory() as folder, patch(GENERATOR, return_value=SOURCE) as generator:
            manager = CaptureSettingsManager(Path(folder)/"settings.json")
            data = manager.load()
            dialog = AdvancedWpsDialog(data["openwrt"]["wps_defaults"])
            try:
                dialog.spoof_mac_enabled.setChecked(True)
                dialog.accept()
                data["openwrt"]["wps_defaults"] = dialog.get_settings()
                manager.save(data)
            finally:
                dialog.deleteLater(); APP.processEvents()
            reopened = AdvancedWpsDialog(manager.load()["openwrt"]["wps_defaults"])
            try:
                self.assertEqual(reopened.spoof_mac.text(), SOURCE)
                generator.assert_called_once()
            finally:
                reopened.deleteLater(); APP.processEvents()
