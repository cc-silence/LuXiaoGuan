"""LAN force retry eligibility, native Lua path, and user consent regressions."""
import copy
import json
import threading
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock,patch
from tests import test_checked_apply as base
from modules.capture_services.openwrt.force_apply import ApplyRestored,apply_forced,connection_error
from modules.capture_services.openwrt.checked_apply import apply_common,run_checked
from modules.capture_services.openwrt.common_settings import read_settings
from modules.capture_services.openwrt.operation_diagnostics import OperationDiagnostics
from modules.capture_services.lan_force_retry import LanForceRetry
from modules.capture_services.openwrt.ssh_client import HostKeyChangedError
from modules.capture_services.openwrt.signal_manager import SignalError


class ForceDevice(base.ApplyDevice):
    def __init__(self,legacy=False):
        super().__init__(legacy);self.native=[]
    def read(self,command):
        if command.startswith('ubus -v list uci'):return '"commit" "apply" "confirm" "rollback"'
        return super().read(command)
    def call(self,obj,method,arguments):
        args=self.unwrap(arguments)
        if obj=='uci' and method=='commit':
            self.calls.append((obj,method));self.native.append((method,args))
            if self.fail==(obj,method):return None,6
            if self.applied:return None,6
            self.config[args['config']]=copy.deepcopy(self.sessions[args['ubus_rpc_session']][args['config']])
            self.files['/etc/config/'+args['config']]=json.dumps(self.config[args['config']],sort_keys=True)
            return self.wrap({}),None
        if obj=='uci' and method=='apply' and args.get('rollback') is False:
            self.calls.append((obj,method));self.native.append((method,args))
            return None,5
        return super().call(obj,method,arguments)


class ForceExecutionTests(unittest.TestCase):
    def fixture(self,legacy=False):
        self.assertIsNotNone(base.LuaRuntime)
        device=ForceDevice(legacy);channel=base.TestChannel(device);event=threading.Event()
        review=read_settings(device,'network',event)
        values=dict(review['values'],auto='1',ipaddr='192.168.22.1',gateway='',broadcast='')
        manager=NS(runner=NS(operation_diagnostics=OperationDiagnostics()))
        settings=dict(host='192.168.8.1',port=22,user='root')
        return device,channel,event,review,values,manager,settings
    def execute(self,parts):
        device,channel,event,review,values,manager,settings=parts
        with patch('modules.capture_services.openwrt.checked_apply.ApplyChannel',return_value=channel):
            return apply_forced(manager,review,values,event,Mock(),settings,'private-password','PIN')
    def test_force_uses_one_native_commit_then_apply_without_rollback_on_both_layouts(self):
        for legacy in (False,True):
            parts=self.fixture(legacy);device,channel,_,_,values,manager,_=parts;before=copy.deepcopy(device.config)
            result=self.execute(parts)
            self.assertTrue(result['saved']);self.assertFalse(result['applied']);self.assertEqual(result['outcome'],'reconnect')
            self.assertEqual([m for m,a in device.native],['commit','apply'])
            self.assertIs(device.native[-1][1]['rollback'],False);self.assertIsNone(device.applied)
            self.assertEqual(device.config['network']['lan']['ipaddr'],values['ipaddr'])
            for config in before:
                if config!='network':self.assertEqual(device.config[config],before[config])
            for action in ('checked_apply','checked_confirm','checked_rollback'):self.assertNotIn(action,channel.calls)
            self.assertFalse(device.sessions)
            data=manager.runner.operation_diagnostics.snapshot()
            self.assertTrue(data['configuration'][-1]['force_apply']);self.assertNotIn('private-password',json.dumps(data))
    def test_lost_reply_does_not_repeat_commit_destroy_live_session_or_restore(self):
        parts=self.fixture();device,channel=parts[:2];original=channel.call
        def call(action,**values):
            result=original(action,**values)
            if action=='common_force_apply':raise OSError('reply lost')
            return result
        channel.call=call;result=self.execute(parts)
        self.assertEqual(result['outcome'],'uncertain');self.assertFalse(result['applied']);self.assertFalse(result['saved'])
        self.assertEqual(channel.calls.count('common_force_apply'),1)
        self.assertEqual(device.config['network']['lan']['ipaddr'],'192.168.22.1')
        self.assertNotIn('checked_rollback',channel.calls);self.assertNotIn('discard_session',channel.calls)
    def test_changed_configuration_and_cancellation_stop_before_writes(self):
        for mode in ('changed','cancel'):
            parts=self.fixture();device,channel,event=parts[:3]
            if mode=='changed':device.files['/etc/config/network']+=' '
            else:event.set()
            with self.assertRaises(RuntimeError if mode=='changed' else InterruptedError):self.execute(parts)
            self.assertFalse(device.native);self.assertNotIn('common_force_apply',channel.calls)
    def test_cancel_after_staging_discards_private_session(self):
        parts=self.fixture();device,channel,event=parts[:3];original=channel.call
        def call(action,**values):
            result=original(action,**values)
            if action=='common_stage':event.set()
            return result
        channel.call=call
        with self.assertRaises(InterruptedError):self.execute(parts)
        self.assertFalse(device.native);self.assertFalse(device.sessions)
    def test_edit_between_staging_and_force_commit_never_overwrites_new_config(self):
        parts=self.fixture();device,channel=parts[:2];original=channel.call
        def call(action,**values):
            result=original(action,**values)
            if action=='common_stage':device.files['/etc/config/network']+=' '
            return result
        channel.call=call;result=self.execute(parts)
        self.assertEqual(result['outcome'],'uncertain');self.assertFalse(device.native)
        self.assertNotIn('checked_rollback',channel.calls)
    def test_normal_permission_failure_never_produces_force_offer(self):
        parts=self.fixture();device,channel,event,review,values,manager,settings=parts
        manager.ssh=NS(_client=object(),_host=settings['host'],_port=22,_user='root',host_fingerprint='PIN')
        device.fail=('uci','apply')
        with patch('modules.capture_services.openwrt.checked_apply.ApplyChannel',return_value=channel):
            with self.assertRaises(SignalError):apply_common(manager,review,values,event,Mock(),settings,'secret','PIN')
        self.assertNotIn('checked_rollback',channel.calls);self.assertFalse(device.native)

    def test_service_declines_force_without_verified_failure_offer(self):
        from tests.test_router_policy_worker import Owner
        from modules.capture_services.router_management_service import RouterManagementService
        ssh=NS(connected=True,_client=object(),host_fingerprint='PIN')
        owner=Owner(ssh,Mock());service=RouterManagementService(owner)
        try:
            with self.assertRaises(RuntimeError):service.submit('common_save',apply=True,force=True,force_token='fabricated')
            self.assertFalse(service.queue);self.assertIsNone(service.current)
        finally:owner.deleteLater()


class RetryEligibilityTests(unittest.TestCase):
    def test_only_transport_failure_with_verified_restore_offers_force(self):
        for mode in ('transport','identity','runtime','rollback_unverified'):
            device,channel,event,stage,verify=base.CheckedApplyTests().fixture();native=channel.call
            if mode in {'transport','identity','rollback_unverified'}:
                def connect(host,timeout):
                    if host=='new':raise HostKeyChangedError('old','new') if mode=='identity' else ConnectionRefusedError('unreachable')
                channel.connect=connect
            else:verify=lambda *_:False
            if mode=='rollback_unverified':
                def call(action,**values):return dict(restored=False) if action=='checked_rollback' else native(action,**values)
                channel.call=call
            with base.fast_deadlines():
                with self.assertRaises(RuntimeError) as error:
                    run_checked(channel,stage,verify,event,candidates=('new',),original_host='old',allow_force_retry=True)
            self.assertEqual(getattr(error.exception,'force_retry_eligible',False),mode=='transport')
    def test_authentication_and_unknown_errors_are_not_retryable(self):
        from paramiko import AuthenticationException
        for error in (AuthenticationException('bad'),HostKeyChangedError(),ValueError('bad setting'),RuntimeError('unknown'),SignalError('changed','external edit')):
            self.assertFalse(connection_error(error))
        self.assertTrue(connection_error(ConnectionRefusedError('down')))
    def test_apply_common_returns_structured_offer_only_after_actual_restore(self):
        parts=ForceExecutionTests().fixture();device,channel,event,review,values,manager,settings=parts
        manager.ssh=NS(_client=object(),_host=settings['host'],_port=22,_user='root',host_fingerprint='PIN')
        channel.connect=lambda host,timeout:(_ for _ in ()).throw(ConnectionRefusedError('down')) if host=='192.168.22.1' else None
        with base.fast_deadlines(),patch('modules.capture_services.openwrt.checked_apply.ApplyChannel',return_value=channel):
            result=apply_common(manager,review,values,event,Mock(),settings,'secret','PIN')
        self.assertEqual(result['outcome'],'restored');self.assertTrue(result['retry_eligible']);self.assertFalse(result['applied'])
        self.assertEqual(device.config['network']['lan']['ipaddr'],review['values']['ipaddr'])


class ForceOfferTests(unittest.TestCase):
    def offer(self):
        gate=LanForceRetry();ssh=NS(connected=True,_client=object(),host_fingerprint='PIN')
        args=dict(apply=True,review=dict(config='network',token='original'),values=dict(ipaddr='desired'))
        result=dict(outcome='restored',retry_eligible=True)
        gate.publish(('normal','common_save',args,'PIN',ssh._client,None),result)
        return gate,ssh,args,result
    def test_single_use_offer_ignores_tampered_values_and_expires(self):
        gate,ssh,args,result=self.offer()
        final=gate.consume(dict(force_token=result['force_retry'],values=dict(ipaddr='tampered')),ssh)
        self.assertEqual(final['values'],args['values']);self.assertTrue(final['force'])
        with self.assertRaises(RuntimeError):gate.consume(dict(force_token=result['force_retry']),ssh)
        gate,ssh,args,result=self.offer()
        with patch('modules.capture_services.lan_force_retry.time.monotonic',return_value=10**12):
            with self.assertRaises(RuntimeError):gate.consume(dict(force_token=result['force_retry']),ssh)
    def test_changed_device_or_connection_rejects_offer(self):
        for field,value in (('_client',object()),('host_fingerprint','OTHER'),('connected',False)):
            gate,ssh,args,result=self.offer();setattr(ssh,field,value)
            with self.assertRaises(RuntimeError):gate.consume(dict(force_token=result['force_retry']),ssh)
    def test_no_offer_for_uncertain_restore_or_already_forced_request(self):
        for outcome,force in (('uncertain',False),('restored',True)):
            gate,ssh,args,result=self.offer();args['force']=force;result=dict(outcome=outcome,retry_eligible=True)
            gate.publish(('normal','common_save',args,'PIN',ssh._client,None),result)
            self.assertIsNone(gate.offer);self.assertNotIn('force_retry',result)


class ForceWorkerFlowTests(unittest.TestCase):
    def test_restored_worker_offer_can_start_force_only_after_user_submission(self):
        from PySide6.QtTest import QSignalSpy
        from tests.test_router_policy_worker import Owner,wait_for
        from modules.capture_services.router_management_service import RouterManagementService
        device,channel,event,review,values,manager,settings=ForceExecutionTests().fixture()
        ssh=NS(connected=True,_client=object(),host_fingerprint='PIN',_host=settings['host'],_port=22,_user='root')
        channel.connect=lambda host,timeout:(_ for _ in ()).throw(ConnectionRefusedError('down')) if host=='192.168.22.1' else None
        owner=Owner(ssh,device);service=RouterManagementService(owner);done=QSignalSpy(service.completed);failed=QSignalSpy(service.failed)
        offered=[]
        def complete(key,result):
            if result.get('force_retry'):
                offered.append(result)
                self.assertEqual(len(device.native),0)
                service.submit('common_save',apply=True,force=True,force_token=result['force_retry'])
        service.completed.connect(complete)
        try:
            with base.fast_deadlines(),patch('modules.capture_services.openwrt.checked_apply.ApplyChannel',return_value=channel):
                service.submit('common_save',review=review,values=values,apply=True)
                wait_for(done,2)
            self.assertEqual(done.count(),2,str([failed.at(i) for i in range(failed.count())]))
            self.assertEqual(failed.count(),0);self.assertEqual(len(offered),1)
            self.assertEqual(done.at(1)[1]['outcome'],'reconnect')
            self.assertEqual([m for m,a in device.native],['commit','apply'])
            self.assertIsNone(service.current);self.assertFalse(service.queue);self.assertFalse(owner._openwrt_workers)
            self.assertIsNone(service._force_retry.offer)
        finally:owner.deleteLater()


class RestoredConnectionTests(unittest.TestCase):
    def test_verified_restored_connection_can_resume_only_the_original_session(self):
        parts=ForceExecutionTests().fixture();device,channel,event,review,values,manager,settings=parts
        original_client=object();new_client=object();channel.ssh._client=new_client
        ssh=NS(_client=original_client,connected=True,connection_generation=7,_host=settings['host'],_port=22,_user='root',host_fingerprint='PIN')
        calls=[]
        def adopt(source,expected,generation,fingerprint):
            calls.append((source,expected,generation,fingerprint))
            ssh._client=new_client;ssh.connected=True;return True
        ssh.adopt_verified=adopt;manager.ssh=ssh
        def connect(host,timeout):
            if host=='192.168.22.1':ssh.connected=False;raise ConnectionRefusedError('new target unavailable')
        channel.connect=connect
        with base.fast_deadlines(),patch('modules.capture_services.openwrt.checked_apply.ApplyChannel',return_value=channel):
            result=apply_common(manager,review,values,event,Mock(),settings,'secret','PIN')
        self.assertTrue(result['session_restored']);self.assertIs(ssh._client,new_client)
        self.assertEqual(calls,[(channel.ssh,original_client,7,'PIN')])
