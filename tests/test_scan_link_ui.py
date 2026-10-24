"""Real controls for scan cancellation, link-based warnings and password refill."""
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import unittest
from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtWidgets import QWidget, QLineEdit
from PySide6.QtTest import QTest
from tests.test_standalone import APP
from tests.test_wireless_ui import environment
from modules.common_widgets.common_dialogs import ConfirmDialog
from modules.dialog_theme import build_base_stylesheet
from modules.config_manager import ConfigManager, BASE_DIR
from modules.capture_services.openwrt.management_link import ManagementLink
from modules.capture_services.scan_management_service import ScanManagementPlan
from modules.main_pages.packet_capture.scan_start_controller import ScanStartController
from modules.main_pages.packet_capture.openwrt.signal_setup_actions import SignalSetupActions
from modules.capture_services.openwrt.wireless_setup_models import (
    WirelessNetwork, WirelessTaskResult, StoredWirelessPassword)


class Probe(QObject):
    finished = Signal(str,object,str,bool)
    def start(self, settings):
        self.options = settings
        return "probe"


class Setup(QObject):
    completed = Signal(object)
    snapshotChanged = Signal(object)
    def __init__(self, env):
        super().__init__()
        self.snapshot = NS(environment=env)
        self.identity = object()
        self._connection_identity = Mock(side_effect=lambda: self.identity)
        self.read_password = Mock(return_value=True)
        self.cancel = Mock()


class ScanLinkUiTests(unittest.TestCase):
    def setUp(self):
        self.page = QWidget()
        self.probe = Probe()
        self.page.info_panel = NS(append_info=Mock())
        self.page.capture_service = NS(scan_management=self.probe,
            openwrt_backend=NS(ssh_client=NS(host_fingerprint="fingerprint")),
            scan_openwrt_wifi_async=Mock(return_value=True))
        self.controller = ScanStartController(self.page)

    def tearDown(self):
        self.page.close()
        self.page.deleteLater()
        APP.processEvents()

    def deliver(self, link, *, fingerprint="fingerprint"):
        self.controller.start({"band":"2.4G"})
        self.probe.finished.emit("probe",ScanManagementPlan("phy0",link,fingerprint),"",False)

    def test_x_and_escape_cancel_without_launching_scan(self):
        for action in ("x","escape"):
            def choose(dialog):
                dialog.show()
                APP.processEvents()
                if action == "x":
                    dialog.close_btn.click()
                else:
                    QTest.keyClick(dialog,Qt.Key_Escape)
                self.assertFalse(dialog.secondary_selected)
                return dialog.result()
            with self.subTest(action=action),patch.object(ConfirmDialog,"exec",choose):
                self.deliver(ManagementLink("wireless","phy0-ap0","phy0","ssh_return_path"))
                self.page.capture_service.scan_openwrt_wifi_async.assert_not_called()

    def test_cancel_button_cannot_override_management_radio_protection(self):
        def choose(dialog):
            dialog.no_button.click()
            return dialog.result()
        with patch.object(ConfirmDialog, "exec", choose):
            self.deliver(ManagementLink("wireless", "phy0-ap0", "phy0", "ssh_return_path"))
        self.page.capture_service.scan_openwrt_wifi_async.assert_not_called()

    def test_wired_and_other_phy_never_show_same_band_dialog(self):
        for link in (ManagementLink("wired","eth0"),ManagementLink("wireless","phy1-ap0","phy1")):
            with patch.object(ConfirmDialog,"exec",side_effect=AssertionError("Unexpected warning")):
                self.deliver(link)
        self.assertEqual(self.page.capture_service.scan_openwrt_wifi_async.call_count,2)


    def test_active_business_on_either_radio_does_not_warn_on_independent_login(self):
        for link in (ManagementLink("wired", "eth0"), ManagementLink("wireless", "phy1-ap0", "phy1")):
            for modes in (("managed",), ("ap", "managed")):
                with self.subTest(link=link, modes=modes):
                    self.controller.start({"band": "2.4G"})
                    plan = ScanManagementPlan("phy0", link, "fingerprint", modes)
                    with patch.object(ConfirmDialog, "exec", side_effect=AssertionError("Unexpected warning")):
                        self.probe.finished.emit("probe", plan, "", False)
        self.assertEqual(self.page.capture_service.scan_openwrt_wifi_async.call_count, 4)

    def test_switch_to_other_radio_launches_without_switching_back(self):
        self.page.openwrt_tab = NS(capture_page=NS(tools=NS(band_combo=NS(setCurrentText=Mock()))))
        self.page._save_openwrt_settings = Mock()
        link = ManagementLink("wireless", "phy0-ap0", "phy0", "ssh_return_path")
        prompts = []
        def switch(dialog):
            prompts.append(dialog)
            dialog.yes_button.click()
            return dialog.result()
        with patch.object(ConfirmDialog, "exec", switch):
            self.deliver(link)
            self.assertEqual(len(prompts), 1)
            self.assertEqual(self.probe.options["band"], "5G")
            self.page.capture_service.scan_openwrt_wifi_async.assert_not_called()
            plan = ScanManagementPlan("phy1", link, "fingerprint", ("ap", "managed"))
            self.probe.finished.emit("probe", plan, "", False)
            self.assertEqual(len(prompts), 1)
        self.page.openwrt_tab.capture_page.tools.band_combo.setCurrentText.assert_called_once_with("5G")
        settings = self.page.capture_service.scan_openwrt_wifi_async.call_args.args[0]
        self.assertEqual(settings["band"], "5G")
        self.assertEqual(settings["management_scan_phy"], "phy1")
        self.assertNotIn("management_scan_allowed_phy", settings)

    def test_cancelled_stale_or_old_probe_does_not_launch(self):
        self.controller.start({"band":"2.4G"})
        self.probe.finished.emit("old",None,"",False)
        self.assertIsNotNone(self.controller._pending)
        self.probe.finished.emit("probe",None,"",True)
        self.page.capture_service.scan_openwrt_wifi_async.assert_not_called()
        self.deliver(ManagementLink("wired","eth0"),fingerprint="old-router")
        self.page.capture_service.scan_openwrt_wifi_async.assert_not_called()

    def test_close_x_has_contrast_in_both_rendered_themes(self):
        output = BASE_DIR/"build"/"management-link-review"
        output.mkdir(parents=True,exist_ok=True)
        for light in (False,True):
            stylesheet = build_base_stylesheet(ConfigManager(),light_theme=light,
                theme_name="light.qss" if light else "dark.qss")
            with patch("modules.common_widgets.common_dialogs.build_current_base_stylesheet",return_value=stylesheet):
                dialog = ConfirmDialog("Management link","Router-side management connection check.",self.page)
            dialog.show()
            APP.processEvents()
            background = dialog.title_bar.grab().toImage().pixelColor(40,20)
            self.assertGreater(background.lightness(),200) if light else self.assertLess(background.lightness(),70)
            image = dialog.close_btn.icon().pixmap(24,24).toImage()
            colors = [image.pixelColor(x,y) for x in range(image.width()) for y in range(image.height())
                      if image.pixelColor(x,y).alpha()>180]
            self.assertTrue(colors)
            mean = sum(c.lightness() for c in colors)/len(colors)
            self.assertLess(mean,140) if light else self.assertGreater(mean,180)
            self.assertTrue(dialog.grab().save(str(output/("close-light.png" if light else "close-dark.png"))))
            dialog.reject()
            dialog.deleteLater()


class EditPasswordTests(unittest.TestCase):
    def test_ap_and_relay_prefill_masked_reveal_edit_and_clear(self):
        for mode in ("ap","sta"):
            with self.subTest(mode=mode):
                env = environment()
                if mode == "ap":
                    env.data["rows"][0]["has_key"] = True
                    section = "home"
                else:
                    section = "upper"
                    env.data["rows"].append(dict(section=section,ssid="Upper",device="radio7",mode="sta",
                        network="wan",encryption="psk2",has_key=True))
                page = QWidget()
                service = Setup(env)
                dialogs = []
                def show(dialog):
                    dialogs.append(dialog)
                    dialog.open()
                panels = NS(page=page,_show=show)
                actions = SignalSetupActions(panels,service)
                row = NS(section=section,ssid="Home" if mode=="ap" else "Upper",mode=mode,
                         radio="radio7",running="active")
                if mode=="ap":
                    actions.access_point("radio7","5G",row=row)
                else:
                    actions.relay(WirelessNetwork("radio7","","Upper",36,"5G",-40,"psk2"),row=row)
                dialog = dialogs[0]
                service.read_password.assert_called_once_with(section,env.data["revision"])
                self.assertTrue(dialog._busy)
                value = StoredWirelessPassword("saved-test-password")
                service.completed.emit(WirelessTaskResult("password",value))
                value.clear()
                self.assertFalse(dialog._busy)
                self.assertEqual(dialog.password.edit.text(),"saved-test-password")
                self.assertEqual(dialog.password.edit.echoMode(),QLineEdit.Password)
                self.assertEqual(dialog.password.submitted_text(),"")
                APP.processEvents()
                output = BASE_DIR/"build"/"management-link-review"
                output.mkdir(parents=True,exist_ok=True)
                self.assertTrue(dialog.grab().save(str(output/("edit-"+mode+"-password.png"))))
                dialog.password.toggle.click()
                self.assertEqual(dialog.password.edit.echoMode(),QLineEdit.Normal)
                dialog.password.edit.setText("changed-password")
                self.assertEqual(dialog.password.submitted_text(),"changed-password")
                dialog.reject()
                self.assertEqual(dialog.password.edit.text(),"")
                self.assertIsNone(dialog.password._stored_password)
                actions.dispose()
                page.close()
                page.deleteLater()
                APP.processEvents()


if __name__ == "__main__":
    unittest.main()
