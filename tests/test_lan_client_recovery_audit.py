"""Second-pass fault cases for client recovery; never mutate real adapters."""
import time
import unittest
from unittest.mock import patch,Mock
from tests.test_lan_client_recovery import adapter,review,NEW
from modules.capture_services.lan_client_recovery import client_plan,RenewalGate


class RecoveryAuditTests(unittest.TestCase):
    def gate(self,*rows):
        return RenewalGate(client_plan(review(*(rows or (adapter(),))),NEW))

    def test_twenty_five_second_recovery_phase_accepts_bounded_request(self):
        gate=self.gate();gate.start(time.monotonic()+25,restoring=True)
        claimed=gate.claim(adapter()['id'])
        self.assertIsNotNone(claimed)
        self.assertGreater(claimed[1],10);self.assertLess(claimed[1],17.1)
        gate.finish_renewal('done')

    def test_late_click_gives_reason_and_publishes_unavailable_state(self):
        gate=self.gate();gate.start(time.monotonic()+12,restoring=True)
        self.assertIsNone(gate.claim(adapter()['id']))
        self.assertTrue(gate.message);self.assertFalse(gate.payload()['can_renew'])
        self.assertFalse(gate.busy)

    def test_only_chosen_adapter_can_satisfy_readiness(self):
        chosen=adapter(route=False);other=adapter(id='other',route=False,mac='11:22:33:44:55:66')
        gate=self.gate(chosen,other);gate.start(time.monotonic()+90);gate.arm()
        gate.claim(chosen['id']);gate.finish_renewal('done')
        fresh=adapter(id='other',route=False,mac=other['mac'],addresses=['192.168.22.55/24'],gateways=['192.168.22.1'],dns=['192.168.22.1'])
        with patch('modules.platform_services.lan_network.inspect',return_value=dict(adapters=[chosen,fresh])):
            self.assertFalse(gate.ready())

    def test_wan_management_does_not_offer_overlapping_local_lan_reset(self):
        value=review(adapter());value['client_network']['peer']='192.168.11.254'
        self.assertFalse(client_plan(value,NEW)['guide'])

    def test_facade_counts_action_and_final_read_in_same_budget(self):
        from modules.platform_services import lan_network as facade
        from modules.platform_services import lan_network_windows as windows
        with patch.object(facade,'current_os',return_value=facade.WINDOWS),patch.object(facade.time,'monotonic',side_effect=[100,110]),patch.object(windows,'renew') as execute,patch.object(facade,'inspect',return_value=dict(adapters=[adapter()])) as inspect:
            facade.renew(adapter(),20,lambda:True)
        self.assertEqual(execute.call_args.args[1],17)
        self.assertEqual(inspect.call_args.kwargs['timeout'],10)

    def test_linux_slow_validation_leaves_no_budget_for_activation(self):
        from modules.platform_services import lan_network_linux as linux
        with patch.object(linux.time,'monotonic',side_effect=[100,119.5]),patch.object(linux,'inspect',return_value=[adapter()]),patch.object(linux,'command') as command:
            with self.assertRaises(TimeoutError):linux.renew(adapter(),20,lambda:True)
        command.assert_not_called()

    def test_linux_timeout_never_disables_device_or_autoconnect(self):
        from modules.platform_services import lan_network_linux as linux
        with patch.object(linux,'inspect',return_value=[adapter()]),patch.object(linux,'command',side_effect=TimeoutError('activation pending')) as command:
            with self.assertRaises(TimeoutError):linux.renew(adapter(),20,lambda:True)
        self.assertEqual(command.call_count,1)
        self.assertNotIn('disconnect',command.call_args.args[0]);self.assertNotIn('down',command.call_args.args[0])

    def test_windows_denied_renewal_uses_same_identity_and_expiry_after_authorization(self):
        import base64
        from modules.platform_services import lan_network_windows as windows
        with patch.object(windows.subprocess,'run',return_value=Mock(returncode=5)) as run,patch('modules.platform_services.update_elevation.run_elevated_update',return_value=(True,0)) as elevate:
            windows.renew(adapter(),20,lambda:True)
        self.assertEqual(run.call_count,1);self.assertEqual(elevate.call_count,1)
        original=run.call_args.args[0][-1]
        self.assertIn(original,elevate.call_args.args[1])
        script=base64.b64decode(original).decode('utf-16-le')
        self.assertNotIn('ReleaseDHCPLease',script)
        self.assertIn('RenewDHCPLease',script)

    def test_windows_expired_authorization_is_not_launched(self):
        from modules.platform_services import lan_network_windows as windows
        with patch.object(windows.time,'time',side_effect=[100,125]),patch.object(windows.subprocess,'run',return_value=Mock(returncode=5)),patch('modules.platform_services.update_elevation.run_elevated_update') as elevate:
            with self.assertRaises(RuntimeError):windows.renew(adapter(),20,lambda:True)
        elevate.assert_not_called()

    def test_rejected_click_restores_ui_state_instead_of_silent_disabled_button(self):
        from PySide6.QtCore import Signal
        from tests.test_router_policy_worker import Owner
        from modules.capture_services.lan_recovery_service import LanRecoveryService
        class Service(Owner):configurationProgressChanged=Signal(str,object)
        owner=Service(Mock(),Mock());broker=LanRecoveryService(owner)
        gate=self.gate();gate.start(time.monotonic()+12,restoring=True)
        owner._openwrt_workers['apply']=object();events=[]
        owner.configurationProgressChanged.connect(lambda key,value:events.append(value))
        broker.observe('apply',dict(configuration_apply=True,network_recovery=gate.payload()))
        self.assertFalse(broker.request('apply',adapter()['id']))
        self.assertEqual(len(events),1);self.assertFalse(events[0]['network_recovery']['can_renew'])
        self.assertTrue(events[0]['network_recovery']['message'])
        owner._openwrt_workers.clear();broker.release('apply');owner.deleteLater()
