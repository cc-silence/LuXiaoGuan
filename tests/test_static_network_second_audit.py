"""Second audit: subnet-mask transitions and configuration/live-state divergence."""
import copy
import tempfile
import threading
import unittest
from unittest.mock import patch,Mock
from tests.test_static_network import Device
from tests.test_lan_client_recovery import adapter,review,NEW
from modules.capture_services.lan_client_recovery import client_plan
from modules.platform_services.static_network import StaticMigration,migration_plan,StaticMigrationError
from modules.platform_services import static_network_linux as linux


class StrictDevice(Device):
    def write(self,before,after):
        ips=[v.split('/')[0] for v in after['addresses']]
        if len(set(ips))!=len(ips):raise RuntimeError('Same IP cannot be added twice with different masks')
        super().write(before,after)


class MaskChangeTests(unittest.TestCase):
    def test_mask_change_preserves_ip_and_does_not_add_duplicate_ip(self):
        for mask in ('255.255.255.128','255.255.0.0'):
            with self.subTest(mask=mask),tempfile.TemporaryDirectory() as folder:
                driver=StrictDevice();row=adapter(dhcp=False,dns=driver.state['dns'])
                new=dict(NEW,ipaddr='192.168.11.2',netmask=mask)
                plan=migration_plan(client_plan(review(row),new))
                tx=StaticMigration(plan,driver,folder);tx.prepare(threading.Event())
                self.assertEqual(driver.state['addresses'],['192.168.11.123/24'])
                tx.activate();tx.commit()
                self.assertEqual(driver.state['addresses'],['192.168.11.123/'+('25' if mask.endswith('128') else '16')])


class RuntimeReadbackTests(unittest.TestCase):
    def test_unchanged_profile_does_not_hide_different_live_addresses(self):
        driver=object.__new__(linux.Driver)
        state=dict(addresses=['192.168.11.123/24'],gateways=['192.168.11.1'],dns=['192.168.11.1'],metric=-1)
        driver.adapter=adapter(manager='networkmanager',dhcp=False)
        import time
        driver.deadline=time.monotonic()+25
        with patch.object(driver,'profile',return_value=copy.deepcopy(state)),patch.object(linux,'inspect',return_value=[adapter(manager='networkmanager',dhcp=False,addresses=['192.168.22.123/24'])]):
            plan=migration_plan(client_plan(review(adapter(dhcp=False)),NEW))
            with tempfile.TemporaryDirectory() as folder:
                tx=StaticMigration(plan,driver,folder);tx.baseline=copy.deepcopy(state);tx.expected=copy.deepcopy(state);tx._save()
                with self.assertRaises(StaticMigrationError):tx.rollback()
                self.assertTrue(tx.path.exists())


class LinuxMaskTests(unittest.TestCase):
    def test_mask_change_does_not_probe_its_own_existing_ip_as_a_duplicate(self):
        before=dict(addresses=['192.168.11.123/24'],gateways=['192.168.11.1'],dns=['192.168.11.1'],metric=-1)
        after=dict(before,addresses=['192.168.11.123/25'])
        row=adapter(dhcp=False,manager='networkmanager')
        driver=object.__new__(linux.Driver);driver.adapter=row
        import time
        driver.deadline=time.monotonic()+25;driver.query=Mock(return_value='')
        live_before=dict(row,**{k:before[k] for k in ('addresses','gateways','dns')})
        live_after=dict(row,**{k:after[k] for k in ('addresses','gateways','dns')})
        with patch.object(driver,'profile',side_effect=[before,after]),patch.object(linux,'inspect',side_effect=[[live_before],[live_after]]):
            driver.write(before,after)
        self.assertFalse(any(call.args[0][0]=='arping' for call in driver.query.call_args_list))


if __name__=='__main__':unittest.main()
