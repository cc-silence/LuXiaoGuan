"""Factory recovery shares LAN planning and CAS writes; no real network changes."""
import copy
from pathlib import Path as PathType
import tempfile
import threading
import time
import unittest
from unittest.mock import patch,Mock
from types import SimpleNamespace as NS
from tests.test_lan_client_recovery import adapter
from tests.test_static_network import Device
from modules.capture_services.lan_client_recovery import factory_reset_plan,RenewalGate
from modules.capture_services.openwrt.factory_reset_guidance import read_factory_defaults
from modules.platform_services.static_network import StaticMigration,StaticMigrationError
from modules.platform_services.local_static_recovery import recover


DEFAULTS=dict(address='192.168.1.1',netmask='255.255.255.0',source='rom')


def inventory(**changes):
    row=adapter(dhcp=False,renewable=False,dns=['192.168.11.1','1.1.1.1'])
    row.update(changes)
    return dict(adapters=[row],peer='192.168.11.1',selection_source='ssh_socket')


class FactoryStaticPlanTests(unittest.TestCase):
    def test_uses_same_lan_address_and_dns_mapping(self):
        before=inventory();untouched=copy.deepcopy(before)
        plan=factory_reset_plan(before,DEFAULTS)
        self.assertEqual(before,untouched)
        self.assertEqual(plan['static_migration']['new_address'],'192.168.1.123/24')
        self.assertTrue(plan['adapters'][0]['static_recovery'])
        self.assertFalse(plan['selection_required'])

    def test_unknown_mask_source_multiple_routes_overlap_and_conflict_stop(self):
        cases=[]
        cases.append((inventory(),dict(address='192.168.1.1')))
        cases.append((inventory(),dict(DEFAULTS,source='standard')))
        unknown=inventory();unknown['selection_source']='system_route';cases.append((unknown,DEFAULTS))
        duplicate=inventory();duplicate['adapters'].append(dict(duplicate['adapters'][0],id='other'));cases.append((duplicate,DEFAULTS))
        overlap=inventory();overlap['adapters'].append(adapter(id='other',route=False,addresses=['192.168.1.99/24']));cases.append((overlap,DEFAULTS))
        cases.append((inventory(addresses=['192.168.11.1/24']),DEFAULTS))
        for state,defaults in cases:
            with self.subTest(state=state,defaults=defaults):
                plan=factory_reset_plan(state,defaults)
                self.assertNotIn('static_migration',plan)
                self.assertFalse(any(r.get('static_recovery') for r in plan['adapters']))

    def test_static_cannot_be_claimed_before_restart_or_automatically(self):
        plan=factory_reset_plan(inventory(),DEFAULTS);gate=RenewalGate(plan)
        gate.start(time.monotonic()+90)
        identity=plan['adapters'][0]['id']
        self.assertIsNone(gate.claim(identity))
        gate.arm(automatic=True);self.assertIsNone(gate.take_automatic())
        self.assertEqual(gate.claim(identity)[0]['id'],identity)
        self.assertIsNone(gate.claim(identity))

    def test_dhcp_path_is_unchanged(self):
        state=inventory(dhcp=True,renewable=True)
        expected = factory_reset_plan(state)
        expected['new_addresses'] = ['192.168.1.1/24']
        self.assertEqual(expected, factory_reset_plan(state, DEFAULTS))


class FactoryDefaultsMaskTests(unittest.TestCase):
    def test_cidr_legacy_unknown_and_conflicting_masks(self):
        cases=[('address=10.1.2.1/24\nsource=rom','255.255.255.0'),
               ('address=10.1.2.1\nnetmask=255.255.0.0\nsource=rom','255.255.0.0'),
               ('address=10.1.2.1\nsource=rom',''),
               ('address=10.1.2.1/24\nnetmask=255.255.0.0\nsource=rom',''),
               ('address=10.1.2.1/24 10.2.3.1/24\nsource=rom',''),
               ('source=standard','255.255.255.0')]
        for raw,expected in cases:
            with self.subTest(raw=raw):
                defaults=read_factory_defaults(NS(_run=Mock(return_value=raw)),threading.Event())
                self.assertEqual(defaults['netmask'],expected)


class FactoryStaticTransactionTests(unittest.TestCase):
    def fixture(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        driver=Device();plan=factory_reset_plan(inventory(),DEFAULTS)['static_migration']
        tx=StaticMigration(plan,driver,folder.name)
        return driver,plan,tx

    def test_verified_local_completion_preserves_external_dns_without_claiming_router_identity(self):
        driver,plan,tx=self.fixture()
        with patch('modules.platform_services.local_static_recovery.StaticMigration',return_value=tx):
            result=recover(plan,threading.Event(),lambda:True)
        self.assertEqual(driver.state['addresses'],['192.168.1.123/24'])
        self.assertEqual(driver.state['gateways'],['192.168.1.1'])
        self.assertEqual(driver.state['dns'],['192.168.1.1','1.1.1.1'])
        self.assertTrue(result['local_settings_updated']);self.assertFalse(tx.router_confirmed)
        self.assertTrue(tx.committed);self.assertFalse(tx.path.exists())

    def test_failed_final_write_restores_exact_original_state(self):
        driver,plan,tx=self.fixture();before=driver.read();write=driver.write;count=[0]
        def fail_once(old,new):
            count[0]+=1;write(old,new)
            if count[0]==3:raise RuntimeError('Interrupted local completion')
        driver.write=fail_once
        with patch('modules.platform_services.local_static_recovery.StaticMigration',return_value=tx):
            with self.assertRaises(RuntimeError):recover(plan,threading.Event(),lambda:True)
        self.assertEqual(driver.state,before);self.assertFalse(tx.path.exists())

    def test_external_edits_are_not_overwritten(self):
        driver,plan,tx=self.fixture();driver.state['dns']=['8.8.8.8'];before=driver.read()
        with patch('modules.platform_services.local_static_recovery.StaticMigration',return_value=tx):
            with self.assertRaises(StaticMigrationError):recover(plan,threading.Event(),lambda:True)
        self.assertEqual(driver.state,before);self.assertFalse(driver.writes)

    def test_persistent_receipt_cleanup_failure_does_not_mark_restored_pc_completed(self):
        import json
        driver,plan,tx=self.fixture();before=driver.read()
        with patch('modules.platform_services.local_static_recovery.StaticMigration',return_value=tx), \
             patch.object(PathType,'unlink',side_effect=OSError('Receipt locked')):
            with self.assertRaises(StaticMigrationError):recover(plan,threading.Event(),lambda:True)
        self.assertEqual(driver.state,before)
        saved=json.loads(tx.path.read_text(encoding='utf-8'))
        self.assertFalse(saved['local_confirmed'])
        self.assertEqual(saved['expected'],before)
        next_tx=StaticMigration(plan,driver,tx.folder)
        next_tx.prepare(threading.Event());next_tx.rollback()
        self.assertEqual(driver.state,before);self.assertFalse(next_tx.path.exists())

    def test_next_lan_change_reconciles_verified_local_journal(self):
        from modules.capture_services.lan_client_recovery import client_plan
        from modules.platform_services.static_network import migration_plan
        driver,plan,tx=self.fixture();tx.prepare(threading.Event());tx.activate()
        with patch.object(PathType,'unlink',side_effect=OSError('Receipt cleanup interrupted')):
            with self.assertRaises(OSError):tx.commit_local()
        self.assertTrue(tx.path.exists());self.assertTrue(tx.local_confirmed)
        row=adapter(dhcp=False,renewable=False,addresses=driver.state['addresses'],
                    gateways=driver.state['gateways'],dns=driver.state['dns'])
        review=dict(values=dict(proto='static',ipaddr='192.168.1.1',netmask='255.255.255.0'),
                    client_network=dict(adapters=[row],selection_source='ssh_socket'))
        wanted=dict(proto='static',ipaddr='192.168.22.1',netmask='255.255.255.0')
        next_tx=StaticMigration(migration_plan(client_plan(review,wanted)),driver,tx.folder)
        next_tx.prepare(threading.Event());next_tx.rollback()
        self.assertFalse(next_tx.path.exists())

    def test_cancel_after_preparation_rolls_back(self):
        driver,plan,tx=self.fixture();before=driver.read();allowed=iter([True,False])
        with patch('modules.platform_services.local_static_recovery.StaticMigration',return_value=tx):
            with self.assertRaises(InterruptedError):recover(plan,threading.Event(),lambda:next(allowed))
        self.assertEqual(driver.state,before)


class FactoryStaticWorkerTests(unittest.TestCase):
    def test_closing_offer_during_static_prepare_restores_and_releases_job(self):
        from tests.test_factory_reset_recovery import RecoveryOwner,APP
        from modules.capture_services.lan_recovery_service import LanRecoveryService
        from modules.capture_services.openwrt.operation_diagnostics import OperationDiagnostics
        owner=RecoveryOwner(NS(),NS(operation_diagnostics=OperationDiagnostics()))
        broker=owner.lan_recovery=LanRecoveryService(owner)
        plan=factory_reset_plan(inventory(),DEFAULTS)
        key=broker.create_offer(plan);driver=Device();baseline=driver.read()
        started=threading.Event();resume=threading.Event();write=driver.write;first=[True]
        def hold_after_alias(before,after):
            write(before,after)
            if first[0]:
                first[0]=False;started.set();resume.wait(2)
        driver.write=hold_after_alias
        try:
            with tempfile.TemporaryDirectory() as folder:
                tx=StaticMigration(plan['static_migration'],driver,folder)
                with patch('modules.platform_services.local_static_recovery.StaticMigration',return_value=tx):
                    self.assertTrue(broker.request(key,plan['adapters'][0]['id']))
                    self.assertTrue(started.wait(1))
                    broker.release(key);resume.set()
                    deadline=time.monotonic()+3
                    while owner._openwrt_workers and time.monotonic()<deadline:
                        APP.processEvents();time.sleep(.01)
                self.assertFalse(owner._openwrt_workers);self.assertFalse(broker.adapter_jobs)
                self.assertEqual(driver.state,baseline);self.assertFalse(tx.path.exists())
                self.assertFalse(broker.offers)
        finally:
            resume.set();broker.release(key);owner.deleteLater()

    def test_failed_static_action_mentions_the_actual_static_button(self):
        from tests.test_factory_reset_recovery import RecoveryOwner,APP
        from modules.capture_services.lan_recovery_service import LanRecoveryService
        from modules.capture_services.openwrt.operation_diagnostics import OperationDiagnostics
        owner=RecoveryOwner(NS(),NS(operation_diagnostics=OperationDiagnostics()))
        owner.lan_recovery=LanRecoveryService(owner)
        broker=owner.lan_recovery;plan=factory_reset_plan(inventory(),DEFAULTS)
        key=broker.create_offer(plan)
        try:
            with patch('modules.platform_services.local_static_recovery.recover',side_effect=RuntimeError('test')):
                self.assertTrue(broker.request(key,plan['adapters'][0]['id']))
                deadline=time.monotonic()+3
                while owner._openwrt_workers and time.monotonic()<deadline:
                    APP.processEvents();time.sleep(.01)
            payload=broker.payload(key)
            self.assertFalse(payload['busy']);self.assertTrue(payload['can_renew'])
            self.assertIn('\u8c03\u6574\u56fa\u5b9a IP',payload['message'])
            self.assertNotIn('\u91cd\u65b0\u83b7\u53d6\u7f51\u7edc\u5730\u5740',payload['message'])
        finally:
            broker.release(key);owner.deleteLater()

    def test_checkbox_then_click_uses_static_worker_not_dhcp(self):
        from tests.test_factory_reset_recovery import RecoveryOwner,APP
        from modules.capture_services.lan_recovery_service import LanRecoveryService
        from modules.main_pages.packet_capture.openwrt.factory_reset_result_dialog import FactoryResetResultDialog
        from modules.capture_services.openwrt.operation_diagnostics import OperationDiagnostics
        owner=RecoveryOwner(NS(),NS(operation_diagnostics=OperationDiagnostics()))
        owner.lan_recovery=LanRecoveryService(owner)
        result=dict(note='Factory defaults',factory_login=DEFAULTS,outcome='reconnect')
        dialog=FactoryResetResultDialog(result,inventory(),owner)
        try:
            self.assertFalse(dialog.panel.button.isEnabled())
            self.assertEqual(dialog.panel.adapters.count(),1)
            self.assertIn('IP',dialog.panel.button.text())
            key=dialog.offer;identity=dialog.panel.adapters.currentData()
            with patch('modules.platform_services.local_static_recovery.recover') as static,patch('modules.platform_services.lan_network.renew') as dhcp, patch(
                    'modules.platform_services.lan_network.inspect', return_value=dict(inventory(), error='')):
                static.return_value=adapter(dhcp=False,renewable=False,addresses=['192.168.1.123/24'],local_settings_updated=True)
                self.assertFalse(owner.lan_recovery.request(key,identity))
                dialog.ready.setChecked(True)
                deadline=time.monotonic()+3
                while owner._openwrt_workers and time.monotonic()<deadline:
                    APP.processEvents();time.sleep(.01)
                self.assertFalse(owner._openwrt_workers)
                self.assertTrue(dialog.panel.button.isEnabled())
                dialog.panel.button.click()
                deadline=time.monotonic()+3
                while owner._openwrt_workers and time.monotonic()<deadline:
                    APP.processEvents();time.sleep(.01)
                self.assertFalse(owner._openwrt_workers)
                static.assert_called_once();dhcp.assert_not_called()
                self.assertTrue(owner.lan_recovery.payload(key)['message'])
                self.assertFalse(dialog.panel.button.isVisible())
        finally:
            dialog.reject();dialog.deleteLater();owner.deleteLater()


if __name__=='__main__':unittest.main()
