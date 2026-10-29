"""Mixed login requests reuse the same verified routes as manual groups."""
from threading import Event
from types import SimpleNamespace as NS
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from modules.capture_services.openwrt.dependency_manager import OpenWrtDependencyManager
from modules.capture_services.openwrt.models import DependencyInfo, DependencyState
from modules.capture_services.openwrt.bundle_specs import COMMON_ROOTS, CAPTURE_ROOTS
from modules.capture_services.openwrt.installation_sources import package_batches
from modules.capture_services.openwrt.signal_manager import SignalError


class MixedDependencyInstallTests(unittest.TestCase):
    def run_install(self, local, *, online=None, computer=None, cancel=None):
        manager = OpenWrtDependencyManager(Mock())
        installed = set()
        wanted = ('aircrack-ng', 'reaver', 'wpa-cli')
        def check(*args):
            return tuple(DependencyInfo(name, name,
                DependencyState.READY if name in installed else DependencyState.MISSING,
                () if name in installed else (name,)) for name in wanted)
        manager.check = Mock(side_effect=check)
        manager._install_online = Mock(side_effect=lambda batch,*args: online(batch,installed) if online else self.fail('Unexpected online install'))
        manager._install_offline = Mock(side_effect=lambda batch,*args: computer(batch,installed) if computer else self.fail('Unexpected computer download'))
        event = cancel or Event()
        calls = []
        def install(_manager,batch,*args):
            calls.append(tuple(batch))
            return local(batch,installed,event)
        with TemporaryDirectory() as folder, patch('modules.capture_services.openwrt.common_bundle_installer.install_from_local',side_effect=install):
            result = manager.install_all({}, True, NS(), folder, event, Mock(), include_sftp=False)
        self.assertTrue(all(row.state == DependencyState.READY for row in result))
        return calls, manager

    def test_feedback_mixed_request_uses_both_local_bundles_without_network(self):
        def local(batch,installed,event):
            self.assertTrue(set(batch).issubset(COMMON_ROOTS) or set(batch).issubset(CAPTURE_ROOTS))
            installed.update(batch)
            return True
        calls, manager = self.run_install(local)
        self.assertEqual(calls,[('aircrack-ng','reaver'),('wpa-cli',)])
        manager._install_online.assert_not_called()
        manager._install_offline.assert_not_called()

    def test_missing_one_local_bundle_only_falls_back_for_that_group(self):
        def local(batch,installed,event):
            if 'wpa-cli' in batch:return False
            installed.update(batch)
            return True
        def online(batch,installed):
            self.assertEqual(batch,('wpa-cli',))
            raise SignalError('offline_retry','router network unavailable')
        def computer(batch,installed):
            self.assertEqual(batch,('wpa-cli',))
            installed.update(batch)
        _, manager = self.run_install(local,online=online,computer=computer)
        self.assertEqual(manager._install_online.call_count,1)
        self.assertEqual(manager._install_offline.call_count,1)

    def test_dependency_supplied_by_previous_group_is_not_reinstalled(self):
        def local(batch,installed,event):
            installed.update(('aircrack-ng','reaver','wpa-cli'))
            return True
        calls,_ = self.run_install(local)
        self.assertEqual(calls,[('aircrack-ng','reaver')])

    def test_cancellation_between_groups_never_starts_second_install(self):
        calls=[]
        def local(batch,installed,event):
            calls.append(tuple(batch));installed.update(batch);event.set()
            return True
        with self.assertRaises(SignalError) as raised:
            self.run_install(local)
        self.assertEqual(raised.exception.code,'cancelled')
        self.assertEqual(calls,[('aircrack-ng','reaver')])

    def test_safety_failure_does_not_continue_to_another_group_or_route(self):
        calls=[]
        def local(batch,installed,event):
            calls.append(tuple(batch))
            raise SignalError('environment','signature mismatch')
        with self.assertRaisesRegex(SignalError,'signature mismatch'):
            self.run_install(local)
        self.assertEqual(calls,[('aircrack-ng','reaver')])

    def test_single_bundle_and_unknown_packages_keep_exact_scope(self):
        for names in (('aircrack-ng','iw'),('wpa-cli','iw'),('hcxdumptool',)):
            self.assertEqual(package_batches(names),(names,))
        batches=package_batches(('aircrack-ng','wpa-cli','iw','other','aircrack-ng'))
        self.assertEqual(batches,(('aircrack-ng','iw'),('wpa-cli',),('other',)))
        self.assertEqual(package_batches(()),())
