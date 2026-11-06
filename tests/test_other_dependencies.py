"""Other features reuse dependency tasks and network installation boundaries."""
from dataclasses import replace
from threading import Event
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import unittest
from tests.test_dependency_group_controls import APP, ViewOwner, info
from tests.test_dependency_diagnostic_paths import Owner
from PySide6.QtWidgets import QWidget
from modules.capture_services.dependency_service import OpenWrtDependencyService
from modules.capture_services.openwrt.other_tool_dependencies import OTHER_TOOLS, OTHER_KEYS
from modules.capture_services.openwrt.tool_catalog import check_tools
from modules.capture_services.openwrt.dependency_groups import REQUIRED_COMMON_KEYS
from modules.capture_services.openwrt.dependency_batch_install import install_packages
from modules.capture_services.openwrt.installation_sources import install_with_sources
from modules.capture_services.openwrt.signal_manager import SignalError
from modules.main_pages.packet_capture.openwrt.common_tool_section import CommonToolSection

class OtherDependencyTests(unittest.TestCase):
    def test_shared_rows_show_function_and_package_without_offline_controls(self):
        owner=ViewOwner(); parent=QWidget()
        section=CommonToolSection(parent,owner,other=True)
        self.addCleanup(parent.deleteLater); self.addCleanup(section.dispose)
        self.assertIsNone(section.offline_controls)
        self.assertEqual(set(section.rows),set(OTHER_KEYS))
        for tool in OTHER_TOOLS:
            self.assertIn(tool.title,section.names[tool.key].text())
            self.assertIn('\uff08'+tool.package+'\uff09',section.names[tool.key].text())
            self.assertTrue(section.names[tool.key].text().endswith('\uff08'+tool.summary+'\uff09'))
        self.assertFalse(OTHER_KEYS & REQUIRED_COMMON_KEYS)
        owner.dependency_service.check_other_async=Mock()
        section.install_all_button.click()
        owner.dependency_service.check_other_async.assert_called_once_with()

    def test_multi_package_check_requires_both_backend_and_ui(self):
        runner=Mock();runner.ssh_client.connected=True
        runner.run.return_value=NS(exit_code=0,stdout='__OPKG__\nbase-files - 1\nluci-app-ttyd - 1\n__PACKAGES_END__\n',cancelled=False,timed_out=False)
        row=check_tools(runner,('other_terminal',))[0]
        self.assertEqual(row.state.value,'missing');self.assertEqual(row.missing_packages,('ttyd',))
        runner.run.return_value.stdout='__APK__\nbase-files\nluci-app-ttyd\nttyd\n__PACKAGES_END__\n'
        self.assertEqual(check_tools(runner,('other_terminal',))[0].state.value,'ready')
        runner.run.return_value.stdout='__OPKG__\ntruncated'
        self.assertEqual(check_tools(runner,('other_terminal',))[0].state.value,'unknown')

    def test_network_route_falls_back_to_computer_but_never_to_bundle(self):
        manager=Mock();manager.command_runner.ssh_client.connected=True
        manager.command_runner.ssh_client._client=object()
        installed=set()
        def checked(**kwargs):return (info('other_files','ready' if installed else 'missing',() if installed else ('luci-app-filebrowser',)),)
        manager._missing_packages=lambda rows: tuple(p for row in rows for p in row.missing_packages)
        manager._install_online.side_effect=SignalError('offline_retry','network unavailable')
        manager._install_offline.side_effect=lambda *args:installed.add('luci-app-filebrowser')
        with patch('modules.capture_services.openwrt.common_bundle_installer.install_from_local') as bundle:
            rows=install_packages(manager,checked,NS(),'.',False,Event(),Mock(),allow_local_bundle=False)
        bundle.assert_not_called();manager._install_online.assert_called_once();manager._install_offline.assert_called_once()
        self.assertEqual(rows[0].state.value,'ready')

    def test_integrity_failure_does_not_switch_routes(self):
        online=Mock(side_effect=SignalError('environment','signature mismatch'));computer=Mock()
        with self.assertRaises(SignalError):install_with_sources(None,online,computer,Mock())
        computer.assert_not_called()

    def test_task_passes_only_selected_feature_and_disables_prebuilt_bundles(self):
        owner=Owner();service=OpenWrtDependencyService(owner)
        row=info('other_files')
        service.manager.install_all=Mock(return_value=(row,))
        with patch('modules.capture_services.dependency_service.check_tools',return_value=(row,)):
            self.assertTrue(service.install_async({'scan':True},scope='other_files'))
            operation,event=owner.pending
            result=operation(event,Mock())
        call=service.manager.install_all.call_args
        self.assertEqual(call.args[0],{'other_files':True})
        self.assertFalse(call.kwargs['include_sftp']);self.assertFalse(call.kwargs['allow_local_bundle'])
        self.assertFalse(result['error'])
        service._complete(service.task.session_id,result)
        self.assertEqual(owner.openwrt_backend.snapshot.dependencies[-1].key,'other_files')

    def test_check_missing_is_success_but_unknown_preserves_reason(self):
        for state, detail in [('missing',''),('unknown','package database unavailable')]:
            with self.subTest(state=state):
                owner=Owner();service=OpenWrtDependencyService(owner)
                rows=tuple(replace(info(tool.key,state,tool.package_names if state=='missing' else ()),detail=detail)
                           for tool in OTHER_TOOLS)
                with patch('modules.capture_services.dependency_service.check_tools',return_value=rows):
                    self.assertTrue(service.check_other_async())
                    operation,event=owner.pending;result=operation(event,Mock())
                self.assertEqual(bool(result['error']),state=='unknown')
                if state=='unknown':self.assertIn(detail,result['error'])
                session=service.task.session_id
                finished=Mock();service.taskFinished.connect(finished)
                service._complete(session,result)
                self.assertEqual(finished.call_args.args[1],state=='missing')
                self.assertEqual({row.state.value for row in owner.openwrt_backend.snapshot.dependencies},{state})

    def test_bulk_other_scope_checks_without_installing(self):
        owner=Owner();service=OpenWrtDependencyService(owner)
        service.manager.install_all=Mock()
        rows=tuple(info(tool.key) for tool in OTHER_TOOLS)
        with patch('modules.capture_services.dependency_service.check_tools',return_value=rows):
            self.assertTrue(service.check_other_async())
            operation,event=owner.pending; result=operation(event,Mock())
        service.manager.install_all.assert_not_called();self.assertFalse(result['error'])
        service._complete(service.task.session_id,result)
        with self.assertRaises(ValueError):service.install_async({},scope='other_check')

if __name__=='__main__':unittest.main()
