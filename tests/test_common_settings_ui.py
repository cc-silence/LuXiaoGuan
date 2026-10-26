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

    def test_common_info_keeps_operation_history_and_password_failure_popup(self):
        page,_=self.create_page(with_info=True)
        page._password_key="password-request"
        with patch("modules.common_widgets.common_dialogs.MessageDialog") as dialog:
            page._failed("other-request","unrelated")
            dialog.assert_not_called()
            page._failed("password-request","Password tool missing")
            dialog.return_value.exec.assert_called_once()
        self.assertIn("Password tool missing",page.info_panel.info_box.toPlainText())
        self.assertIsNone(page._password_key)
        self.assertTrue(page.password_button.isEnabled())
        page.system_button.click()
        self.assertIn(page.status_label.text(),page.info_panel.info_box.toPlainText())
        page.info_dock.set_collapsed(True)
        page.info_dock.set_collapsed(False)
        self.assertIn("Password tool missing",page.info_panel.info_box.toPlainText())

    def test_common_information_dock_renders_both_themes(self):
        output=Path("build/common-settings-review");output.mkdir(parents=True,exist_ok=True)
        for light in (False,True):
            page,_=self.create_page(light,with_info=True)
            page._message("Common settings operation completed","success")
            APP.processEvents()
            from PySide6.QtCore import Qt
            self.assertEqual(page.info_dock.orientation,Qt.Horizontal)
            self.assertAlmostEqual(page.info_dock._expanded_ratio,240/840)
            self.assertTrue(page.info_panel.isVisible())
            self.assertGreater(page.scroll.height(),100)
            self.assertGreater(page.info_panel.height(),50)
            page.grab().save(str(output/("common-info-"+str(light)+".png")))

    def test_cards_reflow_without_recreating_widgets_at_narrow_width(self):
        page,_=self.create_page(with_info=True)
        original=page.cards
        for width in (720,1400,900):
            page.resize(width,900)
            APP.processEvents()
            page._layout_cards()
            APP.processEvents()
            self.assertEqual(page.cards,original)
            viewport_width=page.scroll.viewport().width()
            for card in page.cards:
                self.assertLessEqual(card.geometry().right(),viewport_width)
        self.assertLess(page._card_columns,3)

    def test_password_tab_moves_between_fields_before_accessory_buttons(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        page,_=self.create_page()
        dialog=RouterPasswordDialog("root",page)
        self.addCleanup(dialog.deleteLater)
        dialog.show();dialog.activateWindow();APP.processEvents()
        dialog.new_password.edit.setFocus();APP.processEvents()
        QTest.keyClick(dialog.new_password.edit,Qt.Key_Tab);APP.processEvents()
        self.assertIs(APP.focusWidget(),dialog.confirm_password.edit)
        QTest.keyClick(dialog.confirm_password.edit,Qt.Key_Backtab);APP.processEvents()
        self.assertIs(APP.focusWidget(),dialog.new_password.edit)
        QTest.keyClick(dialog.new_password.edit,Qt.Key_Tab)
        QTest.keyClick(dialog.confirm_password.edit,Qt.Key_Tab);APP.processEvents()
        self.assertIs(APP.focusWidget(),dialog.new_password.toggle)
        dialog.reject()

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

    def test_random_hostname_and_regional_timezone_keep_changes_in_editor_until_save(self):
        page,_=self.create_page()
        dialog=RouterConfigDialog(SYSTEM,page);self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.values()["timezone"],"CST-8")
        self.assertGreater(dialog.controls["zone"].count(),300)
        previous=dialog.values()["hostname"]
        dialog.hostname_random_button.click()
        self.assertNotEqual(dialog.values()["hostname"],previous)
        zone=dialog.controls["zone"]
        london=next(i for i in range(zone.count()) if tuple(zone.itemData(i))[0]=="Europe/London")
        zone.setCurrentIndex(london)
        values=dialog.values()
        self.assertEqual(values["zonename"],"Europe/London")
        self.assertIn(",M3.5.0",values["timezone"])
        from modules.capture_services.openwrt.common_settings import validate_values
        validate_values(SYSTEM,values)
        dialog.reject()
        page.manager.submit.assert_not_called()

    def test_device_timezone_rules_and_unknown_current_timezone_are_preserved(self):
        page,_=self.create_page()
        review=dict(SYSTEM,values=dict(SYSTEM["values"],zonename="Custom/Zone",timezone="ABC-3"),
                    timezones=[["Europe/London","GMT0BST,M3.5.0/1,M10.5.0"]])
        dialog=RouterConfigDialog(review,page);self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.values()["timezone"],"ABC-3")
        zone=dialog.controls["zone"]
        self.assertEqual(zone.count(),3)
        selected=next(i for i in range(zone.count()) if tuple(zone.itemData(i))[0]=="Europe/London")
        zone.setCurrentIndex(selected)
        self.assertEqual(dialog.values()["timezone"],review["timezones"][0][1])

    def test_lan_editor_buttons_select_apply_or_save_without_touching_router(self):
        for apply in (False,True):
            page,_=self.create_page()
            dialog=RouterConfigDialog(NETWORK,page);self.addCleanup(dialog.deleteLater)
            button=dialog.save_button if apply else dialog.save_only_button
            self.assertEqual(button.text(),"\u4fdd\u5b58\u5e76\u5e94\u7528" if apply else "\u4ec5\u4fdd\u5b58")
            button.click()
            self.assertEqual(dialog.result(),dialog.Accepted)
            self.assertEqual(dialog.apply_requested,apply)
            page.manager.submit.assert_not_called()

    def test_lan_confirmation_routes_reboot_save_only_and_cancel(self):
        for mode in ("reboot","save","cancel","direct-save"):
            page,_=self.create_page()
            page.lan_button.click()
            page.manager.submit.return_value="saved"
            with patch("modules.main_pages.packet_capture.openwrt.common_config_actions.RouterConfigDialog") as editor,patch(
                    "modules.main_pages.packet_capture.openwrt.common_config_actions.ConfirmDialog") as confirm:
                dialog=editor.return_value
                dialog.Accepted=1;dialog.exec.return_value=1
                dialog.values.return_value=NETWORK["values"]
                dialog.apply_requested=mode!="direct-save"
                confirm.return_value.Accepted=1
                confirm.return_value.exec.return_value=1 if mode=="reboot" else 0
                confirm.return_value.secondary_selected=mode=="save"
                page._completed("request",NETWORK)
                if mode=="cancel":
                    self.assertEqual(page.manager.submit.call_count,1)
                else:
                    args=dict(review=NETWORK,values=NETWORK["values"])
                    if mode=="reboot":args["apply"]=True
                    page.manager.submit.assert_called_with("common_save",**args)
                if mode=="direct-save":confirm.assert_not_called()
            self.assertEqual(bool(page.config_actions.progress_dialogs),mode=="reboot")
            if mode=="reboot":
                page.manager.manager.ssh._client=None
                page.manager.manager.ssh.connected=False
                note="Saved; reconnect at 192.168.11.1"
                page._completed("saved",dict(saved=True,reboot_requested=True,outcome="reconnect",note=note))
                self.assertEqual(page.status_label.text(),note)
                self.assertFalse(page.config_actions.progress_dialogs)

    def test_device_switch_during_lan_reboot_confirmation_never_saves(self):
        page,_=self.create_page()
        page.lan_button.click()
        with patch("modules.main_pages.packet_capture.openwrt.common_config_actions.RouterConfigDialog") as editor,patch(
                "modules.main_pages.packet_capture.openwrt.common_config_actions.ConfirmDialog") as confirm:
            editor.return_value.Accepted=1;editor.return_value.exec.return_value=1
            editor.return_value.values.return_value=NETWORK["values"]
            editor.return_value.apply_requested=True
            confirm.return_value.Accepted=1
            def switched():
                page.manager.manager.ssh._client=object()
                return 1
            confirm.return_value.exec.side_effect=switched
            page._completed("request",NETWORK)
        self.assertEqual(page.manager.submit.call_count,1)
