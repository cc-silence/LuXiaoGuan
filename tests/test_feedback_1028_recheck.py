"""Second independent review: declined retry, closed windows and cross-offer workers."""
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch
from PySide6.QtWidgets import QWidget
from tests.test_standalone import APP
from tests.common_settings_fixture import create_page
from tests.test_factory_reset_recovery import RecoveryOwner
from tests.test_lan_client_recovery import adapter,review,NEW
from modules.capture_services.lan_client_recovery import client_plan,factory_reset_plan
from modules.capture_services.lan_recovery_service import LanRecoveryService


class ForceRecoveryRecheckTests(unittest.TestCase):
    create_page=create_page

    def setup_result(self):
        page,_=self.create_page();page.manager.manager.runner=NS()
        actions=page.config_actions;ssh=page.manager.manager.ssh
        actions.pending['normal']=('network',ssh._client,ssh.host_fingerprint,'save')
        actions.recovery_plans['normal']=client_plan(review(adapter()),NEW)
        result=dict(outcome='restored',note='Restored',force_retry='proof',force_values=NEW)
        return page,actions,result

    def test_declining_force_keeps_recovery_for_original_subnet(self):
        page,actions,result=self.setup_result()
        with patch('modules.main_pages.packet_capture.openwrt.common_config_actions.ConfirmDialog') as confirm,patch('modules.main_pages.packet_capture.openwrt.network_recovery_result_dialog.NetworkRecoveryResultDialog') as recovery:
            confirm.return_value.Accepted=1;confirm.return_value.exec.return_value=0
            self.assertTrue(actions.completed('normal',result))
        page.manager.submit.assert_not_called()
        self.assertEqual(recovery.call_count,1)
        self.assertEqual(recovery.call_args.args[1]['new_addresses'],['192.168.11.1/24'])
        self.assertFalse(actions.pending);self.assertFalse(actions.recovery_plans)

    def test_accepting_force_carries_recovery_forward_without_an_extra_window(self):
        page,actions,result=self.setup_result();page.manager.submit.return_value='forced'
        with patch('modules.main_pages.packet_capture.openwrt.common_config_actions.ConfirmDialog') as confirm,patch('modules.main_pages.packet_capture.openwrt.network_recovery_result_dialog.NetworkRecoveryResultDialog') as recovery:
            confirm.return_value.Accepted=1;confirm.return_value.exec.return_value=1
            actions.completed('normal',result)
        recovery.assert_not_called()
        self.assertEqual(actions.pending['forced'][3],'force')
        self.assertEqual(actions.recovery_plans['forced']['new_addresses'],['192.168.22.1/24'])

    def test_switching_router_during_retry_prompt_never_opens_old_recovery(self):
        page,actions,result=self.setup_result()
        with patch('modules.main_pages.packet_capture.openwrt.common_config_actions.ConfirmDialog') as confirm,patch('modules.main_pages.packet_capture.openwrt.network_recovery_result_dialog.NetworkRecoveryResultDialog') as recovery:
            confirm.return_value.Accepted=1
            def switch():page.manager.manager.ssh._client=object();return 0
            confirm.return_value.exec.side_effect=switch
            actions.completed('normal',result)
        recovery.assert_not_called();page.manager.submit.assert_not_called()


class CrossOfferConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.owner=RecoveryOwner(NS(),NS());self.addCleanup(self.owner.deleteLater)
        self.broker=LanRecoveryService(self.owner)

    def offer(self,row=None):
        return self.broker.create_offer(factory_reset_plan(dict(adapters=[row or adapter()])))

    def test_closed_window_keeps_adapter_locked_until_its_worker_finishes(self):
        first=self.offer();second=self.offer();jobs=[]
        with patch('modules.capture_services.task_completion.start_capture_task',side_effect=lambda *args:jobs.append(args)):
            self.assertTrue(self.broker.request(first,adapter()['id']))
            self.broker.release(first)
            self.assertFalse(self.broker.request(second,adapter()['id']))
            self.assertEqual(len(jobs),1)
            jobs[0][5]('cancelled','')
            self.assertFalse(self.broker.adapter_jobs)
            self.assertTrue(self.broker.request(second,adapter()['id']))
            self.assertEqual(len(jobs),2)
            jobs[0][5]('duplicate old failure','')
            self.assertTrue(self.broker.adapter_jobs)
            jobs[1][4](adapter())
        self.assertFalse(self.broker.adapter_jobs)
        self.assertFalse(self.broker.payload(second)['busy'])
        self.broker.release(second);self.assertFalse(self.broker.offers)

    def test_different_adapters_can_run_independently(self):
        a=adapter();b=adapter(id='other-adapter',mac='02:11:22:33:44:55')
        first=self.offer(a);second=self.offer(b);jobs=[]
        with patch('modules.capture_services.task_completion.start_capture_task',side_effect=lambda *args:jobs.append(args)):
            self.assertTrue(self.broker.request(first,a['id']))
            self.assertTrue(self.broker.request(second,b['id']))
            self.assertEqual(len(self.broker.adapter_jobs),2)
            jobs[0][5]('failure','');jobs[1][4](b)
        self.assertFalse(self.broker.adapter_jobs)
        self.broker.release(first);self.broker.release(second)

    def test_worker_start_failure_releases_gate_and_adapter_lock(self):
        key=self.offer()
        with patch('modules.capture_services.task_completion.start_capture_task',return_value=False):
            self.assertFalse(self.broker.request(key,adapter()['id']))
        self.assertFalse(self.broker.adapter_jobs)
        self.assertFalse(self.broker.payload(key)['busy'])
        self.assertTrue(self.broker.payload(key)['message'])
        self.broker.release(key)

    def test_panel_blocks_a_second_offer_and_preserves_selected_adapter_after_refill(self):
        from modules.main_pages.packet_capture.openwrt.lan_recovery_panel import LanRecoveryPanel
        parent=QWidget();self.addCleanup(parent.deleteLater);panel=LanRecoveryPanel(parent)
        rows=[adapter(),adapter(id='second',name='Ethernet2')]
        value=dict(guide='Guide',adapters=rows,selection_required=True,can_renew=True)
        panel.update_payload(value);panel.adapters.setCurrentIndex(2)
        self.assertEqual(panel.adapters.currentData(),'second')
        updated=[dict(row,addresses=['192.168.22.100/24']) for row in rows]
        panel.update_payload(dict(value,adapters=updated,busy_adapters=['second']))
        self.assertEqual(panel.adapters.currentData(),'second');self.assertFalse(panel.button.isEnabled())
        panel.update_payload(dict(value,adapters=updated,busy_adapters=[]))
        self.assertEqual(panel.adapters.currentData(),'second');self.assertTrue(panel.button.isEnabled())
