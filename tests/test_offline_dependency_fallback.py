"""Customer tmux/librt failure: only an unfulfilled local plan may change source."""
import unittest
from threading import Event
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from tests import test_common_offline_bundle as bundle_tests
from modules.capture_services.openwrt.common_bundle_installer import install_from_local
from modules.capture_services.openwrt.dependency_resolution import UnsatisfiedDependency
from modules.capture_services.openwrt.installation_sources import install_with_sources
from modules.capture_services.openwrt.package_manager import PackageManager
from modules.capture_services.openwrt.signal_manager import SignalError


class MissingOfflineDependencyTests(unittest.TestCase):
    setUp = bundle_tests.CommonOfflineTests.setUp
    tearDown = bundle_tests.CommonOfflineTests.tearDown

    def prepare(self, requirement='librt', listing='libc - 1\n'):
        profile = self.repo.profile(self.f.device)
        row = next(row for row in profile['packages'] if row['name'] == 'tmux')
        row['depends'] = ['libc', requirement]
        manager = NS(command_runner=Mock(), _execute_stage=Mock(return_value=listing),
                     _install_offline=Mock())
        return manager, Mock()

    def local(self, manager, tell, cancel=None):
        return install_from_local(manager, ('tmux',), self.f.device, self.root / 'cache',
                                  True, cancel or Event(), tell, repository=self.repo)

    def test_missing_librt_reaches_online_before_any_local_transfer(self):
        manager, tell = self.prepare()
        online, computer = Mock(), Mock()
        source = install_with_sources(lambda: self.local(manager, tell), online, computer, tell)
        self.assertEqual(source, '路由器联网安装')
        online.assert_called_once_with()
        computer.assert_not_called()
        manager._install_offline.assert_not_called()
        messages = '\n'.join(call.args[0] for call in tell.call_args_list)
        self.assertIn('librt=<missing>', messages)
        self.assertIn('libc=1', messages)
        self.assertIn('2/3', messages)
        self.assertFalse(list((self.root / 'cache').glob('common-local-*')))

    def test_router_network_failure_continues_to_computer_route(self):
        manager, tell = self.prepare()
        online = Mock(side_effect=SignalError('offline_retry', 'DNS failure'))
        computer = Mock()
        source = install_with_sources(lambda: self.local(manager, tell), online, computer, tell)
        self.assertEqual(source, '电脑下载后传到路由器安装')
        online.assert_called_once_with()
        computer.assert_called_once_with()
        manager._install_offline.assert_not_called()

    def test_existing_librt_keeps_local_route(self):
        manager, tell = self.prepare(listing='libc - 1\nlibrt - 1.2.5-r4\n')
        online, computer = Mock(), Mock()
        install_with_sources(lambda: self.local(manager, tell), online, computer, tell)
        manager._install_offline.assert_called_once()
        online.assert_not_called()
        computer.assert_not_called()

    def test_version_mismatch_may_try_feed_but_never_forces_upgrade(self):
        manager, tell = self.prepare('libc (>= 2)')
        computer = Mock()
        def online():
            try:
                PackageManager('opkg').verify_plan('Upgrading libc on root from 1 to 2.', ('tmux',))
            except ValueError as error:
                raise SignalError('environment', str(error)) from error
        with patch('modules.capture_services.openwrt.common_bundle_installer.compare_opkg_versions', return_value=False):
            with self.assertRaises(SignalError):
                install_with_sources(lambda: self.local(manager, tell), online, computer, tell)
        computer.assert_not_called()
        manager._install_offline.assert_not_called()

    def test_malformed_requirement_and_kernel_dependency_do_not_fall_back(self):
        for requirement in ('librt ???', 'kmod-test'):
            with self.subTest(requirement=requirement):
                manager, tell = self.prepare(requirement)
                if requirement == 'kmod-test':
                    from tests.test_dependency_providers import package
                    index = {'tmux': package('tmux', depends=('kmod-test',)),
                             'kmod-test': package('kmod-test')}
                    from modules.capture_services.openwrt.dependency_resolution import resolve_packages
                    self.repo.ipk_selection = lambda *_: resolve_packages(index, ('tmux',), set())
                online, computer = Mock(), Mock()
                with self.assertRaises(SignalError):
                    install_with_sources(lambda: self.local(manager, tell), online, computer, tell)
                online.assert_not_called()
                computer.assert_not_called()
                manager._install_offline.assert_not_called()

    def test_invalid_inventory_does_not_become_missing_dependency(self):
        manager, tell = self.prepare(listing='not an opkg listing')
        online, computer = Mock(), Mock()
        with self.assertRaises(SignalError):
            install_with_sources(lambda: self.local(manager, tell), online, computer, tell)
        online.assert_not_called()
        computer.assert_not_called()

    def test_install_failure_never_restarts_another_route(self):
        manager, tell = self.prepare(listing='libc - 1\nlibrt - 1\n')
        manager._install_offline.side_effect = UnsatisfiedDependency('late-install-error')
        online, computer = Mock(), Mock()
        with self.assertRaises(SignalError):
            install_with_sources(lambda: self.local(manager, tell), online, computer, tell)
        online.assert_not_called()
        computer.assert_not_called()
        manager._install_offline.assert_called_once()

    def test_integrity_error_does_not_fall_back(self):
        manager, tell = self.prepare(listing='libc - 1\nlibrt - 1\n')
        profile = self.repo.profile(self.f.device)
        row = next(row for row in profile['packages'] if row['name'] == 'tmux')
        self.repo.object_path(row).write_bytes(b'corrupt')
        online, computer = Mock(), Mock()
        with self.assertRaises(SignalError):
            install_with_sources(lambda: self.local(manager, tell), online, computer, tell)
        online.assert_not_called()
        computer.assert_not_called()
        manager._install_offline.assert_not_called()

    def test_cancel_before_matching_never_falls_back(self):
        manager, tell = self.prepare()
        cancel = Event()
        cancel.set()
        online, computer = Mock(), Mock()
        with self.assertRaises(SignalError) as error:
            install_with_sources(lambda: self.local(manager, tell, cancel), online, computer, tell)
        self.assertEqual(error.exception.code, 'cancelled')
        online.assert_not_called()
        computer.assert_not_called()
        manager._execute_stage.assert_not_called()
