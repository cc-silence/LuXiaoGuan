"""Regression cases for the user's initial-setup feedback."""
import copy
import hashlib
import json
import unittest
from unittest.mock import Mock,patch
from tests.test_router_policy import PolicyDevice,LuaRuntime
from modules.capture_services.openwrt.router_policy_plan import build_plan


class AnonymousPolicyDevice(PolicyDevice):
    def __init__(self):
        super().__init__()
        self.config['system']['cfg01']['.anonymous']=True
        self.config['dropbear']['ssh1']['.anonymous']=True
        self.config['firewall']['zone_wan']['.anonymous']=True
        self.sync_files()
    def sync_files(self):
        self.files.update({'/etc/config/'+k:json.dumps(v,sort_keys=True) for k,v in self.config.items()})
    def regenerate(self,config):
        result={}
        for index,(name,row) in enumerate(self.config[config].items(),1):
            if row.get('.anonymous'):
                digest=hashlib.sha256(json.dumps(row,sort_keys=True).encode()).hexdigest()[:4]
                name='cfg%02x%s'%(index,digest)
            result[name]=row
        self.config[config]=result;self.sync_files()
    def session_commit(self,sid,config):
        result=super().session_commit(sid,config)
        if result:self.regenerate(config)
        return result
    def call(self,obj,method,args):
        result=super().call(obj,method,args)
        if obj=='uci' and method=='apply' and result[1] is None:
            for config in list(self.config):self.regenerate(config)
        return result
    def read(self,command):
        system=next(row for row in self.config['system'].values() if row.get('.type')=='system')
        if command.startswith('cat /tmp/TZ'):return system['timezone']
        if command.startswith('cat /proc/sys/kernel/hostname'):return system['hostname']
        return super().read(command)


@unittest.skipUnless(LuaRuntime,'Lua runtime unavailable')
class InitialFeedbackTests(unittest.TestCase):
    def test_old_name_based_comparison_reproduces_save_commit_failure(self):
        import tests.test_router_policy as fixtures
        from modules.capture_services.openwrt.router_policy import PolicyDeviceError
        d=AnonymousPolicyDevice();snapshot=d.policy.read(d.cancel)
        plan=build_plan(snapshot,'initial',dict(items=['timezone','ssh','ping']))
        old=fixtures.BOOT.replace('  local identity=row[".name"];if row[".anonymous"] then identity=false end\n  out[#out+1]={name=identity,values=values}', '  out[row[".name"]]=values')
        with patch.object(fixtures,'BOOT',old):
            with self.assertRaises(PolicyDeviceError) as error:d.policy._transaction(snapshot,plan['operations'],d.cancel)
        self.assertEqual(error.exception.code,'save_commit')

    def test_common_system_save_resolves_regenerated_anonymous_section(self):
        from modules.capture_services.openwrt.common_settings import read_settings,save_settings
        d=AnonymousPolicyDevice();review=read_settings(d,'system',d.cancel)
        result=save_settings(d,review,dict(review['values'],hostname='NewRouter'),d.cancel)
        self.assertTrue(result['saved'])
        self.assertEqual(read_settings(d,'system',d.cancel)['values']['hostname'],'NewRouter')

    def test_save_and_order_verification_survive_anonymous_renumbering(self):
        d=AnonymousPolicyDevice();snapshot=d.policy.read(d.cancel)
        plan=build_plan(snapshot,'initial',dict(items=['timezone','ssh','ping']))
        before=list(d.config['firewall'])
        d.policy._transaction(snapshot,plan['operations'],d.cancel)
        self.assertNotEqual(before,list(d.config['firewall']))
        self.assertEqual(next(r for r in d.config['system'].values() if r['.type']=='system')['timezone'],'CST-8')
        self.assertEqual(list(d.config['firewall'])[:2],['lxg_initial_ping4','lxg_initial_ping6'])

    def test_native_initial_apply_verifies_new_ids_and_retains_lan_session(self):
        d=AnonymousPolicyDevice();snapshot=d.policy.read(d.cancel)
        plan=build_plan(snapshot,'initial',dict(items=['timezone','ssh','ping']))
        original=d.ssh._client
        plan['signature']=snapshot['signature']
        result=d.policy.apply(plan,None,None,d.cancel,Mock())
        self.assertTrue(result['applied']);self.assertIs(d.ssh._client,original)
        self.assertTrue(d.ssh.connected)

    def test_extra_non_management_server_does_not_block_initial_preview(self):
        d=PolicyDevice();d.config['uhttpd']['files']={'.type':'uhttpd','home':'/srv/files','listen_http':['0.0.0.0:8080']};d.files['/etc/config/uhttpd']=json.dumps(d.config['uhttpd'])
        plan=build_plan(d.policy.read(d.cancel),'initial',dict(items=['http','homepage','web']))
        self.assertTrue(plan['operations']);self.assertTrue(plan['files'])
        self.assertFalse(any(op.get('section')=='files' for op in plan['operations']))
        web=[op for op in plan['operations'] if op.get('section')=='lxg_initial_web'][0]
        self.assertEqual(web['values']['dest_port'],['80','443'])

    def test_ambiguous_management_servers_skip_only_affected_items(self):
        d=PolicyDevice();d.config['uhttpd']['other']=copy.deepcopy(d.config['uhttpd']['main']);d.files['/etc/config/uhttpd']=json.dumps(d.config['uhttpd'])
        plan=build_plan(d.policy.read(d.cancel),'initial',dict(items=['http','homepage','web','timezone']))
        self.assertEqual(len(plan['skipped']),3)
        self.assertFalse(any(op.get('config')=='uhttpd' for op in plan['operations']))
        self.assertTrue(any(op.get('config')=='system' for op in plan['operations']))

    def test_absent_web_service_does_not_block_other_initial_settings(self):
        d=PolicyDevice();d.config.pop('uhttpd');d.files.pop('/etc/config/uhttpd')
        plan=build_plan(d.policy.read(d.cancel),'initial',dict(items=['http','homepage','web','timezone']))
        self.assertEqual(len(plan['skipped']),3);self.assertTrue(plan['operations'])


class LanSessionHandoffTests(unittest.TestCase):
    def clients(self):
        from modules.capture_services.openwrt.ssh_client import OpenWrtSshClient
        key=Mock();key.asbytes.return_value=b'router-public-key'
        def make(active):
            ssh=OpenWrtSshClient();ssh.configure('192.168.11.1',22,'root')
            transport=Mock();transport.is_active.return_value=active;transport.get_remote_server_key.return_value=key
            client=Mock();client.get_transport.return_value=transport;ssh._client=client
            ssh._authenticated_settings=dict(host='192.168.11.1',port=22,user='root',auth_method='password')
            return ssh,client
        target,old=make(False);source,new=make(True)
        return target,old,source,new

    def test_verified_transport_survives_temporary_channel_cleanup(self):
        target,old,source,new=self.clients();generation=target.connection_generation
        with patch.object(target,'_watch_transport'):
            self.assertTrue(target.adopt_verified(source,old,generation,source.host_fingerprint))
        source.close();self.assertTrue(target.connected);self.assertIs(target._client,new)
        new.close.assert_not_called();old.close.assert_called_once();target.close()

    def test_user_disconnect_or_live_login_cannot_be_overwritten(self):
        target,old,source,new=self.clients();generation=target.connection_generation;fingerprint=source.host_fingerprint
        target.close();self.assertFalse(target.adopt_verified(source,old,generation,fingerprint))
        self.assertIsNone(target._client);source.close()
        target,old,source,new=self.clients();old.get_transport().is_active.return_value=True
        self.assertFalse(target.adopt_verified(source,old,target.connection_generation,source.host_fingerprint))
        self.assertIs(target._client,old);target.close();source.close()

    def test_changed_device_or_address_is_never_adopted(self):
        target,old,source,new=self.clients()
        self.assertFalse(target.adopt_verified(source,old,target.connection_generation,'wrong-key'))
        source.configure('192.168.22.1',22,'root')
        self.assertFalse(target.adopt_verified(source,old,target.connection_generation,source.host_fingerprint))
        target.close();source.close()


class FeedbackUiTests(unittest.TestCase):
    def test_hotspot_fields_follow_explicit_checkbox_and_keep_input(self):
        from tests.test_standalone import APP
        from modules.main_pages.packet_capture.openwrt.router_policy_dialogs import InitialPolicyDialog
        dialog=InitialPolicyDialog(dict(pages=['wireless']))
        self.assertFalse(dialog.checks['replace_ap'].isChecked());self.assertTrue(dialog.ap_fields.isHidden())
        self.assertNotIn('replace_ap',dialog.values()['items'])
        dialog.checks['replace_ap'].setChecked(True);dialog.password.setText('chosen-password')
        self.assertFalse(dialog.ap_fields.isHidden());self.assertIn('replace_ap',dialog.values()['items'])
        dialog.checks['replace_ap'].setChecked(False);dialog.checks['replace_ap'].setChecked(True)
        self.assertEqual(dialog.password.text(),'chosen-password');dialog.deleteLater()

    def test_backup_and_restore_results_always_show_modal_notice(self):
        from modules.main_pages.packet_capture.openwrt.maintenance_actions import MaintenanceActions
        page=Mock();page.manager.progress=None
        for note in ('backup saved','restore accepted; wait before login'):
            actions=MaintenanceActions(page);actions.pending['task']=('done',None)
            with patch('modules.main_pages.packet_capture.openwrt.maintenance_actions.MessageDialog') as dialog:
                self.assertTrue(actions.completed('task',dict(note=note)))
                dialog.return_value.exec.assert_called_once();dialog.return_value.deleteLater.assert_called_once()
            page._message.assert_called_with(note)


class RestoredSessionPublishTests(unittest.TestCase):
    def test_main_thread_snapshot_tracks_verified_restored_lan_session(self):
        from tests.test_router_policy_worker import Owner
        from modules.capture_services.router_management_service import RouterManagementService
        from modules.capture_services.openwrt.models import OpenWrtSessionSnapshot,OpenWrtConnectionState
        from types import SimpleNamespace
        from threading import Event
        ssh=SimpleNamespace(connected=True,_client=object(),host_fingerprint='pinned')
        owner=Owner(ssh,Mock());owner._shutting_down=True;owner.task_controller=Mock()
        owner.openwrt_backend.snapshot=OpenWrtSessionSnapshot(connection_state=OpenWrtConnectionState.DISCONNECTED)
        service=RouterManagementService(owner)
        service.current=('task','policy_apply',{},'pinned',object(),Event())
        service._success(dict(applied=True,session_restored=True,note='done'))
        self.assertEqual(owner.openwrt_backend.snapshot.connection_state,OpenWrtConnectionState.CONNECTED)
        self.assertEqual(owner.openwrt_backend.snapshot.host_fingerprint,'pinned')
        self.assertIsNone(service.current);owner.deleteLater()
