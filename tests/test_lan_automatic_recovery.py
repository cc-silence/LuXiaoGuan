"""Automatic DHCP recovery follows a confirmed apply, never a dialog opening."""
import time
import unittest
from unittest.mock import Mock,patch
from tests.test_lan_client_recovery import adapter,review,NEW
from modules.capture_services.lan_client_recovery import RenewalGate,client_plan


class AutomaticRecoveryTests(unittest.TestCase):
    def gate(self,*rows):
        gate=RenewalGate(client_plan(review(*(rows or (adapter(),))),NEW))
        gate.start(time.monotonic()+90)
        return gate

    def test_only_explicit_automatic_arm_selects_the_confirmed_route_once(self):
        gate=self.gate()
        self.assertIsNone(gate.take_automatic())
        gate.arm();self.assertIsNone(gate.take_automatic())
        gate.arm(automatic=True)
        self.assertEqual(gate.take_automatic(),adapter()['id'])
        self.assertIsNone(gate.take_automatic())
        self.assertIsNotNone(gate.claim(adapter()['id']))
        gate.finish_renewal('failed')
        self.assertIsNone(gate.take_automatic())
        self.assertIsNotNone(gate.claim(adapter()['id']))

    def test_static_unknown_and_ambiguous_routes_are_not_automatically_changed(self):
        cases=[(adapter(dhcp=False,renewable=False),),
               (adapter(route=False),),
               (adapter(),adapter(id='other',mac='00:00:00:00:00:02'))]
        for rows in cases:
            with self.subTest(rows=len(rows)):
                gate=self.gate(*rows);gate.arm(automatic=True)
                self.assertIsNone(gate.take_automatic())

    def test_progress_broker_starts_one_background_request_without_a_click(self):
        from tests.test_router_policy_worker import Owner
        from modules.capture_services.lan_recovery_service import LanRecoveryService
        owner=Owner(Mock(),Mock());owner._openwrt_workers['apply']=object()
        broker=LanRecoveryService(owner);gate=self.gate()
        with patch.object(broker,'request',return_value=True) as request:
            broker.observe('apply',dict(configuration_apply=True,network_recovery=gate.payload()))
            request.assert_not_called()
            gate.arm(automatic=True)
            payload=dict(configuration_apply=True,network_recovery=gate.payload())
            broker.observe('apply',payload);broker.observe('apply',payload)
            request.assert_called_once_with('apply',adapter()['id'],automatic=True)
        owner._openwrt_workers.clear();broker.release('apply');owner.deleteLater()

    def test_success_or_expiry_cannot_start_an_automatic_job(self):
        gate=self.gate();gate.arm(automatic=True);gate.close()
        self.assertIsNone(gate.take_automatic())
        gate=self.gate();gate.start(time.monotonic()+10);gate.arm(automatic=True)
        self.assertIsNone(gate.take_automatic())

    def test_wifi_and_wired_use_same_dhcp_route(self):
        for name in ('Wi-Fi','wlan0','Ethernet0'):
            with self.subTest(name=name):
                gate=self.gate(adapter(name=name));gate.arm(automatic=True)
                self.assertEqual(gate.take_automatic(),adapter()['id'])
                selected,budget=gate.claim(adapter()['id'])
                self.assertEqual(selected['name'],name)
                self.assertEqual(selected['expected_addresses'],['192.168.22.1/24'])

    def test_auto_failure_leaves_time_for_manual_retry(self):
        from tests.test_router_policy_worker import Owner
        from modules.capture_services.lan_recovery_service import LanRecoveryService
        from PySide6.QtCore import Signal
        class Service(Owner):configurationProgressChanged=Signal(str,object)
        owner=Service(Mock(),Mock());owner._openwrt_workers['apply']=object()
        broker=LanRecoveryService(owner);gate=self.gate(adapter(name='Wi-Fi'))
        with patch('modules.capture_services.task_completion.start_capture_task',return_value=True) as start:
            broker.observe('apply',dict(configuration_apply=True,network_recovery=gate.payload()))
            gate.arm(automatic=True)
            broker.observe('apply',dict(configuration_apply=True,network_recovery=gate.payload()))
            self.assertEqual(start.call_count,1)
            start.call_args.args[5]('DHCP unavailable','')
            payload=broker.payload('apply')
            self.assertTrue(payload['can_renew']);self.assertFalse(payload['busy'])
            self.assertIn('Wi-Fi',payload['message'])
            self.assertTrue(broker.request('apply',adapter()['id']))
            self.assertEqual(start.call_count,2)
            start.call_args.args[5]('still unavailable','')
        owner._openwrt_workers.clear();broker.release('apply');owner.deleteLater()

    def test_progress_after_auto_start_shows_busy_not_stale_clickable_state(self):
        from PySide6.QtCore import Signal
        from tests.test_router_policy_worker import Owner
        from modules.capture_services.lan_recovery_service import LanRecoveryService
        from modules.capture_services.task_completion import CaptureTaskCompletion
        class Service(Owner):configurationProgressChanged=Signal(str,object)
        owner=Service(Mock(),Mock());owner._openwrt_workers['apply']=object()
        owner.lan_recovery=LanRecoveryService(owner);gate=self.gate();gate.arm(automatic=True)
        values=[];owner.configurationProgressChanged.connect(lambda key,value:values.append(value))
        completion=CaptureTaskCompletion(owner,'apply',Mock(),Mock(),Mock())
        with patch('modules.capture_services.task_completion.start_capture_task',return_value=True) as start:
            completion.configuration_progress(dict(configuration_apply=True,network_recovery=gate.payload()))
            self.assertTrue(values[-1]['network_recovery']['busy'])
            start.call_args.args[5]('test failure','')
        owner._openwrt_workers.clear();owner.lan_recovery.release('apply');owner.deleteLater()
