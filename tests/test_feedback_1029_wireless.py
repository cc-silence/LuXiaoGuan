"""October 29 regressions: early hostapd status and unrelated wireless switches."""
import copy
import json
import threading
import unittest
from unittest.mock import Mock, patch
from tests.test_wireless_setup import LuaRuntime
from tests.test_wireless_version_contracts import PROFILES, VersionDevice
from tests.test_router_policy import PolicyDevice
from tests.test_orphan_interfaces import OrphanDevice, ORPHAN
from modules.capture_services.openwrt.orphan_interface_manager import OrphanInterfaceManager
from modules.capture_services.openwrt.wireless_setup_manager import WirelessSetupManager
from modules.capture_services.openwrt.router_policy_plan import BOOT_SCRIPT


@unittest.skipUnless(LuaRuntime, 'Lua unavailable')
class EarlyHostapdTests(unittest.TestCase):
    def readiness(self, *, state='ENABLED', freq=5180, ssid=None, observed=None,
                  observed_freq=5180, mode='Master', error=None, link=True):
        profile=next(p for p in PROFILES if p['series']=='21.02')
        d=VersionDevice(profile)
        d.runtime_ssid=observed
        d.native_link_up=link
        original=d.call
        def call(obj, method, args):
            if obj.startswith('hostapd.') and method=='get_status':
                if error is not None:return None,error
                value=dict(status=state,freq=freq)
                if ssid is not None:value['ssid']=ssid
                return d.wrap(value),None
            value,err=original(obj,method,args)
            if obj=='iwinfo' and method=='info' and not err:
                value=d.unwrap(value);value.update(frequency=observed_freq,mode=mode)
                return d.wrap(value),None
            return value,err
        d.lua.globals().bridge_call=call
        result=json.loads(d.execute(dict(action='status',radio='radio7',section='home',mode='ap')))
        self.assertTrue(result['ok'])
        return result['result']['ready']

    def test_early_21_02_1_status_without_ssid_uses_running_iwinfo(self):
        self.assertTrue(self.readiness())

    def test_explicit_wrong_name_is_never_overridden(self):
        self.assertFalse(self.readiness(ssid='Old-name'))

    def test_missing_name_requires_matching_running_name_and_frequency(self):
        for options in (dict(observed='Old-name'),dict(observed_freq=2412),dict(mode='Client'),
                        dict(freq=None),dict(freq=0),dict(link=False)):
            with self.subTest(options=options):self.assertFalse(self.readiness(**options))

    def test_disabled_or_unavailable_status_cannot_fall_back_to_success(self):
        for options in (dict(state='DISABLED'),dict(state='DFS'),dict(error=4),dict(error=6)):
            with self.subTest(options=options):self.assertFalse(self.readiness(**options))


class EarlyPolicyDevice(PolicyDevice):
    def call(self,obj,method,args):
        if obj.startswith('hostapd.') and method=='get_status':
            return self.wrap(dict(status='ENABLED',freq=2412)),None
        if obj=='iwinfo' and method=='info':
            section=self.unwrap(args).get('device','').removeprefix('iface-')
            return self.wrap(dict(mode='Master',frequency=2412,
                             ssid=self.config['wireless'].get(section,{}).get('ssid',''))),None
        return super().call(obj,method,args)


@unittest.skipUnless(LuaRuntime, 'Lua unavailable')
class SwitchPreservationTests(unittest.TestCase):
    def populate(self,d):
        for index in range(8):
            d.config['wireless']['saved_sta'+str(index)]=dict({'.type':'wifi-iface'},mode='sta',
                device='radio7',network='wwan',ssid='Saved-'+str(index),
                disabled=str(index%2),lxg_boot_disabled='1')

    def test_ap_batch_with_early_hostapd_confirms_and_preserves_stations(self):
        d=EarlyPolicyDevice();self.populate(d)
        d.config['wireless']['default_radio7']['disabled']='0'
        before=copy.deepcopy(d.config['wireless'])
        plan=d.policy.preview('ap',{},d.cancel)
        result=d.policy.apply(plan,None,None,d.cancel,Mock())
        self.assertTrue(result['applied'])
        for section,row in before.items():
            if row.get('mode')=='sta':self.assertEqual(d.config['wireless'][section],row)
        self.assertNotEqual(d.config['wireless']['default_radio7']['ssid'],before['default_radio7']['ssid'])
        self.assertIn(('uci','confirm'),d.calls)
        self.assertNotIn(('uci','rollback'),d.calls)

    def test_both_startup_policy_choices_preserve_all_current_switches(self):
        for enabled in (False,True):
            with self.subTest(enabled=enabled):
                d=PolicyDevice();self.populate(d)
                if enabled:
                    d.files['/etc/init.d/luxiaoguan-wifi-policy']=BOOT_SCRIPT
                    d.flags['boot_hook_enabled']=True
                before=copy.deepcopy(d.config['wireless'])
                plan=d.policy.preview('boot',dict(enabled=enabled),d.cancel)
                self.assertFalse(plan['operations'])
                original_run=d.policy._run
                def run(name,*args,**kwargs):
                    if name in ('boot_off','boot_on'):return ''
                    return original_run(name,*args,**kwargs)
                with patch.object(d.policy,'_run',side_effect=run), patch(
                        'modules.capture_services.openwrt.router_boot_status.read_boot_status',
                        return_value=dict(boot_state=enabled)):
                    result=d.policy.apply(plan,None,None,d.cancel,Mock())
                self.assertEqual(result['outcome'],'saved')
                self.assertEqual(d.config['wireless'],before)
                self.assertNotIn(('uci','apply'),d.calls)

    def test_orphan_cleanup_does_not_commit_or_reload_wireless(self):
        d=OrphanDevice();self.populate(d);d.sync_files()
        before=copy.deepcopy(d.config['wireless']);runtime=copy.deepcopy(d.runtime)
        wireless_bytes=d.files['/etc/config/wireless']
        manager=OrphanInterfaceManager(WirelessSetupManager(d));cancel=threading.Event()
        review=manager.preview(cancel)
        result=manager.cleanup(review,[ORPHAN],cancel,Mock())
        self.assertFalse(result['applied'])
        self.assertEqual(d.config['wireless'],before)
        self.assertEqual(d.files['/etc/config/wireless'],wireless_bytes)
        self.assertEqual(d.runtime,runtime)
        self.assertNotIn(('uci','apply'),d.calls)
        self.assertNotIn(('network','reload'),d.calls)
