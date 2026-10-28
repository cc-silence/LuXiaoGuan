"""All disconnected router actions explain login instead of silently doing nothing."""
import unittest
from dataclasses import replace
from unittest.mock import patch
from tests.common_settings_fixture import create_page


class CommonLoginPromptTests(unittest.TestCase):
    create_page=create_page
    def test_all_device_actions_show_login_prompt_and_submit_nothing(self):
        page,service=self.create_page()
        page.manager.manager.ssh.connected=False
        service.snapshot=replace(service.snapshot,connected=False)
        page._sync()
        buttons=[page.password_button,page.lan_button,page.system_button,page.backup_button,page.restore_button,
                 page.reset_button,page.reboot_button,*page.policy_actions.buttons.values(),page.relocation_actions.button,page.orphan_actions.button]
        with patch('modules.common_widgets.common_dialogs.MessageDialog') as prompt:
            for button in buttons:
                with self.subTest(text=button.text()):
                    self.assertTrue(button.isEnabled());before=prompt.call_count;button.click()
                    self.assertEqual(prompt.call_count,before+1)
        page.manager.submit.assert_not_called()
        self.assertFalse(page.config_actions.active);self.assertFalse(page.maintenance_actions.active);self.assertFalse(page.policy_actions.active)
    def test_disconnect_after_login_uses_live_connection_not_stale_button_or_signal_snapshot(self):
        page,service=self.create_page()
        self.assertTrue(service.snapshot.connected)
        self.assertTrue(page.lan_button.isEnabled())
        page.manager.manager.ssh.connected=False
        # Do not deliver a snapshot signal or sync the buttons: this is the reported race.
        buttons=[page.password_button,page.lan_button,page.system_button,page.backup_button,page.restore_button,
                 page.reset_button,page.reboot_button,*page.policy_actions.buttons.values()]
        with patch('modules.common_widgets.common_dialogs.MessageDialog') as prompt:
            for button in buttons:
                with self.subTest(text=button.text()):
                    before=prompt.call_count;button.click();self.assertEqual(prompt.call_count,before+1)
            for action in (page.relocation_actions,page.orphan_actions):
                action.button.setEnabled(True);before=prompt.call_count;action.button.click()
                self.assertEqual(prompt.call_count,before+1)
        page.manager.submit.assert_not_called()
        self.assertFalse(page.relocation_actions._waiting);self.assertFalse(page.orphan_actions._waiting)

    def test_busy_state_still_blocks_actions_and_logged_in_read_runs(self):
        page,service=self.create_page();page.capture_service.is_busy=True;page._sync()
        self.assertFalse(page.lan_button.isEnabled())
        page.capture_service.is_busy=False;page._sync()
        with patch('modules.common_widgets.common_dialogs.MessageDialog') as prompt:page.lan_button.click();prompt.assert_not_called()
        page.manager.submit.assert_called_once_with('common_read',config='network')
