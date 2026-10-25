"""Cross-version installation and removal regressions grounded in native sources."""
from pathlib import Path
from threading import Event
from unittest.mock import Mock, patch
import unittest
from modules.capture_services.openwrt.signal_manager import RouterSignalManager, SignalError
from modules.capture_services.openwrt.package_manager import PackageManager


class SignalSpaceTests(unittest.TestCase):
    def test_small_install_uses_plan_size_instead_of_fixed_one_mb_gate(self):
        for pm in ('opkg', 'apk'):
            manager = RouterSignalManager(Mock())
            before = dict(lua=False, uci=True, ubus=True, jsonc=True, root=True,
                          opkg=pm == 'opkg', apk=pm == 'apk', overlay_kb=512)
            after = dict(before, lua=True)
            manager.probe_dependencies = Mock(side_effect=[before, after])
            plan = ('Installing lua (1) to root...' if pm == 'opkg'
                    else '(1/1) Installing lua (1-r1)\nOK: 10 MiB in 5 packages')
            manager._execute_stage = Mock(return_value=plan)
            with patch('modules.capture_services.openwrt.package_space.check_plan_space') as space:
                self.assertEqual(manager.install_dependencies(Event()), after)
                space.assert_called_once()
            self.assertEqual(manager._execute_stage.call_args.args[0], 'router_signal_install')

    def test_actual_plan_space_failure_still_prevents_install(self):
        manager = RouterSignalManager(Mock())
        manager.probe_dependencies = Mock(return_value=dict(lua=False, uci=True, ubus=True,
            jsonc=True, root=True, opkg=True, apk=False, overlay_kb=512))
        manager._execute_stage = Mock(return_value='Installing lua (1) to root...')
        with patch('modules.capture_services.openwrt.package_space.check_plan_space',
                   side_effect=SignalError('environment', 'not enough space')):
            with self.assertRaises(SignalError):
                manager.install_dependencies(Event())
        self.assertNotIn('router_signal_install', [c.args[0] for c in manager._execute_stage.call_args_list])


class SignalTransferTests(unittest.TestCase):
    def test_pc_downloaded_ipk_installs_without_sftp(self):
        from modules.capture_services.openwrt.dependency_manager import OpenWrtDependencyManager
        runner = Mock()
        runner.ssh_client.open_sftp.side_effect = OSError('SFTP unavailable')
        manager = RouterSignalManager(runner)
        manager._execute_stage = Mock(return_value='Installing lua (1) to root...')
        manager._execute = Mock(return_value='')
        manager.probe_dependencies = Mock(return_value=dict(lua=True, uci=True, ubus=True, jsonc=True))
        with patch.object(OpenWrtDependencyManager, '_exec_push') as push, \
             patch.object(OpenWrtDependencyManager, '_preflight', return_value=PackageManager('opkg')), \
             patch('modules.capture_services.openwrt.package_space.ipk_package_versions', return_value={'lua':'1'}), \
             patch('modules.capture_services.openwrt.package_space.ipk_installed_size', return_value=64):
            manager._install_ipk_paths([Path('lua_1_all.ipk')], Event())
        self.assertEqual(push.call_count, 1)
        self.assertEqual(push.call_args.args[:2], ('lua_1_all.ipk', '/tmp/winbox-signal-pkgs/lua_1_all.ipk'))
        self.assertIn('router_signal_offline_install', [c.args[0] for c in manager._execute_stage.call_args_list])

    def test_pc_downloaded_apk_uses_same_fallback(self):
        from modules.capture_services.openwrt.dependency_manager import OpenWrtDependencyManager
        runner = Mock()
        runner.ssh_client.open_sftp.side_effect = OSError('SFTP unavailable')
        manager = RouterSignalManager(runner)
        manager.probe_dependencies = Mock(return_value=dict(lua=True, uci=True, ubus=True, jsonc=True))
        def construct(*args, **kwargs):
            kwargs['uploader']('lua.apk', '/tmp/winbox-apk/base/lua.apk')
            return Mock()
        with patch.object(OpenWrtDependencyManager, '_exec_push') as push, \
             patch('modules.capture_services.openwrt.apk_offline.ApkOfflineInstaller', side_effect=construct):
            manager._install_offline_apk(Mock(), 'cache', Event())
        self.assertEqual(push.call_count, 1)

    def test_cancelled_upload_does_not_open_sftp_or_push(self):
        from modules.capture_services.openwrt.dependency_manager import OpenWrtDependencyManager
        manager = OpenWrtDependencyManager(Mock())
        manager._sftp_upload = Mock()
        manager._exec_push = Mock()
        cancel = Event()
        upload = manager._package_uploader(True, cancel, Mock())
        cancel.set()
        with self.assertRaises(SignalError):
            upload('lua.ipk', '/tmp/winbox-signal-pkgs/lua.ipk')
        manager._sftp_upload.assert_not_called()
        manager._exec_push.assert_not_called()


class ApkRetainedPackageTests(unittest.TestCase):
    def test_native_success_without_requested_deletion_is_rejected(self):
        # APK del can return zero while keeping a required package. Running the
        # real transaction would still change /etc/apk/world in this case.
        native = ('World updated, but the following packages are not removed due to:\n'
                  '  libuci-lua: luci-base\nOK: 32 MiB in 120 packages')
        with self.assertRaises(ValueError):
            PackageManager('apk').verify_plan(native, ('libuci-lua',), remove=True)

    def test_partial_delete_plan_cannot_remove_another_target_pin(self):
        with self.assertRaises(ValueError):
            PackageManager('apk').verify_plan(
                '(1/1) Purging lua (5.1-r1)\nOK: 32 MiB in 120 packages',
                ('lua', 'libuci-lua'), remove=True)

    def test_noop_install_and_complete_removal_keep_existing_behavior(self):
        PackageManager('apk').verify_plan('OK: 32 MiB in 120 packages', ('lua',))
        PackageManager('apk').verify_plan(
            '(1/1) Purging lua (5.1-r1)\nOK: 32 MiB in 120 packages', ('lua',), remove=True)

    def test_retained_package_never_reaches_real_del(self):
        from modules.capture_services.openwrt.dependency_manager import OpenWrtDependencyManager
        manager = OpenWrtDependencyManager(Mock())
        manager._execute_stage = Mock(return_value='OK: 32 MiB in 120 packages')
        with self.assertRaises(SignalError):
            manager._package_change(PackageManager('apk'), ('libuci-lua',), Event(), remove=True)
        self.assertEqual(manager._execute_stage.call_count, 1)
        self.assertIn('--simulate', manager._execute_stage.call_args.args[1])


class NativeInventoryTests(unittest.TestCase):
    def test_opkg_optional_description_is_not_version(self):
        from modules.capture_services.openwrt.signal_package_feed import parse_installed
        installed = parse_installed('libuci-lua - 2020-01-01-1 - Lua bindings - UCI\n'
                                    'lua - 5.1.5-9 - \n')
        self.assertEqual(installed.versions['libuci-lua'], '2020-01-01-1')
        self.assertEqual(installed.versions['lua'], '5.1.5-9')

    def test_described_installed_version_satisfies_native_dependency(self):
        from modules.capture_services.openwrt.signal_package_feed import parse_installed, FeedPackage
        from modules.capture_services.openwrt.dependency_resolution import resolve_packages
        from scripts.openwrt_packages.opkg_version import satisfies
        installed = parse_installed('libuci-lua - 2020-01-01-1 - Lua bindings for UCI\n')
        root = FeedPackage('tool', '1', 'tool.ipk', 'a'*64, 1, 'https://example.invalid/tool',
                           ('libuci-lua (>= 2020-01-01)',))
        result = resolve_packages({'tool':root}, ('tool',), installed, satisfies)
        self.assertEqual([p.name for p in result], ['tool'])

    def test_uninstall_and_common_ui_accept_native_descriptions(self):
        from modules.capture_services.openwrt.command_runner import RemoteCommandResult
        from modules.capture_services.openwrt.tool_uninstall import installed_tool_packages
        from modules.capture_services.openwrt.common_tool_dependencies import check_tools
        from modules.capture_services.openwrt.models import DependencyState
        runner = Mock()
        runner.run.return_value = RemoteCommandResult('inventory', 0,
            'luci-app-commands - git-2025 - LuCI commands - web interface\n__REMOVAL_INVENTORY_END__\n')
        self.assertEqual(installed_tool_packages(runner, PackageManager('opkg'),
                         ('luci-app-commands',), Event()), ('luci-app-commands',))
        runner.run.return_value = RemoteCommandResult('inventory', 0,
            '__OPKG__\nluci-app-commands - git-2025 - LuCI commands\n__PACKAGES_END__\n')
        self.assertEqual(check_tools(runner, ('luci_commands',), Event())[0].state, DependencyState.READY)

    def test_invalid_listing_does_not_get_reported_as_missing(self):
        from modules.capture_services.openwrt.package_inventory import parse_package_listing
        for pm in ('opkg', 'apk'):
            with self.assertRaises(ValueError):
                parse_package_listing('ERROR: package database unreadable', pm)
