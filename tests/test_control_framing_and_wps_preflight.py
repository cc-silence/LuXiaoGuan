"""Redraw framing and WPS preflight regression without live wireless traffic."""
import unittest
from threading import Event
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from modules.capture_services.openwrt.password_control import PasswordControl, _reply_line
from modules.capture_services.openwrt.wps_options import spoof_mac_for
from modules.capture_services.backends.openwrt_backend import OpenWrtCaptureBackend
from modules.capture_services.capture_service import CaptureService
from modules.capture_services.openwrt.models import AccessPointInfo, OpenWrtSessionSnapshot, OpenWrtConnectionState
from tests.test_password_control import Protocol, BSSID

class ControlFramingTests(unittest.TestCase):
    def test_redraw_prefix_is_removed_only_for_exact_protocol_payload(self):
        for prefix in ("p","pi","pin","ping","d","discon","disconnect"):
            self.assertEqual(_reply_line(prefix+"PONG","disconnect"),"PONG")
        self.assertEqual(_reply_line("disconOK","disconnect"),"OK")
        self.assertEqual(_reply_line("statuswpa_state=COMPLETED","status"),"wpa_state=COMPLETED")
        self.assertEqual(_reply_line("staid=3","status"),"id=3")
        for line in ("ssid=piPONG","not-a-replyPONG","garbageOK","ssid=disconnect"):
            self.assertEqual(_reply_line(line,"status"),line)
        self.assertEqual(_reply_line("pi<3>CTRL-EVENT-DISCONNECTED reason=3","disconnect"),
                         "<3>CTRL-EVENT-DISCONNECTED reason=3")

    def test_real_request_loop_accepts_redrawn_ping_frames(self):
        channel=Protocol(("wrong","good"));channel.cancel=Event()
        original=channel.sendall
        def send(data):
            original(data)
            channel.output=channel.output.replace(b"PONG\n",b"piPONG\n")
        channel.sendall=send
        ssh=Mock();ssh.exec_command.return_value=(Mock(),Mock(),Mock(),channel)
        control=PasswordControl(ssh,"owned",channel.cancel)
        try:
            self.assertFalse(control.attempt("fixture",BSSID,"first-candidate"))
            self.assertTrue(control.attempt("fixture",BSSID,"second-candidate"))
        finally:control.close()

class WpsPreflightTests(unittest.TestCase):
    def test_disabled_spoofing_does_not_require_or_generate_an_address(self):
        self.assertEqual(spoof_mac_for({"spoof_mac_enabled":False,"spoof_mac":""}),"")

    def test_invalid_source_addresses_are_rejected(self):
        for value in ("","invalid","00:00:00:00:00:00","FF:FF:FF:FF:FF:FF","01:11:22:33:44:55"):
            with self.subTest(value=value),self.assertRaises(ValueError):
                spoof_mac_for({"spoof_mac_enabled":True,"spoof_mac":value})

    def test_valid_source_is_normalized(self):
        self.assertEqual(spoof_mac_for({"spoof_mac_enabled":True,"spoof_mac":"02:aa:bb:cc:dd:ee"}),
                         "02:AA:BB:CC:DD:EE")

    def test_backend_rejects_bad_options_before_creating_any_interface(self):
        backend=object.__new__(OpenWrtCaptureBackend)
        backend.workspace_manager=Mock();backend._find_scan_radio=Mock()
        with self.assertRaises(ValueError):
            backend.run_wps("owned",{"wps_defaults":{"spoof_mac_enabled":True,"spoof_mac":"invalid"}},
                            (AccessPointInfo(BSSID),),"second")
        backend.workspace_manager.create.assert_not_called()
        backend._find_scan_radio.assert_not_called()

    def test_service_rejects_bad_options_before_reserving_task_slot(self):
        target=AccessPointInfo(BSSID)
        service=Mock();service._shutting_down=False;service.task_controller.busy=False
        service.openwrt_backend.snapshot=OpenWrtSessionSnapshot(
            connection_state=OpenWrtConnectionState.CONNECTED,access_points=(target,))
        self.assertFalse(CaptureService.capture_openwrt_wps_async(service,
            {"wps_defaults":{"spoof_mac_enabled":True,"spoof_mac":"invalid"}},(BSSID,)))
        service.task_controller.begin.assert_not_called()

    def test_dialog_does_not_save_invalid_enabled_spoofing(self):
        from tests.test_standalone import APP
        from modules.main_pages.packet_capture.openwrt.wps_dialog import AdvancedWpsDialog
        dialog=AdvancedWpsDialog({"spoof_mac_enabled":True,"spoof_mac":"invalid"})
        try:
            with patch("modules.main_pages.packet_capture.openwrt.wps_dialog.MessageDialog") as message:
                dialog.accept()
            message.assert_called_once();self.assertEqual(dialog.result(),0)
            dialog.spoof_mac_enabled.setChecked(False);dialog.accept()
            self.assertEqual(dialog.result(),1)
        finally:dialog.deleteLater();APP.processEvents()

if __name__=="__main__":
    unittest.main()
