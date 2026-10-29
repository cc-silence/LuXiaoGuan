"""The automatic policy includes actual app requirements, never optional LuCI tools."""
from dataclasses import replace
from threading import Event
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock,patch
from modules.capture_services.openwrt.dependency_groups import REQUIRED_COMMON_ITEMS, REQUIRED_COMMON_KEYS, COMMON_KEYS, CAPTURE_KEYS
from modules.capture_services.openwrt.common_tool_dependencies import CommonTool, REQUIRED_TOOL_KEYS, check_tools
from modules.capture_services.openwrt.models import DependencyState
from modules.capture_services.openwrt.command_runner import RemoteCommandResult
from modules.capture_services.openwrt.runtime_helper_dependencies import check_wireless_rpc
from tests.test_login_dependency_prompt import LoginDependencyPromptTests, rows
from modules.main_pages.packet_capture.openwrt.login_dependency_prompt import LoginDependencyPrompt


class RequiredDependencyTests(unittest.TestCase):
    def test_only_real_function_requirements_are_in_automatic_scope(self):
        self.assertFalse(CommonTool('extra','Extra','extra','Optional by default').required)
        self.assertEqual(REQUIRED_COMMON_KEYS,{'sftp','iw','wpa_cli','signal','curl','wireless_rpc','hcxdumptool'})
        self.assertTrue({'terminal','iwinfo','luci_commands','luci_base_zh','luci_commands_zh'}.isdisjoint(REQUIRED_COMMON_KEYS))

    def test_required_checks_do_not_read_optional_package_inventory(self):
        def run(name,command,**kwargs):
            if name=='dependency_common_packages':raise AssertionError('Optional package inventory was read')
            return RemoteCommandResult(name,0,'READY')
        runner=NS(run=Mock(side_effect=run),ssh_client=NS(connected=True))
        with patch('modules.capture_services.openwrt.common_tool_dependencies.check_signal_support',return_value=NS(key='signal')), \
             patch('modules.capture_services.openwrt.runtime_helper_dependencies.check_wireless_rpc',return_value=NS(key='wireless_rpc')):
            result=check_tools(runner,REQUIRED_TOOL_KEYS,Event())
        self.assertEqual({r.key for r in result},REQUIRED_TOOL_KEYS)
        self.assertTrue(all(call.args[0]!='dependency_common_packages' for call in runner.run.call_args_list))

    def test_rpc_capability_distinguishes_missing_packages_from_stopped_or_builtin_interface(self):
        cases=(('rpcd=1\niwinfo=1\nmodule=0\n','ready',()),
               ('rpcd=0\niwinfo=0\nmodule=0\n','missing',('rpcd','rpcd-mod-iwinfo')),
               ('rpcd=1\niwinfo=0\nmodule=1\n','unknown',()),
               ('partial','unknown',()))
        for output,state,packages in cases:
            runner=NS(run=Mock(return_value=RemoteCommandResult('check',0,output)),ssh_client=NS(connected=True))
            row=check_wireless_rpc(runner,Event())
            self.assertEqual((row.state.value,row.missing_packages),(state,packages))
            command=runner.run.call_args.args[1]
            for forbidden in ('opkg install','apk add','restart','reload'):
                self.assertNotIn(forbidden,command)


class RequiredLoginConsentTests(LoginDependencyPromptTests):
    def test_hcx_missing_prompts_even_when_pmkid_is_disabled(self):
        owner,page=self.create(False);factory=self.factory(True)
        owner.openwrt_backend.snapshot=replace(owner.openwrt_snapshot,
            dependencies=tuple(replace(r,state=DependencyState.MISSING,missing_packages=('hcxdumptool',))
                               if r.key=='hcxdumptool' else r for r in owner.openwrt_snapshot.dependencies))
        owner.dependency_service.install_async=Mock(return_value=True)
        prompt=LoginDependencyPrompt(page);self.addCleanup(prompt.dispose);self.settle()
        factory.assert_called_once()
        self.assertIn('hcxdumptool',factory.call_args.args[1])
        owner.dependency_service.install_async.assert_called_once()
        self.assertFalse(owner.dependency_service.install_async.call_args.args[0]['pmkid'])

    def test_optional_only_missing_never_prompts_or_installs(self):
        owner,page=self.create(False);factory=self.factory(True)
        optional=COMMON_KEYS-REQUIRED_COMMON_KEYS
        owner.openwrt_backend.snapshot=replace(owner.openwrt_snapshot,
            dependencies=tuple(replace(r,state=DependencyState.MISSING,missing_packages=('optional-package',))
                               if r.key in optional else r for r in owner.openwrt_snapshot.dependencies))
        owner.dependency_service.install_async=Mock()
        prompt=LoginDependencyPrompt(page);self.addCleanup(prompt.dispose);self.settle()
        factory.assert_not_called();owner.dependency_service.install_async.assert_not_called()

    def test_login_can_prompt_with_only_required_results_present(self):
        owner,page=self.create();factory=self.factory(True)
        owner.openwrt_backend.snapshot=replace(owner.openwrt_snapshot,
            dependencies=tuple(r for r in owner.openwrt_snapshot.dependencies if r.key in REQUIRED_COMMON_KEYS | CAPTURE_KEYS))
        owner.dependency_service.install_async=Mock(return_value=True)
        prompt=LoginDependencyPrompt(page);self.addCleanup(prompt.dispose);self.settle()
        factory.assert_called_once();owner.dependency_service.install_async.assert_called_once()

    def test_execution_excludes_optional_even_when_stale_snapshot_reports_them_missing(self):
        owner,page=self.create();service=owner.dependency_service
        optional=COMMON_KEYS-REQUIRED_COMMON_KEYS
        returned=tuple(replace(r,state=DependencyState.MISSING,missing_packages=('optional-package',))
                       if r.key in optional else r for r in rows(False))
        service.manager.install_all=Mock(return_value=returned)
        service.install_async(dict(scan=True,handshake=True,wps=True,password=True,pmkid=False),scope='login_all')
        operation,event=owner.pending;result=operation(event,Mock())
        self.assertFalse(result['error'],result['error'])
        features=service.manager.install_all.call_args.args[0]
        self.assertTrue(features['hcxdumptool'])
        self.assertFalse(features['pmkid'])
        self.assertTrue(optional.isdisjoint(features))
        self.assertTrue(optional.isdisjoint(service.task.items))
        self.assertTrue(optional.isdisjoint(r.key for r in result['checked']))
        service._complete(service.task.session_id,result)


class OptionalToolUiTests(unittest.TestCase):
    def test_optional_tools_are_labeled_and_not_treated_as_failed_login_checks(self):
        from PySide6.QtWidgets import QWidget
        from tests.test_dependency_group_controls import ViewOwner
        from modules.main_pages.packet_capture.openwrt.common_tool_section import CommonToolSection
        from tests.test_standalone import APP
        owner=ViewOwner()
        owner.openwrt_snapshot=replace(owner.openwrt_snapshot,
            dependencies=tuple(r for r in owner.openwrt_snapshot.dependencies if r.key in REQUIRED_COMMON_KEYS | CAPTURE_KEYS))
        parent=QWidget();section=CommonToolSection(parent,owner)
        self.addCleanup(parent.deleteLater);self.addCleanup(section.dispose)
        for key in ('terminal','iwinfo','luci_commands','luci_base_zh','luci_commands_zh'):
            self.assertIn('\u53ef\u9009',section.names[key].text())
            self.assertIn('\u672a\u68c0\u67e5',section.rows[key][0].text())
            self.assertEqual(section.rows[key][1].text(),'\u68c0\u67e5')
        self.assertIn('\u5fc5\u9700',section.install_all_button.text())
