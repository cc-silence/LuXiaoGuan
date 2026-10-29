"""Offline adapter identity, consent, lifecycle and visual regressions."""
import copy
import threading
import time
import unittest
from unittest.mock import patch,Mock
from modules.capture_services.lan_client_recovery import client_plan,RenewalGate


def adapter(**changes):
    row=dict(id='7862e3c3-2780-4f50-98c7-8d29dc0cef7a',index=14,name='Ethernet0',mac='00:50:56:2B:29:2D',
             addresses=['192.168.11.123/24'],gateways=['192.168.11.1'],dns=['192.168.11.1'],dhcp=True,
             renewable=True,connected=True,route=True,connection='profile')
    row.update(changes);return row


def review(*rows):
    return dict(values=dict(proto='static',ipaddr='192.168.11.1',netmask='255.255.255.0'),
                client_network=dict(adapters=list(rows)))


NEW=dict(proto='static',ipaddr='192.168.22.1',netmask='255.255.255.0')


class ClientPlanningTests(unittest.TestCase):
    def test_static_guide_and_no_automatic_renew(self):
        result=client_plan(review(adapter(dhcp=False,renewable=False)),NEW)
        self.assertIn('192.168.22.1',result['guide']);self.assertNotIn('/24',result['guide'])
        self.assertIn('255.255.255.0',result['guide'])
        gate=RenewalGate(result);gate.start(time.monotonic()+90);gate.arm()
        self.assertIsNone(gate.claim(adapter()['id']))
        with patch('modules.platform_services.lan_network.inspect',return_value=dict(adapters=[adapter(dhcp=False)])):
            self.assertTrue(gate.ready())

    def test_known_route_excludes_overlapping_other_adapter(self):
        row=adapter(id='other',route=False)
        routed=adapter(id='wan',addresses=['10.0.0.2/24'],gateways=['10.0.0.1'])
        self.assertFalse(client_plan(review(row,routed),NEW)['guide'])

    def test_unknown_route_requires_explicit_selection_even_one_adapter(self):
        self.assertTrue(client_plan(review(adapter(route=False)),NEW)['selection_required'])
        self.assertFalse(client_plan(review(adapter()),NEW)['selection_required'])

    def test_unchanged_and_dhcp_router_do_not_offer_recovery(self):
        self.assertFalse(client_plan(review(adapter()),review()['values'])['guide'])
        self.assertFalse(client_plan(review(adapter()),dict(NEW,proto='dhcp'))['guide'])

    def test_apply_must_be_sent_before_claim_and_single_concurrent_job(self):
        gate=RenewalGate(client_plan(review(adapter()),NEW));gate.start(time.monotonic()+90)
        self.assertTrue(gate.payload()['closed']);self.assertIsNone(gate.claim(adapter()['id']))
        gate.arm();self.assertIsNone(gate.claim('unrelated'))
        self.assertIsNotNone(gate.claim(adapter()['id']))
        self.assertIsNone(gate.claim(adapter()['id']));self.assertFalse(gate.seal());self.assertFalse(gate.ready())
        gate.finish_renewal('done');gate.close();self.assertFalse(gate.permit())

    def test_expired_request_does_not_release_adapter(self):
        gate=RenewalGate(client_plan(review(adapter()),NEW));gate.start(time.monotonic()+10);gate.arm()
        self.assertIsNone(gate.claim(adapter()['id']))

    def test_router_confirmation_does_not_require_client_address_gateway_dns(self):
        gate=RenewalGate(client_plan(review(adapter()),NEW));gate.start(time.monotonic()+90);gate.arm()
        fresh=adapter(addresses=['192.168.22.123/24'],gateways=['192.168.22.1'],dns=['192.168.22.1'])
        with patch('modules.platform_services.lan_network.inspect',side_effect=[dict(adapters=[adapter()]),dict(adapters=[fresh])]):
            self.assertTrue(gate.ready());self.assertTrue(gate.ready())

    def test_client_inventory_does_not_block_router_readiness(self):
        gate=RenewalGate(client_plan(review(adapter()),NEW));gate.start(time.monotonic()+90)
        fresh=adapter(id='other',addresses=['192.168.22.123/24'],gateways=['192.168.22.1'],dns=['192.168.22.1'])
        with patch('modules.platform_services.lan_network.inspect',return_value=dict(adapters=[fresh])):self.assertTrue(gate.ready())

    def test_restoration_resets_target_to_original_address(self):
        gate=RenewalGate(client_plan(review(adapter()),NEW));gate.start(time.monotonic()+90,restoring=True)
        with patch('modules.platform_services.lan_network.inspect',return_value=dict(adapters=[adapter()])):self.assertTrue(gate.ready())
        self.assertIn('192.168.11.1',gate.payload()['guide'])


class PlatformRenewalTests(unittest.TestCase):
    def test_windows_script_pins_guid_index_mac_dhcp_and_deadline(self):
        import base64
        from modules.platform_services import lan_network_windows as windows
        with patch.object(windows.subprocess,'run',return_value=Mock(returncode=0)) as run:
            windows.renew(adapter(),20,lambda:True)
        argv=run.call_args.args[0]
        script=base64.b64decode(argv[-1]).decode('utf-16-le')
        self.assertIn(adapter()['id'],script);self.assertIn('InterfaceIndex -ne 14',script)
        self.assertIn('0050562B292D',script);self.assertIn('!$nic.DHCPEnabled',script)
        self.assertLess(script.index('ReleaseDHCPLease'),script.index('RenewDHCPLease'));self.assertIn('RenewDHCPLease',script)
        self.assertGreaterEqual(script.count('ToUnixTimeSeconds() -ge'),2)

    def test_windows_expired_gate_does_not_spawn_process(self):
        from modules.platform_services import lan_network_windows as windows
        with patch.object(windows.subprocess,'run') as run:
            with self.assertRaises(RuntimeError):windows.renew(adapter(),20,lambda:False)
        run.assert_not_called()

    def test_linux_identity_change_never_disconnects(self):
        from modules.platform_services import lan_network_linux as linux
        with patch.object(linux,'inspect',return_value=[adapter(connection='changed')]),patch.object(linux,'command') as command:
            with self.assertRaises(RuntimeError):linux.renew(adapter(),20,lambda:True)
        command.assert_not_called()

    def test_linux_reactivates_only_same_profile_and_interface(self):
        from modules.platform_services import lan_network_linux as linux
        with patch.object(linux,'inspect',return_value=[adapter()]),patch.object(linux,'command',return_value='') as command:
            linux.renew(adapter(),20,lambda:True)
        calls=[c.args[0] for c in command.call_args_list]
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0][-6:],['connection','up','uuid','profile','ifname','Ethernet0'])

    def test_static_adapter_facade_rejects_before_os_call(self):
        from modules.platform_services.lan_network import renew
        with patch('modules.platform_services.lan_network_windows.renew') as execute:
            with self.assertRaises(RuntimeError):renew(adapter(dhcp=False),20)
        execute.assert_not_called()


class RecoveryUiTests(unittest.TestCase):
    def test_ambiguous_adapter_requires_selection_and_busy_disables_button(self):
        from tests.test_standalone import APP
        from PySide6.QtWidgets import QWidget
        from modules.main_pages.packet_capture.openwrt.lan_recovery_panel import LanRecoveryPanel
        parent=QWidget();panel=LanRecoveryPanel(parent)
        value=dict(guide='Guide',adapters=[adapter()],selection_required=True,can_renew=True)
        panel.update_payload(value);self.assertFalse(panel.button.isEnabled())
        panel.adapters.setCurrentIndex(1);self.assertTrue(panel.button.isEnabled())
        panel.update_payload(dict(value,busy=True));self.assertFalse(panel.button.isEnabled())
        parent.deleteLater()

    def test_progress_panel_renders_light_dark_and_scaled(self):
        from pathlib import Path
        from tests.test_standalone import APP
        from PySide6.QtWidgets import QWidget
        from modules.ui_scale_manager import UiScaleManager
        from modules.config_manager import ConfigManager
        from modules.dialog_theme import build_base_stylesheet
        from modules.main_pages.packet_capture.openwrt.router_task_progress import RouterTaskProgressDialog
        output=Path('build/lan-recovery-review');output.mkdir(parents=True,exist_ok=True)
        for light in (False,True):
            for percent in (100,150):
                parent=QWidget();parent.scale_manager=UiScaleManager(percent)
                with patch('modules.common_widgets.common_dialogs.build_current_base_stylesheet',side_effect=lambda config_manager,scale_manager:build_base_stylesheet(config_manager,light_theme=light,theme_name='light.qss' if light else 'dark.qss',scale_manager=scale_manager)):
                    dialog=RouterTaskProgressDialog('LAN',Mock(),parent)
                gate=RenewalGate(client_plan(review(adapter()),NEW));gate.start(time.monotonic()+90);gate.arm()
                dialog.update_progress(dict(configuration_apply=True,stage='LAN',deadline=time.monotonic()+80,network_recovery=gate.payload()))
                dialog.show();APP.processEvents()
                self.assertTrue(dialog._network_panel.button.isVisible())
                self.assertTrue(dialog.grab().save(str(output/(str(light)+'-'+str(percent)+'.png'))))
                dialog.finish();parent.deleteLater()


class BrokerLifecycleTests(unittest.TestCase):
    def test_owned_worker_stays_off_gui_and_late_completion_cannot_reopen_dialog(self):
        from PySide6.QtCore import Signal
        from PySide6.QtTest import QSignalSpy
        from tests.test_standalone import APP
        from tests.test_router_policy_worker import Owner,wait_for
        from modules.capture_services.lan_recovery_service import LanRecoveryService
        class Service(Owner):configurationProgressChanged=Signal(str,object)
        owner=Service(Mock(),Mock());broker=LanRecoveryService(owner);owner.lan_recovery=broker
        gate=RenewalGate(client_plan(review(adapter()),NEW));gate.start(time.monotonic()+90);gate.arm()
        owner._openwrt_workers['apply']=object()
        broker.observe('apply',dict(configuration_apply=True,network_recovery=gate.payload()))
        release=threading.Event();started=threading.Event();threads=[]
        def renew(row,timeout,permit):
            threads.append(threading.current_thread() is threading.main_thread());started.set()
            if not release.wait(3):raise RuntimeError('test did not release worker')
            return row
        spy=QSignalSpy(owner.activityChanged);events=[]
        owner.configurationProgressChanged.connect(lambda k,v:events.append(k) if k=="apply" else None)
        with patch('modules.platform_services.lan_network.renew',side_effect=renew):
            self.assertTrue(broker.request('apply',adapter()['id']))
            self.assertTrue(started.wait(2));self.assertFalse(broker.request('apply',adapter()['id']))
            self.assertFalse(gate.seal());broker.release('apply');before=len(events)
            owner._openwrt_workers.pop('apply');release.set();wait_for(spy,2)
            self.assertEqual(len(events),before)
        self.assertEqual(threads,[False]);self.assertFalse(owner._openwrt_workers)
        self.assertFalse(broker.offers);owner.deleteLater()

    def test_lua_transaction_stays_locked_through_apply_and_confirmation(self):
        from tests.test_checked_apply import CheckedApplyTests,run_checked,LuaRuntime
        from modules.capture_services.configuration_progress import reporting
        if not LuaRuntime:self.skipTest('Lua unavailable')
        device,channel,event,stage,verify=CheckedApplyTests().fixture()
        gate=RenewalGate(client_plan(review(adapter()),NEW));states=[]
        fresh=adapter(addresses=['192.168.22.123/24'],gateways=['192.168.22.1'],dns=['192.168.22.1'])
        def progress(value):
            payload=value.get('network_recovery')
            if payload:
                states.append(payload['closed'])
                if not payload['closed']:self.assertIn('checked_apply',channel.calls)
        with patch('modules.platform_services.lan_network.inspect',return_value=dict(adapters=[fresh])),reporting(progress):
            result=run_checked(channel,stage,verify,event,candidates=('192.168.22.1',),original_host='192.168.8.1',client_recovery=gate)
        self.assertEqual(states[:2],[True,True]);self.assertTrue(result['applied'])
        self.assertIn(('uci','confirm'),device.calls);self.assertTrue(gate.closed)
