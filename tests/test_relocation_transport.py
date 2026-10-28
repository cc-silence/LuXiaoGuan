"""Production relocation Lua with a failing pinned connection; no real router changes."""
import copy
import threading
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock,patch
from tests.test_relocation import MovingDevice,LuaRuntime
from modules.capture_services.openwrt.wireless_setup_manager import WirelessSetupManager
from modules.capture_services.openwrt.relocation_manager import RelocationManager
from modules.capture_services.openwrt.relocation_connection import RelocationConnection
from modules.capture_services.openwrt.signal_manager import SignalError
from modules.capture_services.openwrt.operation_diagnostics import OperationDiagnostics


class FastEvent(threading.Event):
    def wait(self,timeout=None):return self.is_set()


class Clock:
    def __init__(self):self.value=0
    def monotonic(self):self.value+=1;return self.value


class LuaChannel:
    def __init__(self,settings,password,fingerprint,wireless):
        self.settings=settings;self.password=password;self.fingerprint=fingerprint
        self.wireless=wireless;self.runner=wireless.runner;self.lua_bin=None
        self.ssh=NS(connected=False,close=Mock(side_effect=self.disconnect))
        self.connects=0;self.calls=[];self.hook=lambda action,values:None
        self.after=lambda action,values,result:result
    def disconnect(self):self.ssh.connected=False
    def connect(self,host,timeout):
        self.connects+=not self.ssh.connected;self.ssh.connected=True
    def call(self,action,**values):
        self.calls.append(action);self.hook(action,values)
        result=self.wireless._call(action,FastEvent(),**values)
        return self.after(action,values,result)
    def close(self):self.password='';self.disconnect()


class RelocationTransportTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(LuaRuntime,'Lua runtime is required')
        self.device=MovingDevice();self.device.operation_diagnostics=OperationDiagnostics()
        self.wireless=WirelessSetupManager(self.device);self.manager=RelocationManager(self.wireless)
        self.event=FastEvent();self.review=self.manager.preview(self.event)
        self.channel=LuaChannel({},'secret','pinned',self.wireless)
        clock=Clock()
        self.addCleanup(patch.stopall)
        self.factory=patch('modules.capture_services.openwrt.relocation_connection.ApplyChannel',return_value=self.channel).start()
        patch('modules.capture_services.openwrt.relocation_manager.time',clock).start()
        patch('modules.capture_services.openwrt.relocation_connection.time',clock).start()
        patch('modules.capture_services.openwrt.relocation_manager.threading',NS(Event=FastEvent)).start()

    def apply(self):
        return self.manager.apply(self.review,self.event,Mock(),connection_settings=dict(host='router',port=22,user='root'),password='secret',fingerprint='pinned')

    def test_status_transport_loss_reconnects_and_never_applies_twice(self):
        def hook(action,values):
            if action=='move_status' and self.channel.calls.count(action)<=2:
                self.channel.disconnect();raise SignalError('transport','private socket failure')
        self.channel.hook=hook
        result=self.apply()
        self.assertEqual(result['macs'],2)
        self.assertEqual(self.channel.calls.count('move_stage'),1)
        self.assertEqual(self.device.calls.count(('uci','apply')),1)
        self.assertGreaterEqual(self.channel.connects,4)
        self.factory.assert_called_once_with(dict(host='router',port=22,user='root'),'secret','pinned')
        self.assertEqual(self.channel.lua_bin,self.wireless.signal._lua_bin)
        self.assertFalse(self.channel.password);self.assertIsNone(self.manager._reconnect)

    def test_lost_stage_reply_restores_without_resubmitting_stage(self):
        def after(action,values,result):
            if action=='move_stage':self.channel.disconnect();raise SignalError('transport','lost stage reply')
            return result
        self.channel.after=after
        with self.assertRaises(SignalError):self.apply()
        self.assertEqual(self.device.config,self.device.original)
        self.assertEqual(self.channel.calls.count('move_stage'),1)
        self.assertTrue(self.device.operation_diagnostics.snapshot()['configuration_failures'][-1]['restored'])
        self.assertEqual(self.device.operation_diagnostics.snapshot()['configuration_failures'][-1]['last_stage'],'apply')

    def test_lost_rollback_reply_is_checked_against_original_configuration(self):
        def hook(action,values):
            if action=='move_status':raise SignalError('changed','verification rejected')
        def after(action,values,result):
            if action=='finish' and not values.get('confirm'):
                self.channel.disconnect();raise SignalError('transport','lost rollback reply')
            return result
        self.channel.hook=hook;self.channel.after=after
        with self.assertRaises(SignalError) as error:self.apply()
        self.assertEqual(error.exception.code,'changed')
        self.assertEqual(self.device.config,self.device.original)
        self.assertEqual(self.channel.calls.count('finish'),1)
        self.assertTrue(self.device.operation_diagnostics.snapshot()['configuration_failures'][-1]['restored'])

    def test_lost_confirm_reply_does_not_claim_rollback_or_success(self):
        def after(action,values,result):
            if action=='finish' and values.get('confirm'):
                self.channel.disconnect();raise SignalError('transport','lost confirm reply')
            return result
        self.channel.after=after
        with self.assertRaises(SignalError) as error:self.apply()
        self.assertEqual(error.exception.code,'recovery')
        self.assertNotEqual(self.device.config,self.device.original)
        self.assertEqual(self.channel.calls.count('move_stage'),1)
        final=self.device.operation_diagnostics.snapshot()['configuration_failures'][-1]
        self.assertEqual(final['status'],'uncertain');self.assertFalse(final['restored'])

    def test_cancel_after_apply_restores_and_closes_connection(self):
        def after(action,values,result):
            if action=='move_stage':self.event.set()
            return result
        self.channel.after=after
        with self.assertRaises(InterruptedError):self.apply()
        self.assertEqual(self.device.config,self.device.original)
        self.assertFalse(self.channel.ssh.connected);self.assertFalse(self.channel.password)

    def test_identity_failure_is_not_retried_as_transport_loss(self):
        original=self.channel.connect
        def connect(host,timeout):
            if self.channel.calls.count('move_stage'):raise ValueError('Untrusted device identity')
            original(host,timeout)
        self.channel.connect=connect
        with self.assertRaises(SignalError) as error:self.apply()
        self.assertEqual(error.exception.code,'recovery')
        self.assertNotIn('move_status',self.channel.calls)
        self.assertEqual(self.channel.calls.count('move_stage'),1)
        self.assertEqual(self.channel.calls.count('finish'),0)

    def test_shared_channel_receives_pinned_fingerprint_and_respects_post_connect_deadline(self):
        with patch('modules.capture_services.openwrt.relocation_connection.ApplyChannel') as factory:
            channel=factory.return_value;channel.ssh.connected=False
            remote=RelocationConnection(self.device,dict(host='router'),'', 'pinned')
            clock=Clock();remote.deadline=2
            with patch('modules.capture_services.openwrt.relocation_connection.time',clock):
                with self.assertRaises(TimeoutError):remote.call('move_status')
            channel.call.assert_not_called()

    def test_verified_transport_adoption_is_bound_to_original_client_and_generation(self):
        original=object();verified=object();self.channel.ssh._client=verified
        ssh=NS(_client=original,connection_generation=42,adopt_verified=Mock(return_value=True))
        self.device.ssh_client=ssh
        result=self.apply()
        ssh.adopt_verified.assert_called_once_with(self.channel.ssh,original,42,'pinned')
        self.assertTrue(result['session_restored']);self.assertIs(result['restored_connection'],verified)

    def test_rejected_transport_adoption_does_not_report_session_restored(self):
        self.device.ssh_client=NS(_client=object(),connection_generation=42,adopt_verified=Mock(return_value=False))
        result=self.apply()
        self.assertNotIn('session_restored',result)


class ReconnectedSessionTests(unittest.TestCase):
    def test_publisher_rejects_replacement_login_and_changed_fingerprint(self):
        from modules.capture_services.reconnected_session import publish_verified_connection
        from modules.capture_services.openwrt.models import OpenWrtSessionSnapshot
        original=object();new=object()
        owner=NS(openwrt_backend=NS(snapshot=OpenWrtSessionSnapshot.initial()),openwrtSnapshotChanged=NS(emit=Mock()))
        ssh=NS(connected=True,_client=new,host_fingerprint='pinned')
        self.assertFalse(publish_verified_connection(owner,ssh,original,'pinned'))
        self.assertFalse(publish_verified_connection(owner,ssh,new,'changed'))
        owner.openwrtSnapshotChanged.emit.assert_not_called()
        self.assertTrue(publish_verified_connection(owner,ssh,new,'pinned'))
        owner.openwrtSnapshotChanged.emit.assert_called_once()
        self.assertEqual(owner.openwrt_backend.snapshot.connection_state.value,'connected')
