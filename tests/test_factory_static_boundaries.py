"""Additional factory static boundaries, with deterministic local fake drivers."""
import copy
import json
import tempfile
import threading
import unittest
from unittest.mock import patch
from modules.capture_services.lan_client_recovery import factory_reset_plan
from modules.platform_services.static_network import StaticMigration,StaticMigrationError
from modules.platform_services.local_static_recovery import recover
from tests.test_factory_static_recovery import inventory,DEFAULTS
from tests.test_static_network import Device


class FactoryStaticBoundaryTests(unittest.TestCase):
    def test_same_ip_prefix_changes_use_one_final_write(self):
        for old_prefix,new_mask in ((16,'255.255.255.0'),(24,'255.255.255.128')):
            with self.subTest(old_prefix=old_prefix,new_mask=new_mask),tempfile.TemporaryDirectory() as folder:
                driver=Device();driver.state['addresses']=['192.168.11.123/'+str(old_prefix)]
                state=inventory(addresses=driver.state['addresses'])
                defaults=dict(DEFAULTS,address='192.168.11.1',netmask=new_mask)
                plan=factory_reset_plan(state,defaults)['static_migration']
                tx=StaticMigration(plan,driver,folder)
                with patch('modules.platform_services.local_static_recovery.StaticMigration',return_value=tx):
                    result=recover(plan,threading.Event(),lambda:True)
                self.assertEqual(result['addresses'],[plan['new_address']])
                self.assertEqual(len(driver.writes),1)
                self.assertFalse(tx.path.exists());self.assertFalse(tx.router_confirmed)

    def test_journal_storage_failure_precedes_any_network_write(self):
        with tempfile.TemporaryDirectory() as folder:
            driver=Device();before=driver.read()
            plan=factory_reset_plan(inventory(),DEFAULTS)['static_migration']
            tx=StaticMigration(plan,driver,folder)
            with patch('modules.platform_services.local_static_recovery.StaticMigration',return_value=tx),patch.object(tx,'_save',side_effect=OSError('Disk unavailable')):
                with self.assertRaises(OSError):recover(plan,threading.Event(),lambda:True)
            self.assertEqual(driver.state,before);self.assertFalse(driver.writes)

    def test_corrupt_or_foreign_journal_cannot_change_network(self):
        for saved in ([],None,{'schema':1,'plan':None},{'schema':1,'plan':{'adapter':{}}}):
            with self.subTest(saved=saved),tempfile.TemporaryDirectory() as folder:
                driver=Device();before=driver.read()
                plan=factory_reset_plan(inventory(),DEFAULTS)['static_migration']
                tx=StaticMigration(plan,driver,folder)
                raw=json.dumps(saved);tx.path.write_text(raw,encoding='utf-8')
                with patch('modules.platform_services.local_static_recovery.StaticMigration',return_value=tx):
                    with self.assertRaises(StaticMigrationError):recover(plan,threading.Event(),lambda:True)
                self.assertEqual(driver.state,before);self.assertFalse(driver.writes)
                self.assertEqual(tx.path.read_text(encoding='utf-8'),raw)

    def test_external_gateway_edit_after_prepare_is_retained(self):
        with tempfile.TemporaryDirectory() as folder:
            driver=Device();plan=factory_reset_plan(inventory(),DEFAULTS)['static_migration']
            tx=StaticMigration(plan,driver,folder);prepare=tx.prepare
            def changed(cancel):
                prepare(cancel);driver.state['gateways']=[]
            tx.prepare=changed
            with patch('modules.platform_services.local_static_recovery.StaticMigration',return_value=tx):
                with self.assertRaises(StaticMigrationError):recover(plan,threading.Event(),lambda:True)
            self.assertEqual(driver.state['gateways'],[])
            self.assertEqual(len(driver.writes),1)
            self.assertTrue(tx.path.exists())

    def test_cancelled_or_expired_request_never_constructs_driver(self):
        for cancelled,timeout in ((True,60),(False,0)):
            with self.subTest(cancelled=cancelled):
                cancel=threading.Event()
                if cancelled:cancel.set()
                with patch('modules.platform_services.local_static_recovery.StaticMigration') as factory:
                    with self.assertRaises(InterruptedError):recover({},cancel,lambda:True,timeout=timeout)
                factory.assert_not_called()


if __name__=='__main__':unittest.main()
