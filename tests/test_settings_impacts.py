"""Final confirmation and cancellation at common-settings write boundaries."""
from pathlib import Path
import unittest
from unittest.mock import patch
from tests.common_settings_fixture import create_page
from tests.test_common_settings_ui import NETWORK
from tests.test_standalone import APP
from modules.main_pages.packet_capture.openwrt.router_password_dialog import RouterPasswordDialog
from modules.main_pages.packet_capture.openwrt.settings_impacts import DISCONNECT_WARNING,LAN_APPLY_WARNING,PASSWORD_WARNING
from modules.common_widgets.common_dialogs import ConfirmDialog


class SettingsImpactTests(unittest.TestCase):
    create_page=create_page

    def test_reboot_cancel_and_connection_switch_never_submit(self):
        for switched in (False,True):
            page,_=self.create_page()
            with patch("modules.main_pages.packet_capture.openwrt.maintenance_actions.ConfirmDialog") as factory:
                dialog=factory.return_value;dialog.Accepted=1
                def answer():
                    if switched:page.manager.manager.ssh._client=object()
                    return 1 if switched else 0
                dialog.exec.side_effect=answer
                page.reboot_button.click()
                self.assertIn(DISCONNECT_WARNING,factory.call_args.args[1])
            page.manager.submit.assert_not_called()
            self.assertFalse(page.maintenance_actions.active)

    def test_reboot_requires_confirmation_then_pending_and_failure_recover(self):
        page,_=self.create_page()
        with patch("modules.main_pages.packet_capture.openwrt.maintenance_actions.ConfirmDialog") as factory:
            factory.return_value.Accepted=1;factory.return_value.exec.return_value=1
            page.reboot_button.click()
        page.manager.submit.assert_called_once_with("maintenance_reboot")
        self.assertFalse(page.reboot_button.isEnabled())
        page._failed("request","Disconnected")
        self.assertTrue(page.reboot_button.isEnabled())
        self.assertFalse(page.maintenance_actions.active)

    def test_lan_warning_cancel_blocks_save_and_keeps_form_values(self):
        page,_=self.create_page();page.lan_button.click()
        with patch("modules.main_pages.packet_capture.openwrt.common_config_actions.RouterConfigDialog") as editor,patch(
                "modules.main_pages.packet_capture.openwrt.common_config_actions.ConfirmDialog") as confirm:
            editor.return_value.Accepted=1;editor.return_value.exec.return_value=1
            editor.return_value.values.return_value=NETWORK["values"]
            editor.return_value.apply_requested=True
            confirm.return_value.secondary_selected=False
            confirm.return_value.Accepted=1;confirm.return_value.exec.return_value=0
            page._completed("request",NETWORK)
            self.assertIn(LAN_APPLY_WARNING,confirm.call_args.args[1])
        self.assertEqual(page.manager.submit.call_count,1)
        self.assertTrue(page.lan_button.isEnabled())

    def test_password_warning_cancel_does_not_accept_or_expose_password(self):
        page,_=self.create_page()
        dialog=RouterPasswordDialog("root",page);self.addCleanup(dialog.deleteLater)
        dialog.new_password.edit.setText("secret-123")
        dialog.confirm_password.edit.setText("secret-123")
        with patch("modules.main_pages.packet_capture.openwrt.router_password_dialog.ConfirmDialog") as confirm:
            confirm.return_value.Accepted=1;confirm.return_value.exec.return_value=0
            dialog.save_button.click()
            self.assertNotEqual(dialog.result(),dialog.Accepted)
            self.assertIn(PASSWORD_WARNING,confirm.call_args.args)
            self.assertNotIn("secret-123",str(confirm.call_args))
            confirm.return_value.exec.return_value=1
            dialog.save_button.click()
            self.assertEqual(dialog.result(),dialog.Accepted)

    def test_warning_dialogs_render_both_themes(self):
        folder=Path("build/router-reboot-review/ui");folder.mkdir(parents=True,exist_ok=True)
        for light in (False,True):
            page,_=self.create_page(light)
            for name,message in (("reboot",DISCONNECT_WARNING),("lan",LAN_APPLY_WARNING),("password",PASSWORD_WARNING)):
                dialog=ConfirmDialog(name,message,page)
                self.addCleanup(dialog.deleteLater)
                dialog.show();APP.processEvents()
                self.assertTrue(dialog.no_button.isVisible())
                self.assertTrue(dialog.yes_button.isVisible())
                dialog.grab().save(str(folder/(name+"-"+str(light)+".png")))
                dialog.reject()
