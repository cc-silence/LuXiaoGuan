"""Dependency retention continues independent removals and preserves safety stops."""
from threading import Event
from types import SimpleNamespace as NS
from unittest.mock import Mock
import unittest
from modules.capture_services.openwrt.command_runner import RemoteCommandResult
from modules.capture_services.openwrt.dependency_manager import OpenWrtDependencyManager
from modules.capture_services.openwrt.package_manager import PackageManager
from modules.capture_services.openwrt.signal_manager import SignalError


class PartialUninstallTests(unittest.TestCase):
    def exercise(self, pm='opkg', failure='blocked', *, blocked_at='plan', targets=('iw', 'curl'), fail_package=None):
        current=set(targets);cancel=Event();commands=[];messages=Mock()
        fail_package=fail_package or targets[0]
        ssh=NS(connected=True,_client=object())
        runner=NS(ssh_client=ssh)
        manager=OpenWrtDependencyManager(runner)
        manager._preflight=Mock(return_value=PackageManager(pm))
        manager.check=Mock(return_value=())
        def response(name,stdout='',stderr='',exit_code=0,**flags):
            return RemoteCommandResult(name,exit_code,stdout,stderr,**flags)
        def run(name,command,**kwargs):
            commands.append((name,command))
            if name=='dependency_removal_inventory':
                if failure=='inventory_failed' and sum(n==name for n,_ in commands)>1:
                    return response(name,exit_code=1)
                text=''.join(n+(' - 1-r1' if pm=='opkg' else '')+'\n' for n in sorted(current))
                return response(name,text+'__REMOVAL_INVENTORY_END__\n')
            package=command.split()[-1].strip("'")
            plan=name=='dependency_package_plan'
            if package==fail_package and (plan if blocked_at=='plan' else not plan):
                if failure=='cancel':return response(name,cancelled=True)
                if failure=='timeout':return response(name,timed_out=True)
                if failure=='disconnect':ssh.connected=False
                if failure=='replacement':ssh._client=object()
                if failure in ('blocked','disconnect','replacement','cancel_event'):
                    if failure=='cancel_event':cancel.set()
                    if pm=='apk' and plan:
                        return response(name,'World updated, but the following packages are not removed due to:\n  iw: driver\nOK: 10 MiB in 8 packages')
                    return response(name,stderr='Package iw is depended upon by packages:\ndriver\n',exit_code=1)
                if failure=='lock':return response(name,stderr='Could not lock database\nPackage iw is depended upon by packages:\ndriver',exit_code=1)
                if failure=='permission':return response(name,stderr='Permission denied',exit_code=1)
                if failure in ('eperm','io'):
                    reason='Operation not permitted' if failure=='eperm' else 'Input/output error'
                    return response(name,stderr='Package iw is depended upon by packages:\n driver\n'+reason,exit_code=1)
                if failure=='extra_plan':return response(name,'(1/2) Purging iw (1-r1)\n(2/2) Purging driver (1-r1)\nOK: 0 packages')
                if failure=='unknown_plan':return response(name,'unrecognized output')
            if plan:
                return response(name,'Removing package '+package+' from root...' if pm=='opkg' else '(1/1) Purging '+package+' (1-r1)\nOK: 0 packages')
            if failure!='still_installed':current.discard(package)
            return response(name)
        runner.run=run
        error=None
        try:manager.uninstall_packages({},False,cancel,messages,list(targets),include_sftp=False)
        except Exception as exc:error=exc
        return error,commands,current,messages,manager

    def test_both_managers_skip_occupied_first_package_and_remove_second(self):
        for pm in ('opkg','apk'):
            error,commands,current,messages,manager=self.exercise(pm)
            self.assertEqual(error.code,'partial_uninstall')
            self.assertEqual(current,{'iw'})
            self.assertTrue(any(n=='dependency_uninstall_curl' for n,_ in commands))
            self.assertFalse(any(n=='dependency_uninstall_iw' for n,_ in commands))
            self.assertIn('curl',str(error));self.assertIn('iw',str(error))
            self.assertIn('driver',error.package_diagnostic)
            self.assertEqual(error.checked_dependencies,())
            manager.check.assert_called_once()

    def test_execution_time_dependency_change_also_preserves_and_continues(self):
        error,commands,current,_,_=self.exercise(blocked_at='execute')
        self.assertEqual(error.code,'partial_uninstall');self.assertEqual(current,{'iw'})
        self.assertTrue(any(n=='dependency_uninstall_curl' for n,_ in commands))

    def test_unknown_failures_and_connection_or_cancel_stop_following_removals(self):
        for pm in ('opkg','apk'):
            for failure in ('cancel','timeout','disconnect','replacement','cancel_event','lock','permission','eperm','io','unknown_plan'):
                with self.subTest(pm=pm,failure=failure):
                    error,commands,current,_,_=self.exercise(pm,failure)
                    self.assertIsNotNone(error)
                    self.assertNotEqual(getattr(error,'code',''),'partial_uninstall')
                    self.assertFalse(any(n=='dependency_uninstall_curl' for n,_ in commands))
                    self.assertEqual(current,{'iw','curl'})

    def test_apk_removal_of_unselected_package_stops_before_mutation(self):
        error,commands,_,_,_=self.exercise('apk','extra_plan')
        self.assertIsNotNone(error)
        self.assertFalse(any(n.startswith('dependency_uninstall_') for n,_ in commands))

    def test_success_is_verified_and_unexpected_remaining_package_is_failure(self):
        for pm in ('opkg','apk'):
            error,commands,current,messages,_=self.exercise(pm,'none')
            self.assertIsNone(error);self.assertFalse(current)
            self.assertEqual(sum(n=='dependency_removal_inventory' for n,_ in commands),2)
            self.assertEqual(messages.call_args_list[-2].args[1],'success')
            error,_,current,_,_=self.exercise(pm,'still_installed')
            self.assertIsNotNone(error);self.assertEqual(current,{'iw','curl'})

    def test_preserved_only_batch_is_not_reported_as_uninstalled(self):
        error,commands,current,_,_=self.exercise(targets=('iw',))
        self.assertEqual(error.code,'partial_uninstall');self.assertEqual(current,{'iw'})
        self.assertFalse(any(n.startswith('dependency_uninstall_') for n,_ in commands))

    def test_service_preserves_verified_snapshot_for_partial_uninstall(self):
        from tests.test_dependency_group_controls import GroupTaskTests,info
        owner,service=GroupTaskTests().service((info('iw'),))
        checked=(info('iw'),)
        error=SignalError('partial_uninstall','removed curl; retained iw')
        error.checked_dependencies=checked
        service.manager.uninstall_packages=Mock(side_effect=error)
        self.assertTrue(service.uninstall_async({},['iw'],scope='iw'))
        operation,event=owner.pending
        result=operation(event,Mock())
        self.assertEqual(result['checked'],checked)
        self.assertEqual(result['error'],str(error))
        service._complete(service.task.session_id,result)
        self.assertFalse(owner.is_busy)

    def test_later_failure_keeps_earlier_attempts_without_claiming_verified_success(self):
        for pm in ('opkg','apk'):
            for failure in ('permission','cancel','timeout','disconnect','replacement'):
                with self.subTest(pm=pm,failure=failure):
                    error,commands,current,_,_=self.exercise(pm,failure,targets=('curl','iw'),fail_package='iw')
                    self.assertIsNotNone(error);self.assertEqual(current,{'iw'})
                    self.assertIn('curl',str(error));self.assertIn('iw',str(error))
                    self.assertIn('\u5c1a\u672a\u5b8c\u6210\u6700\u7ec8\u6838\u5bf9',str(error))
                    self.assertIn('curl',error.package_diagnostic)
                    self.assertNotEqual(getattr(error,'code',''),'partial_uninstall')

    def test_final_inventory_failure_keeps_attempted_package_names(self):
        for pm in ('opkg','apk'):
            error,_,current,_,_=self.exercise(pm,'inventory_failed')
            self.assertIsNotNone(error);self.assertFalse(current)
            self.assertIn('curl',str(error));self.assertIn('iw',str(error))
            self.assertEqual(error.code,'environment')

    def test_fatal_native_errors_override_dependency_refusal(self):
        from modules.capture_services.openwrt.package_manager import removal_dependency_blocked,package_error_reason
        for reason in ('Operation not permitted', 'Input/output error'):
            with self.subTest(reason=reason):
                self.assertFalse(removal_dependency_blocked('Package iw is depended upon by packages:\n driver\n' + reason))
                self.assertEqual(package_error_reason('Package iw is depended upon by packages:\n driver\n'+reason),package_error_reason(reason))

    def test_cancel_or_replaced_connection_during_final_inventory_never_reports_success(self):
        from modules.capture_services.openwrt.dependency_batch_uninstall import remove_packages
        for mode in ('cancel','disconnect','replacement'):
            cancel=Event();ssh=NS(connected=True,_client=object())
            runner=NS(ssh_client=ssh)
            manager=NS(command_runner=runner,_package_change=Mock(return_value='remove'),_execute_stage=Mock())
            tell=Mock()
            def inventory(*args,**kwargs):
                if mode=='cancel':cancel.set()
                elif mode=='disconnect':ssh.connected=False
                else:ssh._client=object()
                return RemoteCommandResult('inventory',0,'__REMOVAL_INVENTORY_END__\n')
            runner.run=inventory
            with self.subTest(mode=mode),self.assertRaises(SignalError) as caught:
                remove_packages(manager,PackageManager('opkg'),('curl',),cancel,tell)
            self.assertIn(caught.exception.code,('cancelled','disconnected'))
            self.assertFalse(any(call.args[1]=='success' for call in tell.call_args_list))

    def test_apk_retention_notice_cannot_bypass_an_unselected_removal(self):
        pm=PackageManager('apk')
        plan='World updated, but the following packages are not removed due to:\n iw: driver\n(1/1) Purging driver (1-r1)\nOK: 0 packages'
        from modules.capture_services.openwrt.package_manager import RemovalRetained
        with self.assertRaises(ValueError) as caught:pm.verify_plan(plan,('iw',),remove=True)
        self.assertNotIsInstance(caught.exception,RemovalRetained)
