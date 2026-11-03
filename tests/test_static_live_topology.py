"""Changing another adapter after preview must never bypass shared guards."""
import json
import tempfile
import threading
import unittest
from unittest.mock import patch
from modules.platform_services.static_network import StaticMigration,StaticMigrationError
from modules.platform_services.static_network_topology import verify
from modules.platform_services.local_static_recovery import recover
from modules.platform_services import static_network_windows as windows,static_network_linux as linux
from tests.test_static_network import Device
from tests.test_factory_static_recovery import inventory,DEFAULTS
from modules.capture_services.lan_client_recovery import factory_reset_plan


class LiveTopologyTests(unittest.TestCase):
    def plan(self):return factory_reset_plan(inventory(),DEFAULTS)['static_migration']

    def test_current_other_adapter_overlap_and_missing_original_stop(self):
        plan=self.plan();selected=plan['adapter']
        verify(plan,[selected])
        other=dict(selected,id='other',addresses=['192.168.1.2/24'])
        with self.assertRaises(StaticMigrationError):verify(plan,[selected,other])
        verify(plan,[selected,dict(other,connected=False)])
        with self.assertRaises(StaticMigrationError):verify(plan,[other])

    def test_overlap_after_preview_stops_before_any_local_write(self):
        plan=self.plan();driver=Device();before=driver.read()
        driver.verify_topology=lambda value:verify(value,[plan['adapter'],dict(plan['adapter'],id='other',addresses=['192.168.1.99/24'])])
        with tempfile.TemporaryDirectory() as folder:
            tx=StaticMigration(plan,driver,folder)
            with patch('modules.platform_services.local_static_recovery.StaticMigration',return_value=tx):
                with self.assertRaises(StaticMigrationError):recover(plan,threading.Event(),lambda:True)
            self.assertEqual(driver.state,before);self.assertFalse(driver.writes)
            self.assertFalse(tx.path.exists())

    def test_overlap_after_alias_preparation_restores_baseline(self):
        plan=self.plan();driver=Device();before=driver.read()
        def check(value):
            rows=[plan['adapter']]
            if driver.writes:rows.append(dict(plan['adapter'],id='other',addresses=['192.168.1.99/24']))
            verify(value,rows)
        driver.verify_topology=check
        with tempfile.TemporaryDirectory() as folder:
            tx=StaticMigration(plan,driver,folder)
            with patch('modules.platform_services.local_static_recovery.StaticMigration',return_value=tx):
                with self.assertRaises(StaticMigrationError):recover(plan,threading.Event(),lambda:True)
            self.assertEqual(driver.state,before);self.assertFalse(tx.path.exists())

    def test_windows_guard_reads_all_adapters(self):
        plan=self.plan();driver=windows.Driver(plan['adapter'])
        with patch('modules.platform_services.lan_network_windows.inspect',return_value=[plan['adapter']]) as read:
            driver.verify_topology(plan)
        self.assertNotIn('adapter_id',read.call_args.kwargs)

    def test_linux_guard_uses_one_address_query_without_per_adapter_nmcli(self):
        plan=self.plan();plan['adapter']['id']='14'
        driver=object.__new__(linux.Driver)
        rows=[dict(ifindex=14,flags=['UP'],addr_info=[dict(family='inet',local='192.168.11.123',prefixlen=24)])]
        with patch.object(driver,'query',return_value=json.dumps(rows)) as query:
            driver.verify_topology(plan)
        query.assert_called_once_with(['ip','-j','-4','address','show'])


if __name__=='__main__':unittest.main()
