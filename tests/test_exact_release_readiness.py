"""Exact official releases, including early 21.02 status without an SSID field."""
import json
from pathlib import Path
import unittest
from tests.test_wireless_setup import LuaRuntime
from modules.capture_services.openwrt.wireless_capabilities import CAPABILITIES_LUA

PROFILES=json.loads((Path(__file__).parent/'fixtures/official_hostapd_exact_releases.json').read_text())

@unittest.skipUnless(LuaRuntime,'Lua unavailable')
class ExactReleaseReadinessTests(unittest.TestCase):
    def readiness(self,profile,*,observed='Running-test-AP',frequency=2412,enabled=True,link=True):
        lua=LuaRuntime(unpack_returned_tuples=True)
        values=lua.globals()
        sample=dict(profile['sample'] or {})
        if not enabled:sample['status']='DISABLED'
        values.reply=lua.table_from(sample);values.reply_error=profile['method_error']
        values.observed=observed;values.frequency=frequency;values.link=link
        lua.execute("""
        function optional(object,method,args)
         if object=='network.device' then return {up=link} end
         if object=='iwinfo' then return {mode='Master',ssid=observed,frequency=frequency} end
         return {}
        end
        bus={call=function(self,object,method,args)
         if method=='get_status' then return reply,reply_error end
         if method=='get_clients' then return {clients={},freq=2412},nil end
         return nil,4
        end}
        """+CAPABILITIES_LUA+"\nfunction check() return ap_ready('wlan-test','Running-test-AP') end")
        return values.check()

    def test_all_50_exact_source_shapes_have_a_verified_success_path(self):
        self.assertEqual(len(PROFILES),50)
        for profile in PROFILES:
            with self.subTest(release=profile['release']):self.assertTrue(self.readiness(profile))

    def test_early_21_02_releases_are_not_misrepresented_as_late_21_02(self):
        missing=[p['release'] for p in PROFILES if p['sample'] and 'ssid' not in p['sample']]
        self.assertEqual(missing,['21.02.0','21.02.1','21.02.2'])

    def test_legacy_and_early_status_need_actual_matching_name_and_frequency(self):
        for profile in PROFILES:
            if profile['sample'] and 'ssid' in profile['sample']:continue
            with self.subTest(release=profile['release']):
                self.assertFalse(self.readiness(profile,observed='Wrong-running-name'))
                self.assertFalse(self.readiness(profile,frequency=5180))

    def test_all_release_shapes_require_live_interface_and_never_override_disabled_status(self):
        for profile in PROFILES:
            with self.subTest(release=profile['release']):
                self.assertFalse(self.readiness(profile,link=False))
                if profile['sample']:self.assertFalse(self.readiness(profile,enabled=False))
