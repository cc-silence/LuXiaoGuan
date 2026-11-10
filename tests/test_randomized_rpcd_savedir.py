import copy,hashlib,threading,unittest,json
from unittest.mock import Mock,patch
from tests.test_save_only import PersistenceDevice,LuaRuntime
from modules.capture_services.openwrt.wireless_setup_manager import WirelessSetupManager
from modules.capture_services.openwrt.wireless_setup_models import WirelessRequest
from modules.capture_services.openwrt.signal_manager import SignalError
from modules.capture_services.openwrt.wireless_setup_commands import BOOT


class RandomizedDirectoryDevice(PersistenceDevice):
    def __init__(self):
        super().__init__();self.directory_map={};self.duplicate=False;self.hide_directories=False
        self.lua.globals().save_load=lambda directory,c:c in self.data(directory)
    def resolve(self,directory):
        return self.directory_map.get(directory)
    def data(self,directory):
        sid=self.resolve(directory)
        return self.sessions[sid] if sid in self.sessions else self.config
    def cursor_get(self,directory,*args):return super().cursor_get(self.resolve(directory),*args)
    def cursor_foreach(self,directory,*args):return super().cursor_foreach(self.resolve(directory),*args)
    def cursor_changes(self,directory,*args):return super().cursor_changes(self.resolve(directory),*args)
    def session_commit(self,directory,*args):return super().session_commit(self.resolve(directory),*args)
    def call(self,obj,method,args):
        value,error=super().call(obj,method,args)
        if obj=='session' and method=='create' and error is None:
            sid=self.unwrap(value)['ubus_rpc_session']
            directory=hashlib.sha256(sid.encode()).hexdigest()[:32]
            self.directory_map[directory]=sid
            if self.duplicate:self.directory_map['f'*32]=sid
        return value,error
    def read(self,command):
        if 'for d in /tmp/run/rpcd/uci-*' in command:
            if self.hide_directories:return ''
            return ''.join('/tmp/run/rpcd/uci-'+d+'\n' for d,sid in self.directory_map.items() if sid in self.sessions)
        return super().read(command)


@unittest.skipUnless(LuaRuntime,'Lua runtime unavailable')
class RandomizedSavedirTests(unittest.TestCase):
    def request(self):return WirelessRequest('ap','radio7','Fixture AP','fixture-password',options={'encryption':'psk2','network':'lan'})
    def test_old_assumption_reproduces_customer_unconfirmed_without_saving(self):
        device=RandomizedDirectoryDevice();manager=WirelessSetupManager(device);before=copy.deepcopy(device.config)
        legacy=BOOT.replace('local path=save_session_directory((selected_configs or save_configs)[1])','local path="/tmp/run/rpcd/uci-"..sid()')
        with patch('tests.test_wireless_setup.BOOT',legacy):
            with self.assertRaises(SignalError) as caught:manager.save(self.request(),threading.Event(),Mock(),apply=False)
        self.assertEqual(caught.exception.code,'unconfirmed');self.assertEqual(device.config,before)
    def test_new_and_old_directory_styles_save_without_reload(self):
        for cls in (PersistenceDevice,RandomizedDirectoryDevice):
            device=cls();manager=WirelessSetupManager(device)
            result=manager.save(self.request(),threading.Event(),Mock(),apply=False)
            self.assertTrue(result['saved']);self.assertFalse(result['applied'])
            self.assertEqual(device.config['wireless'][result['section']]['ssid'],'Fixture AP')
            self.assertFalse(any(name.startswith('lxg_save_') for cfg in device.config.values() for name in cfg))
            self.assertFalse(device.sessions);self.assertNotIn(('uci','apply'),device.calls)
            self.assertNotIn(('network','reload'),device.calls)
    def test_save_and_apply_with_old_and_randomized_directories(self):
        for cls in (PersistenceDevice,RandomizedDirectoryDevice):
            device=cls()
            result=WirelessSetupManager(device).save(self.request(),threading.Event(),Mock(),apply=True)
            self.assertEqual(device.config['wireless'][result['section']]['ssid'],'Fixture AP')
            self.assertIn(('uci','apply'),device.calls)
            self.assertFalse(device.sessions)

    def test_marker_delta_history_does_not_force_unchanged_disk_write(self):
        from dataclasses import replace
        device=RandomizedDirectoryDevice();manager=WirelessSetupManager(device)
        request=self.request()
        first=manager.save(request,threading.Event(),Mock(),apply=False)
        device.commits.clear()
        original=device.cursor_changes
        def changes(directory,config):
            if directory:return device.wrap({config:{'transient_marker_history':True}})
            return original(directory,config)
        device.lua.globals().save_changes=changes
        result=manager.save(replace(request,section=first['section'],password=''),threading.Event(),Mock(),apply=False)
        self.assertTrue(result['saved']);self.assertEqual(device.commits,[])
        self.assertFalse(device.sessions)

    def test_foreign_session_is_neither_committed_nor_destroyed(self):
        device=RandomizedDirectoryDevice()
        sid='e'*32
        foreign=copy.deepcopy(device.config)
        foreign['wireless']['home']['ssid']='foreign-private-change'
        device.sessions[sid]=copy.deepcopy(foreign);device.directory_map['d'*32]=sid
        result=WirelessSetupManager(device).save(self.request(),threading.Event(),Mock(),apply=False)
        self.assertTrue(result['saved'])
        self.assertEqual(device.sessions[sid],foreign)
        self.assertEqual(device.config['wireless']['home']['ssid'],'Home')
        self.assertEqual(device.config['wireless'][result['section']]['ssid'],'Fixture AP')

    def test_marker_cleanup_failure_never_commits(self):
        device=RandomizedDirectoryDevice();before=copy.deepcopy(device.config)
        original=device.call
        def reject_delete(obj,method,args):
            value=device.unwrap(args)
            if obj=='uci' and method=='delete' and value.get('section','').startswith('lxg_save_'):
                return None,7
            return original(obj,method,args)
        device.lua.globals().bridge_call=reject_delete
        with self.assertRaises(SignalError):
            WirelessSetupManager(device).save(self.request(),threading.Event(),Mock(),apply=False)
        self.assertEqual(device.config,before);self.assertFalse(device.commits);self.assertFalse(device.sessions)

    def test_new_directory_bridge_save_does_not_disable_current_dhcp(self):
        for mode in ('wds','relayd'):
            device=RandomizedDirectoryDevice()
            device.config['dhcp']={'lan':{'.type':'dhcp','interface':'lan','ignore':'0','ra':'server','dhcpv6':'server'}}
            device.config['firewall']['zone_lan'].update(input='ACCEPT',output='ACCEPT',forward='ACCEPT')
            device.files.update({'/etc/config/'+k:json.dumps(v,sort_keys=True) for k,v in device.config.items()})
            before=copy.deepcopy(device.config['dhcp'])
            request=WirelessRequest('sta','radio7','Bridge fixture','fixture-password',
                options={'encryption':'psk2','bridge_mode':mode,'network':'lan','proto':'dhcp','zone':'lan' if mode=='relayd' else 'wan'})
            result=WirelessSetupManager(device).save(request,threading.Event(),Mock(),apply=False)
            self.assertTrue(result['saved']);self.assertFalse(result['applied'])
            self.assertEqual(device.config['dhcp'],before)
            self.assertEqual(device.config['wireless'][result['section']]['rm_suspended'],'1')
            self.assertNotIn(('uci','apply'),device.calls);self.assertNotIn(('network','reload'),device.calls)

    def test_new_directory_common_lan_and_system_save_keep_other_configuration(self):
        from tests.test_common_settings import CommonDevice
        from modules.capture_services.openwrt.common_settings import read_settings,save_settings
        class Device(RandomizedDirectoryDevice,CommonDevice):pass
        for legacy in (False,True):
            device=Device();cancel=threading.Event()
            if legacy:device.config['network']['lan'].update(type='bridge',ifname='eth0.1');device.config['network']['lan'].pop('device',None)
            untouched=copy.deepcopy(device.config['wireless'])
            for config in ('system','network'):
                review=read_settings(device,config,cancel);values=dict(review['values'])
                if config=='system':values['hostname']='fixture-host'
                else:values['ipaddr']='192.168.22.1';values['auto']='1'
                result=save_settings(device,review,values,cancel)
                self.assertTrue(result['saved']);self.assertEqual(result['applied'],config=='system')
            self.assertEqual(device.config['wireless'],untouched)
            self.assertEqual(device.config['system']['cfg01']['hostname'],'fixture-host')
            self.assertNotIn(('uci','apply'),device.calls)

    def test_new_directory_policy_commit_uses_same_verified_session(self):
        from tests.test_router_policy import PolicyDevice
        class Device(RandomizedDirectoryDevice,PolicyDevice):pass
        device=Device();policy=device.policy
        plan=policy.preview('initial',{'items':['timezone']},device.cancel)
        result=policy.apply(plan,None,None,device.cancel,Mock())
        self.assertEqual(result['outcome'],'applied')
        self.assertEqual(device.config['system']['cfg01']['zonename'],'Asia/Shanghai')
        self.assertFalse(any(name.startswith('lxg_save_') for cfg in device.config.values() for name in cfg))

    def test_invalid_or_overflow_directory_listing_refuses_commit(self):
        for listing in ('overflow\n','/tmp/run/rpcd/uci-not-a-session\n'):
            device=RandomizedDirectoryDevice();before=copy.deepcopy(device.config)
            original=device.read
            device.lua.globals().bridge_read=lambda command: listing if 'for d in /tmp/run/rpcd/uci-*' in command else original(command)
            with self.assertRaises(SignalError) as caught:
                WirelessSetupManager(device).save(self.request(),threading.Event(),Mock(),apply=False)
            self.assertEqual(caught.exception.code,'save_unsupported')
            self.assertEqual(device.config,before);self.assertFalse(device.commits);self.assertFalse(device.sessions)

    def test_missing_and_ambiguous_directory_stop_without_commit(self):
        for mode in ('missing','duplicate'):
            device=RandomizedDirectoryDevice();before=copy.deepcopy(device.config)
            if mode=='missing':device.hide_directories=True
            else:device.duplicate=True
            with self.assertRaises(SignalError) as caught:
                WirelessSetupManager(device).save(self.request(),threading.Event(),Mock(),apply=False)
            self.assertEqual(caught.exception.code,'save_unsupported')
            self.assertEqual(device.config,before);self.assertFalse(device.commits);self.assertFalse(device.sessions)
