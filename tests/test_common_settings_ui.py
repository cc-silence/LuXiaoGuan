"""Popup cancel, validation, connection binding and rendered common-settings checks."""
import unittest
from pathlib import Path
from unittest.mock import patch
from tests.common_settings_fixture import create_page
from tests.test_standalone import APP
from modules.main_pages.packet_capture.openwrt.router_config_dialog import RouterConfigDialog
from modules.main_pages.packet_capture.openwrt.router_password_dialog import RouterPasswordDialog
from modules.ui_scale_manager import UiScaleManager

NETWORK=dict(config="network",section="lan",token="a"*64,devices=["br-lan","eth0"],values=dict(proto="static",device="br-lan",ipaddr="192.168.11.1",netmask="255.255.255.0",gateway="192.168.0.1",broadcast="192.168.11.255",ip6addr=["fd00::1/64"],ip6prefix=["2001:db8::/48"]))
SYSTEM=dict(config="system",section="cfg01",token="b"*64,devices=[],values=dict(hostname="CMCC_G2T7",timezone="CST-8",zonename="Asia/Shanghai"))


class CommonSettingsUiTests(unittest.TestCase):
    create_page=create_page

    def test_page_constructs_without_remote_read_and_buttons_open_read_request(self):
        page,_=self.create_page()
        manager=page.manager
        manager.submit.assert_not_called()
        page.system_button.click()
        manager.submit.assert_called_once_with("common_read",config="system")
        self.assertFalse(page.system_button.isEnabled())
        with patch("modules.main_pages.packet_capture.openwrt.common_config_actions.RouterConfigDialog") as dialog:
            dialog.return_value.exec.return_value=0
            page._completed("request",SYSTEM)
        self.assertTrue(page.system_button.isEnabled())

    def test_router_switch_during_editor_cannot_save(self):
        page,_=self.create_page()
        page.system_button.click()
        with patch("modules.main_pages.packet_capture.openwrt.common_config_actions.RouterConfigDialog") as dialog:
            dialog.return_value.Accepted=1
            def switch():
                page.manager.manager.ssh._client=object()
                return 1
            dialog.return_value.exec.side_effect=switch
            page._completed("request",SYSTEM)
        self.assertEqual(page.manager.submit.call_count,1)

    def test_system_edit_save_and_success_message(self):
        page,_=self.create_page()
        page.system_button.click()
        page.manager.submit.return_value="save"
        with patch("modules.main_pages.packet_capture.openwrt.common_config_actions.RouterConfigDialog") as dialog:
            dialog.return_value.Accepted=1;dialog.return_value.exec.return_value=1
            dialog.return_value.values.return_value=SYSTEM["values"]
            page._completed("request",SYSTEM)
        self.assertEqual(page.manager.submit.call_args.kwargs,dict(review=SYSTEM,values=SYSTEM["values"]))
        page._completed("save",dict(note="Saved"))
        self.assertEqual(page.status_label.text(),"Saved")
        self.assertTrue(page.system_button.isEnabled())

    def test_dialogs_render_both_themes_and_scales_and_cancel_keeps_values(self):
        output=Path("build/common-settings-review");output.mkdir(parents=True,exist_ok=True)
        for light in (False,True):
            page,_=self.create_page(light)
            from modules.dialog_theme import build_base_stylesheet
            from modules.config_manager import ConfigManager
            theme_patch=patch("modules.common_widgets.common_dialogs.build_current_base_stylesheet",
                side_effect=lambda config_manager,scale_manager:build_base_stylesheet(config_manager,light_theme=light,theme_name="light.qss" if light else "dark.qss",scale_manager=scale_manager))
            theme_patch.start()
            self.addCleanup(theme_patch.stop)
            page.grab().save(str(output/("cards-"+str(light)+".png")))
            for percent in (100,150):
                page.parentWidget().scale_manager=UiScaleManager(percent)
                for name,review in (("lan",NETWORK),("system",SYSTEM)):
                    dialog=RouterConfigDialog(review,page)
                    self.addCleanup(dialog.deleteLater)
                    dialog.show();APP.processEvents()
                    value=dialog.values()
                    self.assertEqual(value["hostname"] if name=="system" else value["ipaddr"],"CMCC_G2T7" if name=="system" else "192.168.11.1")
                    self.assertGreaterEqual(dialog.save_button.width(),dialog.save_button.fontMetrics().horizontalAdvance(dialog.save_button.text()))
                    dialog.grab().save(str(output/(name+"-"+str(light)+"-"+str(percent)+".png")))
                    dialog.reject()
                dialog=RouterPasswordDialog("root",page);self.addCleanup(dialog.deleteLater)
                dialog.show();APP.processEvents()
                dialog.new_password.edit.setText("secret-123")
                dialog.confirm_password.edit.setText("different")
                dialog.save_button.click()
                self.assertTrue(dialog.error_label.text())
                dialog.grab().save(str(output/("password-"+str(light)+"-"+str(percent)+".png")))
                dialog.reject()
                self.assertFalse(dialog.new_password.submitted_text())
            theme_patch.stop()
            page.manager.submit.assert_not_called()
