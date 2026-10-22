"""Execute real save-only Lua with isolated session/file-system fixtures."""
import copy
import hashlib
import json
import threading
import unittest
from unittest.mock import Mock
from dataclasses import replace
from tests.test_pending_config import PendingDevice
from tests.test_wireless_setup import LuaRuntime, WirelessRequest
from modules.capture_services.openwrt.wireless_setup_manager import WirelessSetupManager
from modules.capture_services.openwrt.signal_manager import SignalError


class PersistenceDevice(PendingDevice):
    def __init__(self):
        super().__init__()
        self.pending.clear()
        self.files = {"/etc/config/"+k:json.dumps(v,sort_keys=True) for k,v in self.config.items()}
        self.runtime = copy.deepcopy(self.config)
        self.lua.globals().save_load = lambda sid,c: c in (self.sessions[sid] if sid else self.config)
        self.lua.globals().save_get = self.cursor_get
        self.lua.globals().save_foreach = self.cursor_foreach
        self.lua.globals().save_changes = self.cursor_changes
        self.lua.globals().save_commit = self.session_commit
        self.lua.execute("""
package.loaded.uci.cursor=function(confdir,savedir)
 local sid=savedir and savedir:match("^/tmp/run/rpcd/uci%-(%x+)$")
 return {
 get_confdir=function() return confdir or "/etc/config" end,
 get_savedir=function() return savedir or "/tmp/.uci" end,
 load=function(self,c) return save_load(sid,c) end,
 changes=function(self,c) return save_changes(sid,c) end,
 get=function(self,c,s,k) return save_get(sid,c,s,k,false) end,
 get_all=function(self,c,s) return save_get(sid,c,s,nil,true) end,
 foreach=function(self,c,t,cb) save_foreach(sid,c,t,cb) end,
 commit=function(self,c) return save_commit(sid,c) end}
end
""")

    def cursor_get(self,sid,config,section,key,all_values):
        row=(self.sessions[sid] if sid else self.config).get(config,{}).get(section)
        if row is None:return None
        return self.wrap(dict(row,**{".name":section})) if all_values else row.get(key or ".type")

    def cursor_foreach(self,sid,config,kind,callback):
        for name,row in list((self.sessions[sid] if sid else self.config).get(config,{}).items()):
            if kind is None or row.get(".type")==kind:
                callback(self.wrap(dict(row,**{".name":name})))

    def cursor_changes(self,sid,config):
        if not sid:return self.wrap({config:self.pending[config]}) if self.pending.get(config) else self.wrap({})
        changed=self.sessions[sid].get(config)!=self.config.get(config)
        return self.wrap({config:{"staged":True}} if changed else {})

    def session_commit(self,sid,config):
        self.commits.append(config)
        if config==self.commit_fail:return False
        self.config[config]=copy.deepcopy(self.sessions[sid][config])
        self.files["/etc/config/"+config]=json.dumps(self.config[config],sort_keys=True)
        return True

    def read(self,command):
        if command.startswith("umask 077; mktemp") and "rouxiaoguan-save" in command:
            return "/tmp/rouxiaoguan-save.TEST01\n"
        return super().read(command)


@unittest.skipUnless(LuaRuntime,"Lua runtime unavailable")
class SaveOnlyTests(unittest.TestCase):
    def setUp(self):
        self.device=PersistenceDevice()
        self.manager=WirelessSetupManager(self.device)
        self.cancel=threading.Event()

    def test_new_ap_saved_disabled_without_apply_or_live_checks(self):
        before=copy.deepcopy(self.device.config)
        request=WirelessRequest("ap","radio7","Deferred","save-only-secret",options={"encryption":"psk2","network":"lan"})
        self.manager._wait_ready=Mock(side_effect=AssertionError("Must not verify runtime"))
        result=self.manager.save(request,self.cancel,Mock(),apply=False)
        row=self.device.config["wireless"][result["section"]]
        self.assertEqual(row["disabled"],"1")
        self.assertEqual(row["key"],"save-only-secret")
        self.assertEqual(self.device.config["wireless"]["radio7"],before["wireless"]["radio7"])
        self.assertEqual(self.device.config["wireless"]["home"],before["wireless"]["home"])
        self.assertEqual(self.device.runtime,before)
        self.assertNotIn(("uci","apply"),self.device.calls)
        self.assertNotIn(("uci","commit"),self.device.calls)
        self.assertNotIn(("uci","confirm"),self.device.calls)
        self.assertFalse(result["applied"])
        self.assertFalse(self.device.sessions)
        self.assertFalse(any("rouxiaoguan-save" in p for p in self.device.files))

    def test_existing_ap_saved_disabled_keeps_old_password_when_not_changed(self):
        self.device.config["wireless"]["radio7"]["disabled"]="1"
        self.device.files["/etc/config/wireless"]=json.dumps(self.device.config["wireless"],sort_keys=True)
        request=WirelessRequest("ap","radio7","Home edited","",section="home",
                               options={"encryption":"psk2","network":"lan"})
        result=self.manager.save(request,self.cancel,Mock(),apply=False)
        self.assertEqual(self.device.config["wireless"]["home"]["disabled"],"1")
        self.assertEqual(self.device.config["wireless"]["home"]["key"],"original-password")
        self.assertEqual(self.device.config["wireless"]["radio7"]["disabled"],"1")
        self.assertEqual(self.device.runtime["wireless"]["home"]["ssid"],"Home")
        self.assertFalse(self.device.sessions)

    def test_relay_save_keeps_other_station_enabled_and_rolls_back_partial_commit_failure(self):
        request=WirelessRequest("sta","radio7","Upper","save-only-secret",
            options={"encryption":"psk2","proto":"dhcp","zone":"wan"})
        before=copy.deepcopy(self.device.config)
        self.device.commit_fail="firewall"
        with self.assertRaises(SignalError):
            self.manager.save(request,self.cancel,Mock(),apply=False)
        self.assertEqual(self.device.config,before)
        self.assertNotIn(("uci","apply"),self.device.calls)
        self.assertFalse(self.device.sessions)

    def test_backup_failure_writes_nothing_and_cleans_its_files(self):
        self.device.backup_fail=True
        before=copy.deepcopy(self.device.config)
        request=WirelessRequest("ap","radio7","Deferred","save-only-secret",options={"encryption":"psk2"})
        with self.assertRaises(SignalError):
            self.manager.save(request,self.cancel,Mock(),apply=False)
        self.assertEqual(self.device.config,before)
        self.assertFalse(self.device.commits)
        self.assertFalse(self.device.sessions)
        self.assertFalse(any("rouxiaoguan-save" in p for p in self.device.files))

    def test_saved_relay_is_disabled_and_preserves_other_stations(self):
        self.device.config["wireless"]["upper"]={".type":"wifi-iface","device":"radio7","mode":"sta",
            "ssid":"Existing","network":"wan","encryption":"psk2","key":"old-password","disabled":"0"}
        self.device.files["/etc/config/wireless"]=json.dumps(self.device.config["wireless"],sort_keys=True)
        before=copy.deepcopy(self.device.config["wireless"]["upper"])
        request=WirelessRequest("sta","radio7","Deferred","test-password",
            options={"encryption":"psk2","proto":"dhcp","zone":"wan"})
        result=self.manager.save(request,self.cancel,Mock(),apply=False)
        self.assertEqual(self.device.config["wireless"][result["section"]]["disabled"],"1")
        self.assertEqual(self.device.config["wireless"]["upper"],before)
        self.assertNotIn(("uci","apply"),self.device.calls)

    def test_bridge_save_only_preserves_dhcp_and_manual_enable_uses_existing_toggle(self):
        for mode in ("wds","relayd"):
            with self.subTest(mode=mode):
                self.setUp()
                self.device.config["dhcp"]={"lan":{".type":"dhcp","interface":"lan","ignore":"0","ra":"server","dhcpv6":"server"}}
                self.device.config["firewall"]["zone_lan"].update(input="ACCEPT",output="ACCEPT",forward="ACCEPT")
                self.device.files.update({"/etc/config/"+k:json.dumps(v,sort_keys=True) for k,v in self.device.config.items()})
                before=copy.deepcopy(self.device.config["dhcp"])
                request=WirelessRequest("sta","radio7","Deferred","test-password",
                    options={"encryption":"psk2","bridge_mode":mode,"network":"lan","proto":"dhcp","zone":"lan" if mode=="relayd" else "wan"})
                result=self.manager.save(request,self.cancel,Mock(),apply=False)
                row=self.device.config["wireless"][result["section"]]
                self.assertEqual((row["disabled"],row["rm_suspended"]),("1","1"))
                self.assertEqual(self.device.config["dhcp"],before)
                if mode=="relayd":self.assertEqual(self.device.config["network"][row["rm_bridge"]]["disabled"],"1")
                self.assertNotIn(("uci","apply"),self.device.calls)
                # Execute the real existing enable stage and observe DHCP being disabled only now.
                sid=self.manager._call("session",self.cancel)["sid"]
                env=self.manager.inspect(self.cancel)
                from dataclasses import asdict
                self.manager._call("stage_toggle",self.cancel,sid=sid,values=asdict(replace(request,section=result["section"])),
                    enabled=True,token="1234567890abcdef",revision=env.data["revision"],dhcp_revision=env.data["dhcp_revision"])
                self.assertEqual(self.device.config["dhcp"]["lan"]["ignore"],"1")
                self.assertEqual(self.device.config["wireless"][result["section"]]["disabled"],"0")
                self.assertNotIn("rm_suspended",self.device.config["wireless"][result["section"]])

    def test_edit_active_bridge_save_only_restores_saved_dhcp_config_without_reload(self):
        for mode in ("wds","relayd"):
            with self.subTest(mode=mode):
                self.setUp()
                self.device.config["dhcp"]={"lan":{".type":"dhcp","interface":"lan","ignore":"0","ra":"server"}}
                self.device.config["firewall"]["zone_lan"].update(input="ACCEPT",output="ACCEPT",forward="ACCEPT")
                self.device.files.update({"/etc/config/"+k:json.dumps(v,sort_keys=True) for k,v in self.device.config.items()})
                original=copy.deepcopy(self.device.config["dhcp"])
                request=WirelessRequest("sta","radio7","Bridge","test-password",
                    options={"encryption":"psk2","bridge_mode":mode,"network":"lan","proto":"dhcp","zone":"lan" if mode=="relayd" else "wan"})
                # Stage the existing apply path (runtime confirmation is covered by bridge tests).
                env=self.manager.inspect(self.cancel)
                sid=self.manager._call("session",self.cancel)["sid"]
                from dataclasses import asdict
                value=self.manager._call("stage",self.cancel,sid=sid,values=asdict(request),temporary=False,token="0123456789abcdef",
                    revision=env.data["revision"],dhcp_revision=env.data["dhcp_revision"])
                self.manager._call("finish",self.cancel,sid=sid,confirm=True,unapplied=False)
                self.device.files.update({"/etc/config/"+k:json.dumps(v,sort_keys=True) for k,v in self.device.config.items()})
                self.device.calls.clear()
                result=self.manager.save(replace(request,section=value["section"],password=""),self.cancel,Mock(),apply=False)
                self.assertEqual(self.device.config["wireless"][result["section"]]["rm_suspended"],"1")
                self.assertEqual(self.device.config["dhcp"],original)
                self.assertNotIn(("uci","apply"),self.device.calls)


if __name__=="__main__":unittest.main()
