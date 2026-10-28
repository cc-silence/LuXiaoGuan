"""LAN feedback regressions; no real router or network adapter mutations."""
import copy
import time
import unittest
from unittest.mock import patch
from tests.test_lan_client_recovery import adapter,review,NEW
from modules.capture_services.lan_client_recovery import client_plan,RenewalGate


class LanFeedbackTests(unittest.TestCase):
    def gate(self):
        row=adapter(dhcp=False,renewable=False)
        gate=RenewalGate(client_plan(review(row),NEW));gate.start(time.monotonic()+90);gate.arm()
        return gate,row

    def test_client_configuration_does_not_block_confirmation_or_trigger_inspection(self):
        gate,row=self.gate()
        with patch('modules.platform_services.lan_network.inspect',side_effect=AssertionError('Client inspection is not a confirmation condition')) as inspect:
            self.assertTrue(gate.ready());gate.start(time.monotonic()+25,restoring=True)
            self.assertTrue(gate.ready());inspect.assert_not_called()
        self.assertIsNone(gate.claim(row['id']))

    def test_guide_uses_login_address_and_a_distinct_computer_example(self):
        for address in ('192.168.22.1','192.168.22.2','192.168.22.1/31','192.168.22.1/32'):
            plan=client_plan(review(adapter(dhcp=False)),dict(NEW,ipaddr=address))
            self.assertNotIn('/24',plan['guide']);self.assertNotIn('/31',plan['guide']);self.assertNotIn('/32',plan['guide'])
            self.assertIn(address.split('/')[0],plan['guide'])

    def test_recovery_button_is_hidden_until_apply_has_been_sent_and_never_reenabled_while_busy(self):
        from tests.test_standalone import APP
        from PySide6.QtWidgets import QWidget
        from modules.main_pages.packet_capture.openwrt.lan_recovery_panel import LanRecoveryPanel
        parent=QWidget();panel=LanRecoveryPanel(parent);self.addCleanup(parent.deleteLater)
        gate=RenewalGate(client_plan(review(adapter()),NEW));gate.start(time.monotonic()+90)
        panel.update_payload(gate.payload())
        self.assertTrue(panel.button.isHidden());self.assertFalse(panel.button.isEnabled())
        gate.arm();panel.update_payload(gate.payload())
        self.assertFalse(panel.button.isHidden());self.assertTrue(panel.button.isEnabled())
        gate.claim(adapter()['id']);panel.update_payload(gate.payload())
        self.assertFalse(panel.button.isEnabled());self.assertFalse(panel.adapters.isEnabled())
        gate.close();panel.update_payload(gate.payload());self.assertTrue(panel.button.isHidden())

    def test_netmask_dropdown_preserves_custom_values_and_validation(self):
        from tests.test_standalone import APP
        from tests.test_common_settings_ui import NETWORK
        from modules.main_pages.packet_capture.openwrt.router_config_dialog import RouterConfigDialog
        from modules.capture_services.openwrt.common_settings import validate_values
        original=copy.deepcopy(NETWORK);original['values']['netmask']='255.255.254.0'
        dialog=RouterConfigDialog(original);self.addCleanup(dialog.deleteLater)
        control=dialog.controls['netmask'];self.assertTrue(control.isEditable())
        self.assertEqual(dialog.values()['netmask'],'255.255.254.0')
        self.assertGreaterEqual(control.count(),7)
        control.setCurrentIndex(control.findText('255.255.0.0'))
        self.assertEqual(dialog.values()['netmask'],'255.255.0.0')
        control.setEditText('255.255.252.0');validate_values(original,dialog.values())
        control.setEditText('255.0.255.0')
        with self.assertRaises(ValueError):validate_values(original,dialog.values())
        dialog.reject();self.assertEqual(original['values']['netmask'],'255.255.254.0')


class LanFailureReasonTests(unittest.TestCase):
    def test_verified_router_is_confirmed_even_when_computer_settings_are_old(self):
        from tests.test_checked_apply import CheckedApplyTests,run_checked,fast_deadlines,LuaRuntime
        if not LuaRuntime:self.fail('Lua runtime required for this regression')
        device,channel,event,stage,verify=CheckedApplyTests().fixture()
        row=adapter(dhcp=False,renewable=False)
        gate=RenewalGate(client_plan(review(row),NEW))
        with fast_deadlines(),patch('modules.platform_services.lan_network.inspect',side_effect=AssertionError('No client validation')):
            result=run_checked(channel,stage,verify,event,candidates=('new',),original_host='old',client_recovery=gate)
        self.assertTrue(result['applied'])
        self.assertNotIn('checked_rollback',channel.calls);self.assertIn('checked_confirm',channel.calls)
        self.assertTrue(gate.closed)

    def test_old_router_runtime_produces_runtime_failure_reason(self):
        from tests.test_checked_apply import CheckedApplyTests,run_checked,fast_deadlines,LuaRuntime
        if not LuaRuntime:self.fail('Lua runtime required for this regression')
        device,channel,event,stage,verify=CheckedApplyTests().fixture();channel.bad_runtime=True
        with fast_deadlines():
            with self.assertRaises(RuntimeError) as error:
                run_checked(channel,stage,verify,event,candidates=('new',),original_host='old')
        self.assertNotIn('DNS',str(error.exception));self.assertIn('checked_rollback',channel.calls)
