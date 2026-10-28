"""Preflight feedback, shared validators, checkbox order and info-panel routing."""
import unittest
from unittest.mock import Mock,patch
from tests.test_standalone import APP
from tests.common_settings_fixture import create_page
from modules.main_pages.packet_capture.openwrt.router_policy_dialogs import InitialPolicyDialog
from modules.capture_services.openwrt.initial_input_validation import initial_input_errors
from PySide6.QtWidgets import QCheckBox


class InitialInputFeedbackTests(unittest.TestCase):
    create_page=create_page

    def dialog(self):
        page,_=self.create_page()
        dialog=InitialPolicyDialog(dict(pages=['wireless']),page)
        self.addCleanup(dialog.deleteLater)
        for box in dialog.checks.values():box.setChecked(False)
        dialog.checks['replace_ap'].setChecked(True)
        dialog.initial_ack.setChecked(True)
        dialog.show();APP.processEvents()
        return dialog

    def test_bad_password_is_rejected_before_dialog_accepts_and_marks_field(self):
        for value in ('short','x'*64,'\u4e2d'*8,'goodpass\n'):
            dialog=self.dialog();dialog.password.setText(value)
            accepted=Mock();dialog.accepted.connect(accepted)
            with patch('modules.main_pages.packet_capture.openwrt.router_policy_dialogs.MessageDialog') as message:
                dialog.continue_button.click()
            accepted.assert_not_called();message.assert_called_once()
            self.assertTrue(dialog.password.property('inputError'))
            dialog.password.setText('ValidPass123')
            self.assertFalse(dialog.password.property('inputError'))
            dialog.continue_button.click();accepted.assert_called_once()

    def test_name_and_address_rejected_but_unselected_inputs_ignored(self):
        dialog=self.dialog();dialog.checks['lan'].setChecked(True)
        dialog.address.setText('192.168.11.0');dialog.ap_name.edit.setText('')
        with patch('modules.main_pages.packet_capture.openwrt.router_policy_dialogs.MessageDialog'):
            dialog.continue_button.click()
        self.assertTrue(dialog.address.property('inputError'))
        self.assertTrue(dialog.ap_name.edit.property('inputError'))
        dialog.checks['lan'].setChecked(False);dialog.checks['replace_ap'].setChecked(False)
        dialog.checks['timezone'].setChecked(True)
        self.assertEqual(initial_input_errors(dialog.values()),{})
        accepted=Mock();dialog.accepted.connect(accepted);dialog.continue_button.click()
        accepted.assert_called_once()

    def test_replace_hotspot_is_last_configuration_checkbox(self):
        dialog=self.dialog();layout=dialog.ap_fields.parentWidget().layout()
        positions={key:layout.indexOf(box) for key,box in dialog.checks.items()}
        self.assertEqual(positions['replace_ap'],max(positions.values()))
        self.assertEqual(layout.indexOf(dialog.ap_fields),positions['replace_ap']+1)

    def test_restore_result_goes_only_to_info_panel_without_result_modal(self):
        page,_=self.create_page(with_info=True)
        actions=page.maintenance_actions
        actions.pending['done']=('done',None)
        with patch.object(page.info_panel,'append_info') as append,patch(
                'modules.main_pages.packet_capture.openwrt.maintenance_actions.MessageDialog') as modal:
            self.assertTrue(actions.completed('done',dict(operation='restore',note='Restored; reconnect after reboot.')))
        append.assert_called_once_with('Restored; reconnect after reboot.','info')
        self.assertFalse(page.status_label.text());self.assertTrue(page.status_label.isHidden())
        modal.assert_not_called()

    def test_without_info_panel_messages_remain_visible(self):
        page,_=self.create_page();page._message('Fallback feedback')
        self.assertEqual(page.status_label.text(),'Fallback feedback')
