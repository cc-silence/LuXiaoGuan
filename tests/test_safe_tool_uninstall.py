"""Exact-package uninstall, shared components and transport safety regressions."""
from threading import Event
import unittest
from unittest.mock import Mock
from modules.capture_services.openwrt.command_runner import RemoteCommandResult
from modules.capture_services.openwrt.common_tool_dependencies import TOOL_BY_KEY
from modules.capture_services.openwrt.dependency_manager import OpenWrtDependencyManager
from modules.capture_services.openwrt.models import DependencyInfo, DependencyState
from modules.capture_services.openwrt.package_manager import PackageManager
from modules.capture_services.openwrt.signal_manager import SignalError
from modules.capture_services.openwrt.tool_uninstall import installed_tool_packages

END = '__REMOVAL_INVENTORY_END__\n'


def result(text, **kwargs):
    return RemoteCommandResult('inventory', kwargs.pop('exit_code', 0), text, **kwargs)


class InventoryTests(unittest.TestCase):
    def test_both_native_formats_select_exact_package_only(self):
        for pm, text in [('opkg', 'wpa-cli - 1-r1\nwpad-basic - 1-r1\n'),
                         ('apk', 'wpa-cli\nwpad-basic\n')]:
            runner = Mock()
            runner.run.return_value = result(text + END)
            self.assertEqual(installed_tool_packages(runner, PackageManager(pm),
                             ('wpa-cli',), Event()), ('wpa-cli',))
            command = runner.run.call_args.args[1]
            self.assertNotIn('remove', command)
            self.assertNotIn('del ', command)

    def test_provider_does_not_select_wireless_service(self):
        runner = Mock()
        runner.run.return_value = result('wpad-basic - 1-r1\n' + END)
        self.assertEqual(installed_tool_packages(runner, PackageManager('opkg'),
                         ('wpa-cli',), Event()), ())

    def test_truncated_invalid_failed_cancelled_inventory_never_proceeds(self):
        for response in [result('wpa-cli - 1-r1\n'), result('bad data\n' + END),
                         result(END, exit_code=1), result(END, cancelled=True),
                         result(END, timed_out=True)]:
            runner = Mock()
            runner.run.return_value = response
            with self.subTest(response=response), self.assertRaises(Exception):
                installed_tool_packages(runner, PackageManager('opkg'), ('wpa-cli',), Event())


class UninstallTests(unittest.TestCase):
    def manager(self, key, pm='opkg'):
        manager = OpenWrtDependencyManager(Mock())
        manager._preflight = Mock(return_value=PackageManager(pm))
        manager.check = Mock(return_value=(DependencyInfo(key, key, DependencyState.READY),))
        manager._execute_stage = Mock()
        def stage(name, command, *args):
            if name == 'dependency_package_plan':
                if pm == 'opkg':
                    return 'Removing package ' + command.split()[-1].strip("'") + ' from root...'
                return '(1/1) Purging ' + command.split()[-1].strip("'") + ' (1-r1)\nOK: 0 packages'
            return ''
        manager._execute_stage.side_effect = stage
        return manager

    def inventory(self, manager, names, pm='opkg', remaining=()):
        def listing(items):
            return ''.join(name + (' - 1-r1' if pm == 'opkg' else '') + '\n'
                           for name in items) + END
        manager.command_runner.run.side_effect = [result(listing(names)), result(listing(remaining))]

    def remove(self, manager, key, selected=None):
        return manager.uninstall_packages({key: True}, False, Event(), Mock(),
            list(TOOL_BY_KEY[key].package_names) if selected is None else selected,
            include_sftp=False)

    def test_wpa_cli_uninstalls_on_both_managers_with_native_preflight(self):
        for pm in ('opkg', 'apk'):
            manager = self.manager('wpa_cli', pm)
            self.inventory(manager, ('wpa-cli',), pm)
            self.remove(manager, 'wpa_cli')
            calls = manager._execute_stage.call_args_list
            self.assertEqual(len(calls), 2)
            self.assertIn('--noaction' if pm == 'opkg' else '--simulate', calls[0].args[1])
            self.assertNotIn('--force', calls[1].args[1])
            self.assertNotIn('--autoremove', calls[1].args[1])
            self.assertNotIn('wpad', calls[1].args[1])
            self.assertEqual(manager.command_runner.run.call_count, 2)

    def test_signal_libraries_removed_before_interpreter(self):
        manager = self.manager('signal')
        packages = TOOL_BY_KEY['signal'].package_names
        self.inventory(manager, packages)
        self.remove(manager, 'signal')
        actual = [call.args[0].removeprefix('dependency_uninstall_')
                  for call in manager._execute_stage.call_args_list
                  if call.args[0].startswith('dependency_uninstall_')]
        self.assertEqual(actual, list(reversed(packages)))

    def test_runtime_failure_does_not_prevent_removing_installed_package(self):
        manager = self.manager('wpa_cli')
        manager.check.return_value = (DependencyInfo('wpa_cli', 'wpa_cli', DependencyState.UNKNOWN),)
        self.inventory(manager, ('wpa-cli',))
        self.remove(manager, 'wpa_cli')
        self.assertEqual(manager._execute_stage.call_count, 2)

    def test_shared_package_rejection_stops_before_real_removal(self):
        manager = self.manager('signal')
        self.inventory(manager, TOOL_BY_KEY['signal'].package_names)
        manager._execute_stage.side_effect = SignalError('environment', 'has dependent packages')
        with self.assertRaises(SignalError):
            self.remove(manager, 'signal')
        self.assertEqual(manager._execute_stage.call_count, 1)
        self.assertEqual(manager._execute_stage.call_args.args[0], 'dependency_package_plan')

    def test_apk_automatic_cleanup_outside_target_stops(self):
        manager = self.manager('wpa_cli', 'apk')
        self.inventory(manager, ('wpa-cli',), 'apk')
        manager._execute_stage.side_effect = None
        manager._execute_stage.return_value = '(1/2) Purging wpa-cli (1-r1)\n(2/2) Purging libubus (1-r1)\nOK: 0 packages'
        with self.assertRaises(SignalError):
            self.remove(manager, 'wpa_cli')
        self.assertEqual(manager._execute_stage.call_count, 1)

    def test_unrelated_provider_preserved_without_change_command(self):
        manager = self.manager('wpa_cli')
        self.inventory(manager, ('wpad-basic',))
        self.remove(manager, 'wpa_cli')
        manager._execute_stage.assert_not_called()

    def test_installed_package_still_present_after_remove_is_failure(self):
        manager = self.manager('wpa_cli')
        self.inventory(manager, ('wpa-cli',), remaining=('wpa-cli',))
        with self.assertRaises(SignalError):
            self.remove(manager, 'wpa_cli')

    def test_unknown_target_is_rejected(self):
        manager = self.manager('signal')
        self.inventory(manager, TOOL_BY_KEY['signal'].package_names)
        with self.assertRaises(SignalError):
            self.remove(manager, 'signal', ['wpad-basic'])
        manager._execute_stage.assert_not_called()


class ToolUninstallUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests import test_standalone
        cls.standalone = test_standalone
        test_standalone.StandaloneTests.setUpClass()
        cls.window = test_standalone.StandaloneTests.window

    @classmethod
    def tearDownClass(cls):
        cls.standalone.StandaloneTests.tearDownClass()

    def test_real_buttons_confirm_and_submit_individual_package_names(self):
        from dataclasses import replace
        from pathlib import Path
        from unittest.mock import patch
        from modules.capture_services.openwrt.models import OpenWrtConnectionState
        from modules.common_widgets.common_dialogs import ConfirmDialog
        section = self.window.packet_capture_page.openwrt_tab.login_page.common_tools
        service = section.service
        owner = section.owner
        original = owner.openwrt_backend.snapshot
        output = Path('build/tool-uninstall-review')
        output.mkdir(parents=True, exist_ok=True)
        snapshot = replace(original, connection_state=OpenWrtConnectionState.CONNECTED,
            dependencies=tuple(DependencyInfo(key, key, DependencyState.READY)
                               for key in ('wpa_cli', 'signal')))
        owner.openwrt_backend.snapshot = snapshot
        try:
            for percent in (100, 125, 150, 175):
                for theme in ('dark.qss', 'light.qss'):
                    self.window.apply_appearance(percent, 'default', theme)
                    section.card.set_expanded(True, animate=False)
                    section.apply_snapshot(snapshot)
                    self.standalone.APP.processEvents()
                    for key in ('wpa_cli', 'signal'):
                        button = section.rows[key][1]
                        self.assertEqual(button.text(), '\u5378\u8f7d')
                        self.assertGreaterEqual(button.width(), button.fontMetrics().horizontalAdvance(button.text()))
                    section.card.grab().save(str(output / (theme + '-' + str(percent) + '.png')))
            for key in ('wpa_cli', 'signal'):
                with patch.object(service, 'ensure_available', return_value=True), \
                     patch.object(service, 'uninstall_async') as uninstall, \
                     patch('modules.main_pages.packet_capture.openwrt.common_tool_section.ConfirmDialog') as confirm:
                    confirm.Accepted = ConfirmDialog.Accepted
                    confirm.return_value.exec.return_value = ConfirmDialog.Accepted
                    section._clicked(key)
                    uninstall.assert_called_once_with({key: True}, list(TOOL_BY_KEY[key].package_names), scope=key)
                    uninstall.reset_mock()
                    confirm.return_value.exec.return_value = ConfirmDialog.Rejected
                    section._clicked(key)
                    uninstall.assert_not_called()
                    title, text, parent = confirm.call_args.args
                    dialog = ConfirmDialog(title, text, parent)
                    dialog.show()
                    self.standalone.APP.processEvents()
                    dialog.grab().save(str(output / (key + '-confirm.png')))
                    dialog.close()
                    dialog.deleteLater()
        finally:
            owner.openwrt_backend.snapshot = original
            section.apply_snapshot(original)
