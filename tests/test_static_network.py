"""Static IP transaction, compensation and router sequencing without real adapters."""
import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from modules.platform_services.static_network import migration_plan,StaticMigration,StaticMigrationError
from tests.test_lan_client_recovery import adapter,review,NEW
from modules.capture_services.lan_client_recovery import client_plan


class Device:
    def __init__(self):
        self.state=dict(addresses=['192.168.11.123/24'],gateways=['192.168.11.1'],dns=['192.168.11.1','1.1.1.1'],metric=7)
        self.writes=[];self.failure=None
    def read(self):return copy.deepcopy(self.state)
    def write(self,before,after):
        if self.state!=before:raise StaticMigrationError('concurrent edit')
        self.writes.append(copy.deepcopy(after));self.state=copy.deepcopy(after)
        if self.failure:raise self.failure


class StaticTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.driver=Device()
        row=adapter(dhcp=False,renewable=False,dns=self.driver.state['dns'])
        self.plan=migration_plan(client_plan(review(row),NEW))
        self.tx=StaticMigration(self.plan,self.driver,self.temp.name)
        self.cancel=threading.Event()
    def test_stages_keep_old_address_then_commit_preserves_external_dns(self):
        original=copy.deepcopy(self.driver.state)
        self.tx.prepare(self.cancel)
        self.assertEqual(self.driver.state['addresses'],['192.168.11.123/24','192.168.22.123/24'])
        self.assertEqual(self.driver.state['gateways'],original['gateways'])
        self.tx.activate()
        self.assertEqual(self.driver.state['dns'],['192.168.22.1','1.1.1.1'])
        self.tx.commit()
        self.assertEqual(self.driver.state['addresses'],['192.168.22.123/24'])
        self.assertFalse(self.tx.path.exists())
    def test_failure_after_activation_restores_exact_baseline(self):
        before=self.driver.read();self.tx.prepare(self.cancel);self.tx.activate();self.tx.rollback()
        self.assertEqual(self.driver.read(),before);self.assertFalse(self.tx.path.exists())
    def test_partial_write_failure_is_compensated(self):
        before=self.driver.read();self.driver.failure=RuntimeError('write interrupted')
        with self.assertRaises(RuntimeError):self.tx.prepare(self.cancel)
        self.driver.failure=None;self.tx.rollback();self.assertEqual(self.driver.read(),before)
    def test_deleted_unchanged_gateway_is_not_restored_over_external_edit(self):
        self.driver.state['metric']=0  # A valid route metric also used for an empty route set.
        self.driver.failure=RuntimeError('address write interrupted')
        with self.assertRaises(RuntimeError):self.tx.prepare(self.cancel)
        self.driver.failure=None;self.driver.state['gateways']=[]
        count=len(self.driver.writes)
        with self.assertRaises(StaticMigrationError):self.tx.rollback()
        self.assertEqual(self.driver.state['gateways'],[])
        self.assertEqual(len(self.driver.writes),count)

    def test_stale_preflight_does_not_restore_over_current_settings(self):
        self.driver.state['dns']=['9.9.9.9'];before=self.driver.read()
        with self.assertRaises(StaticMigrationError):self.tx.prepare(self.cancel)
        self.tx.rollback()
        self.assertEqual(self.driver.read(),before);self.assertFalse(self.driver.writes)

    def test_missing_recovery_file_does_not_skip_live_compensation(self):
        before=self.driver.read();self.tx.prepare(self.cancel);self.tx.path.unlink()
        self.tx.rollback()
        self.assertEqual(self.driver.read(),before)

    def test_narrower_mask_cannot_turn_client_into_network_or_broadcast(self):
        for client,router in [('192.168.11.127/24','192.168.11.1'),('192.168.11.128/24','192.168.11.129')]:
            with self.subTest(client=client):
                row=adapter(dhcp=False,addresses=[client])
                new=dict(NEW,ipaddr=router,netmask='255.255.255.128')
                with self.assertRaises(StaticMigrationError):migration_plan(client_plan(review(row),new))

    def test_external_edit_is_never_overwritten(self):
        self.tx.prepare(self.cancel);self.driver.state['dns']=['9.9.9.9']
        with self.assertRaises(StaticMigrationError):self.tx.rollback()
        self.assertEqual(self.driver.state['dns'],['9.9.9.9']);self.assertTrue(self.tx.path.exists())
    def test_confirmed_router_cleanup_failure_keeps_new_configuration(self):
        self.tx.prepare(self.cancel);self.tx.activate();self.driver.failure=RuntimeError('cleanup failed')
        with self.assertRaises(RuntimeError):self.tx.commit()
        state=self.driver.read();self.tx.rollback();self.assertEqual(self.driver.read(),state)
        self.assertTrue(json.loads(self.tx.path.read_text())['router_confirmed'])
    def test_unresolved_confirmation_retains_new_and_old_addresses(self):
        self.tx.prepare(self.cancel);self.tx.activate();self.tx.confirming()
        current=self.driver.read()
        with self.assertRaises(StaticMigrationError):self.tx.rollback()
        self.assertEqual(self.driver.read(),current)
        self.assertTrue(json.loads(self.tx.path.read_text())['confirmation_pending'])

    def test_verified_router_restore_releases_confirmation_barrier(self):
        before=self.driver.read();self.tx.prepare(self.cancel);self.tx.activate();self.tx.confirming()
        self.tx.router_restored();self.tx.rollback()
        self.assertEqual(self.driver.read(),before);self.assertFalse(self.tx.path.exists())

    def test_failed_preparation_without_any_change_does_not_rewrite_network(self):
        self.tx.baseline=self.driver.read();self.tx.expected=self.driver.read();self.tx._save()
        self.tx.rollback();self.assertFalse(self.driver.writes)

    def test_cancellation_before_prepare_has_no_write(self):
        self.cancel.set()
        with self.assertRaises(InterruptedError):self.tx.prepare(self.cancel)
        self.assertFalse(self.driver.writes)
    def test_unknown_or_ambiguous_source_stops(self):
        row=adapter(dhcp=False,renewable=False)
        for source in ('system_route','unknown'):
            value=review(row);value['client_network']['selection_source']=source
            with self.assertRaises(StaticMigrationError):migration_plan(client_plan(value,NEW))
    def test_other_adapter_overlapping_new_subnet_stops(self):
        value=review(adapter(dhcp=False),adapter(id='wifi',route=False,addresses=['192.168.22.55/24']))
        with self.assertRaises(StaticMigrationError):migration_plan(client_plan(value,NEW))

    def test_forced_apply_does_not_bypass_static_safety(self):
        from modules.capture_services.openwrt.force_apply import apply_forced
        value=review(adapter(dhcp=False));value.update(config='network')
        with self.assertRaises(RuntimeError):apply_forced(Mock(),value,dict(NEW,auto='1'),self.cancel,Mock(),{},'secret','PIN')

    def test_router_taking_current_client_address_and_small_subnet_stop(self):
        for address in ('192.168.11.123','192.168.22.1/30'):
            new=dict(NEW,ipaddr=address)
            with self.assertRaises(StaticMigrationError):migration_plan(client_plan(review(adapter(dhcp=False)),new))

    def test_transferred_address_collision_stops_without_selecting_another_ip(self):
        for address,mask,local in (('192.168.22.123','255.255.255.0','192.168.11.123/24'),
                                   ('192.168.22.2','255.255.255.252','192.168.11.2/24')):
            with self.subTest(address=address):
                row=adapter(dhcp=False,addresses=[local]);before=copy.deepcopy(row)
                with self.assertRaises(StaticMigrationError) as error:
                    migration_plan(client_plan(review(row),dict(NEW,ipaddr=address,netmask=mask)))
                self.assertIn(address,str(error.exception))
                self.assertIn('\u91cd\u65b0\u8f93\u5165',str(error.exception))
                self.assertEqual(row,before)

    def test_pending_record_blocks_another_migration(self):
        self.tx.prepare(self.cancel)
        second=StaticMigration(self.plan,self.driver,self.temp.name)
        with self.assertRaises(StaticMigrationError):second.prepare(self.cancel)
    def restored_record(self,**changes):
        state=self.driver.read()
        value=dict(schema=1,plan=self.plan,baseline=state,expected=state,pending=state,
                   router_confirmed=False,confirmation_pending=False)
        value.update(changes)
        self.tx.folder.mkdir(parents=True,exist_ok=True)
        self.tx.path.write_text(json.dumps(value),encoding='utf-8')
        return self.tx.path.read_bytes()

    def test_restored_record_is_archived_before_new_preparation(self):
        raw=self.restored_record()
        self.tx.prepare(self.cancel)
        self.assertEqual(self.tx.path.with_suffix('.resolved').read_bytes(),raw)
        self.assertEqual(len(self.driver.writes),1)
        self.tx.rollback()
        self.assertFalse(self.tx.path.exists())

    def test_clean_snapshot_record_can_be_retired_without_pending_write(self):
        raw=self.restored_record(pending=None)
        self.tx.prepare(self.cancel)
        self.assertEqual(self.tx.path.with_suffix('.resolved').read_bytes(),raw)
        self.tx.rollback()

    def test_uncertain_records_are_preserved_without_network_writes(self):
        changed=self.driver.read();changed['addresses']+=['192.168.22.123/24']
        for changes in ({'router_confirmed':True},{'confirmation_pending':True},
                        {'expected':changed},{'pending':changed},{'schema':2}):
            with self.subTest(changes=changes):
                raw=self.restored_record(**changes)
                with self.assertRaises(StaticMigrationError):self.tx.prepare(self.cancel)
                self.tx.rollback()
                self.assertEqual(self.tx.path.read_bytes(),raw)
                self.assertFalse(self.driver.writes)

    def test_record_for_different_router_is_not_retired(self):
        plan=copy.deepcopy(self.plan);plan['old_router']='192.168.11.2'
        raw=self.restored_record(plan=plan)
        with self.assertRaises(StaticMigrationError):self.tx.prepare(self.cancel)
        self.assertEqual(self.tx.path.read_bytes(),raw)
        self.assertFalse(self.driver.writes)

    def test_malformed_record_remains_intact(self):
        self.tx.path.write_bytes(b'{broken')
        with self.assertRaises(StaticMigrationError):self.tx.prepare(self.cancel)
        self.assertEqual(self.tx.path.read_bytes(),b'{broken')
        self.assertFalse(self.driver.writes)

    def test_non_object_record_is_preserved(self):
        self.tx.path.write_bytes(b'[]')
        with self.assertRaises(StaticMigrationError):self.tx.prepare(self.cancel)
        self.assertEqual(self.tx.path.read_bytes(),b'[]')
        self.assertFalse(self.driver.writes)

    def test_live_verification_failure_keeps_record(self):
        raw=self.restored_record()
        self.driver.verify=Mock(side_effect=StaticMigrationError('live mismatch'))
        with self.assertRaises(StaticMigrationError):self.tx.prepare(self.cancel)
        self.assertEqual(self.tx.path.read_bytes(),raw)
        self.assertFalse(self.driver.writes)

    def test_confirmed_completed_cleanup_record_is_archived_for_next_change(self):
        old_plan=copy.deepcopy(self.plan)
        before=self.driver.read()
        after=copy.deepcopy(before);after['addresses']=sorted(before['addresses']+[old_plan['new_address']])
        after['gateways']=[old_plan['new_router']]
        final=copy.deepcopy(after);final['addresses']=[old_plan['new_address']]
        self.driver.state=final
        next_plan=copy.deepcopy(old_plan)
        next_plan.update(old_router=old_plan['new_router'],old_address=old_plan['new_address'])
        tx=StaticMigration(next_plan,self.driver,self.temp.name)
        for expected,pending in ((after,final),(final,None)):
            with self.subTest(pending=pending):
                raw=self.restored_record(plan=old_plan,baseline=before,expected=expected,pending=pending,
                                         router_confirmed=True,confirmation_pending=True)
                tx._reconcile_record(final)
                self.assertFalse(tx.path.exists())
                self.assertEqual(tx.path.with_suffix('.resolved').read_bytes(),raw)
                self.assertFalse(self.driver.writes)

    def test_confirmed_record_with_old_address_remaining_is_not_retired(self):
        original=self.driver.read();self.tx.prepare(self.cancel);self.tx.activate();self.tx.confirming()
        self.tx.router_confirmed=True;self.tx._save();raw=self.tx.path.read_bytes()
        with self.assertRaises(StaticMigrationError):self.tx._reconcile_record(self.driver.read())
        self.assertEqual(self.tx.path.read_bytes(),raw)
        self.assertNotEqual(self.driver.read(),original)

    def test_same_subnet_keeps_client_ip_but_updates_gateway(self):
        new=dict(NEW,ipaddr='192.168.11.2')
        plan=migration_plan(client_plan(review(adapter(dhcp=False,dns=self.driver.state['dns'])),new))
        tx=StaticMigration(plan,self.driver,self.temp.name);tx.prepare(self.cancel);tx.activate();tx.commit()
        self.assertEqual(self.driver.state['addresses'],['192.168.11.123/24'])
        self.assertEqual(self.driver.state['gateways'],['192.168.11.2'])


class RouterSequenceTests(unittest.TestCase):
    def test_old_connection_cancellation_after_apply_does_not_abort_local_finalization(self):
        from tests.test_checked_apply import CheckedApplyTests,run_checked,LuaRuntime
        if not LuaRuntime:self.skipTest('Lua runtime unavailable')
        device,channel,event,stage,verify=CheckedApplyTests().fixture()
        class CancelAwareDevice(Device):
            def read(self):
                if getattr(self,'cancel',None) is not None and self.cancel.is_set():
                    raise InterruptedError('old connection disconnected')
                return super().read()
        driver=CancelAwareDevice();row=adapter(dhcp=False,dns=driver.state['dns'])
        plan=migration_plan(client_plan(review(row),NEW))
        with tempfile.TemporaryDirectory() as folder:
            tx=StaticMigration(plan,driver,folder);original=channel.call
            def call(action,**kwargs):
                result=original(action,**kwargs)
                if action=='checked_confirm':event.set()
                return result
            channel.call=call
            with patch('modules.platform_services.static_network.for_recovery',return_value=tx):
                result=run_checked(channel,stage,verify,event,candidates=('new',),original_host='old')
            self.assertTrue(result['applied']);self.assertTrue(tx.committed)
            self.assertEqual(driver.read()['addresses'],[plan['new_address']])
            self.assertFalse(tx.path.exists())

    def test_lost_confirm_reply_after_router_commit_does_not_revert_client(self):
        from tests.test_checked_apply import CheckedApplyTests,run_checked,fast_deadlines,LuaRuntime
        if not LuaRuntime:self.skipTest('Lua runtime unavailable')
        device,channel,event,stage,verify=CheckedApplyTests().fixture()
        driver=Device();row=adapter(dhcp=False,dns=driver.state['dns'])
        plan=migration_plan(client_plan(review(row),NEW))
        with tempfile.TemporaryDirectory() as folder:
            tx=StaticMigration(plan,driver,folder)
            original_call=channel.call;lost=[False]
            def call(action,**kwargs):
                result=original_call(action,**kwargs)
                if action=='checked_confirm':lost[0]=True;raise ConnectionResetError('reply lost after commit')
                return result
            original_connect=channel.connect
            def connect(*args):
                if lost[0]:raise ConnectionResetError('connection unavailable')
                return original_connect(*args)
            channel.call=call;channel.connect=connect
            with patch('modules.platform_services.static_network.for_recovery',return_value=tx),fast_deadlines():
                with self.assertRaises(RuntimeError):run_checked(channel,stage,verify,event,candidates=('new',),original_host='old')
            self.assertEqual(device.config['network']['lan']['ipaddr'],'192.168.22.1')
            self.assertEqual(driver.state['addresses'],['192.168.11.123/24','192.168.22.123/24'])
            self.assertEqual(driver.state['gateways'],['192.168.22.1'])
            self.assertTrue(tx.path.exists());self.assertTrue(tx.confirmation_pending)

    def test_failed_static_verification_rolls_back_before_reporting_restored(self):
        from tests.test_checked_apply import CheckedApplyTests,run_checked,fast_deadlines,LuaRuntime
        if not LuaRuntime:self.skipTest('Lua runtime unavailable')
        device,channel,event,stage,verify=CheckedApplyTests().fixture()
        migration=Mock();migration.activate.side_effect=StaticMigrationError('local readback failed')
        with patch('modules.platform_services.static_network.for_recovery',return_value=migration),fast_deadlines():
            with self.assertRaises(RuntimeError):run_checked(channel,stage,verify,event,candidates=('new',),original_host='old')
        self.assertIn('checked_rollback',channel.calls);self.assertNotIn('checked_confirm',channel.calls)
        migration.rollback.assert_called_once();migration.commit.assert_not_called()

    def test_shared_apply_prepares_before_remote_apply_and_commits_after_confirm(self):
        from tests.test_checked_apply import ApplyDevice,TestChannel,LuaRuntime
        if not LuaRuntime:self.skipTest('Lua runtime unavailable')
        from modules.capture_services.openwrt.checked_apply import run_checked
        device=ApplyDevice();channel=TestChannel(device);migration=Mock();order=[]
        def mark(name):return lambda *a,**k:order.append(name)
        migration.prepare.side_effect=mark('prepare');migration.activate.side_effect=mark('activate');migration.commit.side_effect=mark('commit')
        call=channel.call
        def record(action,**kwargs):
            order.append(action);return call(action,**kwargs)
        channel.call=record
        from modules.capture_services.openwrt.common_settings import read_settings
        reviewed=read_settings(device,'network',threading.Event())
        values=dict(reviewed['values'],auto='1',ipaddr='192.168.22.1')
        def stage(remote,sid):
            remote.call('common_stage',sid=sid,config='network',review=reviewed,values=values)
            return {'network':reviewed['token']}
        with patch('modules.platform_services.static_network.for_recovery',return_value=migration):
            run_checked(channel,stage,lambda *args:True,threading.Event(),candidates=('192.168.22.1',),original_host='192.168.8.1')
        self.assertLess(order.index('prepare'),order.index('checked_apply'))
        self.assertLess(order.index('activate'),order.index('checked_confirm'))
        self.assertLess(order.index('checked_confirm'),order.index('commit'))
        migration.rollback.assert_not_called()


if __name__=='__main__':unittest.main()
