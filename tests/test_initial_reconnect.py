"""User-paced initial Wi-Fi reconnects; no real router or network writes."""
import copy
import json
import tempfile
import threading
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock,patch
from modules.capture_services.openwrt.initial_reconnect_store import ReconnectStore,user_reconnect_required
from modules.capture_services.openwrt.initial_reconnect import apply_initial,verify_pending,_complete_static
from tests.test_lan_client_recovery import adapter


class ReconnectTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.store=ReconnectStore(self.temp.name)
        self.network=dict(selection_source='ssh_socket',adapters=[adapter(wireless=True)])
        self.review=dict(kind='initial',await_reconnect=True,client_network=self.network,delete_defaults=['default_radio0'],
                         operations=[dict(action='set',config='system',section='main',values=dict(hostname='NewRouter'))],
                         files=[],new_ap=dict(section='lxg_initial_new',radio='radio0',ssid='NewWifi',mac='02:00:00:00:00:02',password='never-persist-this'))
        self.snapshot=dict(configs=dict(network=dict(lan=dict(proto='static',ipaddr='192.168.11.1',netmask='255.255.255.0'))),hashes={'network':'hash'},files={})
        self.owner=NS(fingerprint='pinned',_apply_host='192.168.11.1',_client_network=self.network,
                      ssh=NS(_host='192.168.11.1'),_create_ap=Mock(return_value=self.snapshot),
                      _call=Mock(side_effect=self.call),read=Mock(return_value=self.snapshot),_current=Mock(),
                      _run=Mock(),_cleanup_file_backup=Mock(return_value=''))
        self.actions=[];self.lose_reply=False;self.stage_failure=False;self.ready=True
    def call(self,action,*args,**kwargs):
        self.actions.append(action)
        if action=='session':return dict(sid='a'*32)
        if action=='stage' and self.stage_failure:raise RuntimeError('stale snapshot')
        if action=='initial_deferred_apply':
            if self.lose_reply:raise ConnectionResetError('reply lost')
            return dict(submitted=True)
        if action in {'policy_applied','ap_ready'}:return dict(ready=self.ready)
        return {}
    def apply(self):
        with patch('modules.capture_services.lan_client_recovery.attach_inventory',return_value=self.review):
            return apply_initial(self.owner,self.review,self.snapshot,threading.Event(),Mock(),store=self.store)
    def test_only_proven_wifi_initial_replacement_uses_deferred_mode(self):
        self.assertTrue(user_reconnect_required(self.review))
        for change in ({'kind':'ap'},{'new_ap':None},{'delete_defaults':[]}):
            self.assertFalse(user_reconnect_required(dict(self.review,**change)))
        for wireless in (False,None):
            row=adapter(wireless=wireless)
            value=dict(self.review,client_network=dict(selection_source='ssh_socket',adapters=[row]))
            self.assertFalse(user_reconnect_required(value))
    def test_submit_returns_pending_without_confirming_or_waiting_for_user(self):
        value=self.apply();self.assertEqual(value['outcome'],'reconnect');self.assertFalse(value['applied'])
        self.assertEqual(self.actions.count('initial_deferred_apply'),1)
        self.assertNotIn('checked_confirm',self.actions);self.assertNotIn('checked_rollback',self.actions)
        saved=self.store.read('pinned');self.assertEqual(saved['state'],'acknowledged')
        raw=self.store.path('pinned').read_text();self.assertNotIn('never-persist-this',raw);self.assertNotIn('deadline',raw)
        self.assertIn('NewWifi',value['note'])
    def test_lost_apply_reply_is_retained_without_replay_or_rollback(self):
        self.lose_reply=True;value=self.apply()
        self.assertFalse(value['saved']);self.assertEqual(self.store.read('pinned')['state'],'sent')
        self.assertEqual(self.actions.count('initial_deferred_apply'),1);self.assertNotIn('files_restore',self.actions)
    def test_failure_before_submit_does_not_leave_a_false_pending_receipt(self):
        self.stage_failure=True
        with self.assertRaises(RuntimeError):self.apply()
        self.assertIsNone(self.store.read('pinned'));self.assertNotIn('initial_deferred_apply',self.actions)
    def test_definite_preapply_rejection_clears_receipt_without_replay(self):
        from modules.capture_services.openwrt.router_policy import PolicyDeviceError
        original=self.call
        def blocked(action,*args,**kwargs):
            if action=='initial_deferred_apply':raise PolicyDeviceError('pending','Another rollback is active')
            return original(action,*args,**kwargs)
        self.owner._call.side_effect=blocked
        with self.assertRaises(PolicyDeviceError):self.apply()
        self.assertIsNone(self.store.read('pinned'))

    def test_relogin_after_backup_was_already_cleaned_can_finish(self):
        self.apply();receipt=self.store.read('pinned')
        receipt.update(file_backup='/root/luxiaoguan-policy.TEST',file_token='a'*16)
        self.store.write(receipt);self.owner._run.return_value='absent'
        self.assertTrue(verify_pending(self.owner,threading.Event(),self.store)['verified'])
        self.owner._cleanup_file_backup.assert_not_called();self.assertIsNone(self.store.read('pinned'))

    def test_backup_cleanup_failure_retains_receipt_for_next_login(self):
        self.apply();receipt=self.store.read('pinned')
        receipt.update(file_backup='/root/luxiaoguan-policy.TEST',file_token='a'*16)
        self.store.write(receipt);self.owner._run.return_value='present'
        self.owner._cleanup_file_backup.return_value='Recovery cleanup could not be verified'
        with self.assertRaises(RuntimeError):verify_pending(self.owner,threading.Event(),self.store)
        self.assertIsNotNone(self.store.read('pinned'))
        self.owner._cleanup_file_backup.return_value=''
        self.assertTrue(verify_pending(self.owner,threading.Event(),self.store)['verified'])

    def test_duplicate_pending_request_stops_before_creating_another_ap(self):
        self.apply();self.owner._create_ap.reset_mock()
        with self.assertRaises(RuntimeError):self.apply()
        self.owner._create_ap.assert_not_called()
    def test_relogin_verifies_and_clears_receipt(self):
        self.apply();result=verify_pending(self.owner,threading.Event(),self.store)
        self.assertTrue(result['verified']);self.assertIsNone(self.store.read('pinned'))
        self.assertIn('policy_applied',self.actions);self.assertIn('ap_ready',self.actions)
    def test_mismatch_preserves_receipt_and_never_claims_success(self):
        self.apply();self.ready=False
        with self.assertRaises(RuntimeError):verify_pending(self.owner,threading.Event(),self.store)
        self.assertIsNotNone(self.store.read('pinned'))
    def test_other_router_does_not_consume_receipt(self):
        self.apply();self.owner.fingerprint='another-router';self.owner._call.reset_mock()
        self.assertIsNone(verify_pending(self.owner,threading.Event(),self.store))
        self.owner._call.assert_not_called();self.assertIsNotNone(self.store.read('pinned'))
    def test_same_pinned_router_can_be_verified_through_a_login_alias(self):
        self.apply();self.owner.ssh._host='192.168.9.1';self.owner._call.reset_mock()
        self.assertTrue(verify_pending(self.owner,threading.Event(),self.store)['verified'])
        self.owner._call.assert_called();self.assertIsNone(self.store.read('pinned'))
    def test_saved_lan_configuration_without_runtime_evidence_is_not_enough(self):
        self.apply();receipt=self.store.read('pinned')
        receipt['lan_before']=dict(proto='static',ipaddr='192.168.11.1',netmask='255.255.255.0')
        receipt['lan_after']=dict(proto='static',ipaddr='192.168.22.1',netmask='255.255.255.0')
        self.store.write(receipt)
        with self.assertRaises(RuntimeError):verify_pending(self.owner,threading.Event(),self.store)
        self.assertIsNotNone(self.store.read('pinned'))

    def test_static_collision_is_checked_before_new_ap_or_local_writes(self):
        from modules.platform_services.static_network import StaticMigrationError
        self.network['adapters']=[adapter(wireless=True,dhcp=False)]
        self.review['new_address']='192.168.22.123'
        self.review['operations']=[dict(action='set',config='network',section='lan',values=dict(ipaddr='192.168.22.123',netmask='255.255.255.0'))]
        with self.assertRaises(StaticMigrationError):self.apply()
        self.owner._create_ap.assert_not_called();self.assertFalse(self.actions)
    def test_static_finalization_occurs_only_after_router_verification(self):
        from modules.platform_services.static_network import StaticMigration,migration_plan
        from modules.capture_services.lan_client_recovery import client_plan
        from tests.test_static_network import Device
        from tests.test_lan_client_recovery import review,NEW
        driver=Device();plan=migration_plan(client_plan(review(adapter(dhcp=False,dns=driver.state['dns'])),NEW))
        with tempfile.TemporaryDirectory() as folder:
            tx=StaticMigration(plan,driver,folder);event=threading.Event()
            tx.prepare(event);tx.applying();tx.activate();tx.confirming()
            self.assertEqual(len(driver.state['addresses']),2)
            with patch('modules.platform_services.static_network.StaticMigration',return_value=tx):_complete_static(plan)
            self.assertEqual(driver.state['addresses'],[plan['new_address']]);self.assertFalse(tx.path.exists())
            with patch('modules.platform_services.static_network.StaticMigration',return_value=tx):_complete_static(plan)
    def test_store_rejects_secrets_and_survives_new_store_instance(self):
        self.apply();other=ReconnectStore(self.temp.name);value=other.read('pinned')
        self.assertEqual(value['state'],'acknowledged');value['password']='bad'
        with self.assertRaises(ValueError):other.write(value)

    def test_login_schedules_verification_in_exclusive_worker(self):
        from PySide6.QtTest import QSignalSpy
        from tests.test_router_policy_worker import PolicyWorkerTests,wait_for
        owner,service,ssh,runner=PolicyWorkerTests().fixture();ssh._host='192.168.11.1'
        done=QSignalSpy(service.completed);verified=QSignalSpy(service.initialVerificationCompleted)
        def check(manager,event):
            self.assertTrue(owner.task_controller.busy)
            self.assertIsNot(threading.current_thread(),threading.main_thread())
            return dict(verified=True,note='Verified original router')
        try:
            with patch('modules.capture_services.openwrt.router_boot_status.read_boot_status',return_value=dict(boot_state=True)),patch('modules.capture_services.openwrt.initial_reconnect_store.ReconnectStore') as store,patch('modules.capture_services.openwrt.initial_reconnect.verify_pending',side_effect=check):
                store.return_value.read.return_value=dict(target=ssh._host)
                owner.openwrtSnapshotChanged.emit(None);wait_for(done,2)
            self.assertEqual(done.count(),2);self.assertEqual(verified.count(),1)
            self.assertFalse(owner.task_controller.busy);self.assertIsNone(service.current)
        finally:owner.deleteLater()

    def test_login_result_is_not_published_as_success_for_a_replaced_connection(self):
        from PySide6.QtTest import QSignalSpy
        from tests.test_router_policy_worker import PolicyWorkerTests,wait_for
        owner,service,ssh,runner=PolicyWorkerTests().fixture();ssh._host='192.168.11.1'
        done=QSignalSpy(service.completed);verified=QSignalSpy(service.initialVerificationCompleted)
        def check(manager,event):
            ssh._client=object()
            return dict(verified=True,note='Original router verified')
        try:
            with patch('modules.capture_services.openwrt.router_boot_status.read_boot_status',return_value=dict(boot_state=True)),patch('modules.capture_services.openwrt.initial_reconnect_store.ReconnectStore') as store,patch('modules.capture_services.openwrt.initial_reconnect.verify_pending',side_effect=check):
                store.return_value.read.return_value=dict(target=ssh._host)
                owner.openwrtSnapshotChanged.emit(None);wait_for(done,2)
            self.assertEqual(verified.count(),1);self.assertFalse(verified.at(0)[0]['verified'])
        finally:owner.deleteLater()


class ReconnectViewTests(unittest.TestCase):
    def test_wireless_reconnect_review_renders_both_themes_and_scales(self):
        from tests.test_standalone import APP
        from PySide6.QtWidgets import QWidget,QTextEdit
        from modules.main_pages.packet_capture.openwrt.router_policy_dialogs import PolicyReviewDialog
        from modules.ui_scale_manager import UiScaleManager
        from modules.dialog_theme import build_base_stylesheet
        from pathlib import Path
        network=dict(selection_source='ssh_socket',adapters=[adapter(wireless=True)])
        review=dict(kind='initial',operations=[{}],files=[],steps=['Apply reviewed settings'],await_reconnect=True,
                    client_network=network,delete_defaults=['default_radio0'],
                    new_ap=dict(ssid='Example-New-WiFi',password='example-password',mac='02:00:00:00:00:01'))
        folder=Path('build/initial-reconnect-verification/views');folder.mkdir(parents=True,exist_ok=True)
        for light in (False,True):
            for percent in (100,150):
                parent=QWidget();parent.scale_manager=UiScaleManager(percent)
                with patch('modules.common_widgets.common_dialogs.build_current_base_stylesheet',side_effect=lambda config_manager,scale_manager:build_base_stylesheet(config_manager,light_theme=light,theme_name='light.qss' if light else 'dark.qss',scale_manager=scale_manager)):
                    dialog=PolicyReviewDialog(review,parent)
                    try:
                        dialog.show();APP.processEvents()
                        self.assertIn('\u4e0d\u4f1a\u56e0\u91cd\u8fde\u8d85\u65f6',dialog.findChild(QTextEdit).toPlainText())
                        self.assertFalse(dialog.apply_button.isEnabled())
                        dialog.grab().save(str(folder/f'review-{light}-{percent}.png'))
                    finally:dialog.reject();dialog.deleteLater();parent.deleteLater()

    def test_final_submit_phase_clears_previous_countdown(self):
        from tests.test_standalone import APP
        import time
        from modules.main_pages.packet_capture.openwrt.router_task_progress import RouterTaskProgressDialog
        dialog=RouterTaskProgressDialog('Initial setup',lambda:None);self.addCleanup(dialog.deleteLater)
        dialog.update_progress(dict(configuration_apply=True,deadline=time.monotonic()+90,stage='First phase',uncancellable=True))
        self.assertTrue(dialog._clock.isActive())
        dialog.update_progress(dict(configuration_apply=True,deadline=None,stage='Submit and reconnect',uncancellable=True))
        self.assertFalse(dialog._clock.isActive());self.assertEqual(dialog.message.text(),'Submit and reconnect')
        dialog.finish()


class NativeRpcTests(unittest.TestCase):
    def setUp(self):
        from tests.test_router_policy import PolicyDevice,LuaRuntime
        if not LuaRuntime:self.skipTest('Lua runtime required')
        self.d=PolicyDevice();self.p=self.d.policy;self.args=[];original=self.d.call
        def call(obj,method,arguments):
            args=self.d.unwrap(arguments)
            if obj=='uci' and method=='confirm' and self.d.applied is None:
                self.d.calls.append((obj,method));return None,5
            if obj=='uci' and method=='apply':self.args.append(args)
            result=original(obj,method,arguments)
            if obj=='uci' and method=='apply' and args.get('rollback') is False and result[1] is None:
                self.d.applied=self.d.backup=None
            return result
        self.d.call=call;self.d.lua.globals().bridge_call=call
    def stage(self):
        before=self.p.read(self.d.cancel)
        ops=[dict(action='set',config='network',section='lan',values=dict(ipaddr='192.168.11.1'))]
        sid=self.p._call('session',self.d.cancel)['sid']
        self.p._call('stage',self.d.cancel,sid=sid,hashes=before['hashes'],operations=ops)
        return before,sid
    def test_real_lua_applies_without_rollback_timer_or_implicit_reboot(self):
        before,sid=self.stage();value=self.p._call('initial_deferred_apply',self.d.cancel,sid=sid,hashes=before['hashes'])
        self.assertTrue(value['submitted']);self.assertIs(self.args[0]['rollback'],False)
        self.assertNotIn('timeout',self.args[0]);self.assertIsNone(self.d.applied)
        self.assertEqual(self.d.config['network']['lan']['ipaddr'],'192.168.11.1')
        self.assertFalse(any('reboot' in command for _,command in self.d.commands))
    def test_external_configuration_change_is_rejected_before_apply(self):
        before,sid=self.stage();self.d.files['/etc/config/network']+=' '
        with self.assertRaises(RuntimeError):self.p._call('initial_deferred_apply',self.d.cancel,sid=sid,hashes=before['hashes'])
        self.assertFalse(self.args)
    def test_foreign_pending_rollback_is_not_destroyed(self):
        before,sid=self.stage();self.d.applied='b'*32;self.d.backup=copy.deepcopy(self.d.config)
        with self.assertRaises(RuntimeError):self.p._call('initial_deferred_apply',self.d.cancel,sid=sid,hashes=before['hashes'])
        self.assertFalse(self.args);self.assertEqual(self.d.applied,'b'*32)


if __name__=='__main__':unittest.main()
