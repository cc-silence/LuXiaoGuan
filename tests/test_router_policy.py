"""Real Lua policy transactions against deterministic UCI/filesystem fixtures."""
import copy
import hashlib
import json
import re
import shlex
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
from tests.test_common_settings import CommonDevice
from tests.test_wireless_setup import LuaRuntime,CAPABILITIES
from modules.capture_services.openwrt.router_policy_commands import BOOT,FILES
from modules.capture_services.openwrt.router_policy_plan import build_plan,BOOT_SCRIPT,OFFICIAL_RESCUE,boot_state,ITEMS
from modules.capture_services.openwrt.router_policy import RouterPolicyManager
from modules.capture_services.openwrt.command_runner import RemoteCommandResult


class PolicyDevice(CommonDevice):
    def __init__(self,legacy=False):
        super().__init__(legacy)
        self.config["system"]["ntp"]={".type":"timeserver","enabled":"1","server":["old.ntp"]}
        self.config["dropbear"]={"ssh1":{".type":"dropbear","Interface":"wan"}}
        self.config["uhttpd"]={"main":{".type":"uhttpd","home":"/www","listen_http":["0.0.0.0:80"],"listen_https":["0.0.0.0:443"],"redirect_https":"1"}}
        self.config["luci"]={"main":{".type":"core","lang":"auto"}}
        self.config["fstab"]={"cfg_global":{".type":"global","auto_mount":"1","anon_swap":"1"},"known":{".type":"swap","enabled":"1"}}
        self.config["dhcp"]={"dns":{".type":"dnsmasq"},"lan":{".type":"dhcp","ra":"server","dhcpv6":"server"}}
        if not legacy:
            self.config["network"]["bridge"]={".type":"device","name":"br-lan","type":"bridge","ports":["lan1","lan2"]}
        self.config["wireless"]["radio7"].update(band="2g",channel="1",htmode="HT20")
        self.config["wireless"]["default_radio7"]={".type":"wifi-iface","mode":"ap","ssid":"OpenWrt","device":"radio7","network":"lan","disabled":"1","encryption":"none"}
        self.config["wireless"]["station"]={".type":"wifi-iface","mode":"sta","ssid":"Upstream","device":"radio7","network":"wwan","key":"preserve-station-password"}
        self.config["network"]["wwan"]={".type":"interface","proto":"dhcp","hostname":"OldPhone"}
        self.config["firewall"]["zone_wan"]["network"]=["wan","wwan"]
        self.files={"/etc/config/"+k:json.dumps(v,sort_keys=True) for k,v in self.config.items()}
        self.files["/www/index.html"]="<html>Old</html>"
        self.files["/etc/rc.button/failsafe"]=OFFICIAL_RESCUE
        self.flags=dict(chinese=True,time=True,mount=True,attended=False,old_watch=False,boot_hook_enabled=False)
        self.flags.update(firewall3=legacy,firewall4=not legacy)
        self.interfaces=[dict(interface="lan",**{"ipv4-address":[dict(address="192.168.8.1",mask=24)]}),
                         dict(interface="wwan",**{"ipv4-address":[dict(address="10.2.3.4",mask=24)]})]
        self.board=dict(model="Fixture",release=dict(distribution="OpenWrt",version="19.07" if legacy else "25.12"))
        self.ssh=SimpleNamespace(_client=object(),host_fingerprint="pinned",connected=True,_host="192.168.8.1",_port=22,_user="root")
        self.manager=SimpleNamespace(ssh=self.ssh,runner=self)
        self.policy=RouterPolicyManager(self.manager)
        self.policy._apply_channel_factory=lambda *args,**kwargs:PolicyChannel(self)
        self.cancel=threading.Event()
        self.lua.globals().file_read=lambda path:self.files.get(path)
        self.lua.globals().file_write=lambda path,data:self.files.__setitem__(path,data) or True
        self.lua.globals().file_rename=self.rename
        self.lua.execute(r'''
io.open=function(path,mode)
 if mode=="r" then
  local content=file_read(path)
  if content==nil then return nil end
  return {read=function(self,n) return content:sub(1,n) end,close=function() return true end}
 end
 return {write=function(self,data) return file_write(path,data) end,close=function() return true end}
end
os.rename=function(a,b) return file_rename(a,b) end
''')

    def rename(self,a,b):
        if a not in self.files:return None
        self.files[b]=self.files.pop(a);return True

    def read(self,command):
        if command.startswith("cat /tmp/TZ"):return self.config["system"]["cfg01"]["timezone"]
        if command.startswith("cat /proc/sys/kernel/hostname"):return self.config["system"]["cfg01"]["hostname"]
        if command.startswith("ubus -v list uci"):return '"apply" "confirm" "rollback"'
        if command.startswith("test ! -e"):
            paths=re.findall("\'([^\']+)\'",command)
            return "" if paths and paths[0] in self.files else "ok"
        if command.startswith("find /root"):return "0"
        if command.startswith("umask 077; mktemp -d /root/"):return "/root/luxiaoguan-policy.TEST01\n"
        if command.startswith("("):
            if "grep -E" in command and "/etc/rc.local" in command:return "ok" if self.flags["old_watch"] else ""
            if "test -f /usr/lib/lua/luci/i18n" in command:return "ok" if self.flags["chinese"] else ""
            if "test -x /sbin/fw3" in command:return "ok" if self.flags["firewall3"] else ""
            if "test -x /sbin/fw4" in command:return "ok" if self.flags["firewall4"] else ""
            if "test -x /etc/init.d/sysntpd" in command:return "ok" if self.flags["time"] else ""
            if "test -x /etc/init.d/attendedsysupgrade" in command:return "ok" if self.flags["attended"] else ""
            if "command -v block" in command:return "ok" if self.flags["mount"] else ""
            if "test -L /etc/rc.d/S18luxiaoguan-wifi-policy" in command:return "ok" if self.flags["boot_hook_enabled"] else ""
            if "/www/luci-static/resources/view/" in command:return "ok"
            if "/usr/lib/lua/luci/" in command and "test -f" in command:return ""
            if "test ! -e" in command:
                paths=re.findall("'([^']+)'",command)
                return "" if paths and paths[0] in self.files else "ok"
        return super().read(command)

    def call(self,obj,method,arguments):
        args=self.unwrap(arguments)
        if obj=="network" and method=="reload":
            self.calls.append((obj,method));return self.wrap({}),None
        if obj=="network.device" and "name" in args:
            section=args["name"].removeprefix("iface-")
            return self.wrap(dict(up=True,macaddr=self.config["wireless"].get(section,{}).get("macaddr","02:00:00:00:00:01"))),None
        if obj=="network.interface.lan":
            return self.wrap(dict(up=True,pending=False,proto=self.config["network"]["lan"].get("proto"),device=self.config["network"]["lan"].get("device"),**{"ipv4-address":[dict(address=self.config["network"]["lan"]["ipaddr"],mask=24)]})),None
        if obj=="system" and method=="board":return self.wrap(self.board),None
        if obj=="network.interface" and method=="dump":return self.wrap(dict(interface=self.interfaces)),None
        if obj=="uci" and method=="order":
            cfg=self.sessions[args["ubus_rpc_session"]][args["config"]]
            self.sessions[args["ubus_rpc_session"]][args["config"]]={s:cfg[s] for s in args["sections"]}
            return None,None
        result=super().call(obj,method,arguments)
        if obj=="uci" and method in {"apply","rollback"} and result[1] is None:
            self.files.update({"/etc/config/"+k:json.dumps(v,sort_keys=True) for k,v in self.config.items()})
        return result

    def execute_policy(self,payload):
        self._output=[]
        self.lua.globals().arg=self.wrap({1:json.dumps(payload)})
        self.lua.execute(BOOT)
        return "".join(self._output)

    def run(self,name,command,timeout,cancel_event=None,output_limit=None):
        if name.startswith("router_policy_") and " <<'LXG_POLICY'" in command:
            payload=json.loads(shlex.split(command.split(" <<",1)[0])[2])
            return RemoteCommandResult(name,0,self.execute_policy(payload))
        return super().run(name,command,timeout,cancel_event)


class PolicyChannel:
    def __init__(self,device):
        self.runner=device
        self.manager=RouterPolicyManager(device.manager)
        self.manager.lua_bin="lua"
        self.ssh=Mock()
        self.calls=[]
    def connect(self,host,timeout):pass
    def call(self,action,timeout=8,**values):
        self.calls.append(action)
        return self.manager._call(action,threading.Event(),timeout=timeout,**values)
    def close(self):pass


def environment():
    return dict(radios=[dict(name="radio7",band="2g",capabilities=CAPABILITIES,channels=[dict(mhz=2412,restricted=False)])])


@unittest.skipUnless(LuaRuntime,"Lua runtime unavailable")
class PolicyTests(unittest.TestCase):
    def setUp(self):self.d=PolicyDevice();self.cancel=self.d.cancel;self.p=self.d.policy

    def test_snapshot_is_readonly_redacts_all_existing_passwords_and_handles_old_layout(self):
        for legacy in (False,True):
            d=PolicyDevice(legacy);before=copy.deepcopy(d.config)
            snapshot=d.policy.read(d.cancel)
            self.assertNotIn("preserve-station-password",json.dumps(snapshot))
            self.assertNotIn("original-password",json.dumps(snapshot))
            self.assertEqual(d.config,before);self.assertFalse(d.commits)
            self.assertTrue(snapshot["boot_state"]);self.assertIn("wireless",snapshot["pages"])

    def test_pending_changes_and_symlinks_are_rejected_before_writing(self):
        self.d.set_changes("system",dict(cfg01=dict(hostname="external")))
        with self.assertRaisesRegex(RuntimeError,"."):self.p.read(self.cancel)
        self.assertFalse(self.d.commits)
        self.d.pending.clear();self.d.symlinks.add("/etc/config/network")
        with self.assertRaises(RuntimeError):self.p.read(self.cancel)
        self.assertFalse(self.d.commits)

    def test_initial_plan_leaves_ipv6_request_limit_physical_ports_and_passwords_untouched(self):
        s=self.p.read(self.cancel);before=copy.deepcopy(self.d.config)
        plan=build_plan(s,"initial",dict(items=list(ITEMS)),environment())
        flat=json.dumps(plan["operations"])
        for forbidden in ("ip6assign","ra_slaac","max_requests","lan1","lan2"):self.assertNotIn(forbidden,flat)
        self.assertEqual(plan["new_ap"]["radio"],"radio7")
        self.assertEqual(plan["new_ap"]["password"],"luxiaoguan123")
        self.assertEqual(self.d.config,before)
        self.assertTrue(any("\u5347\u7ea7" in x for x in plan["skipped"]))
        self.assertEqual(plan["new_address"],"192.168.11.1")

    def test_ipv6_ping_drop_does_not_remove_neighbor_or_packet_too_big_rules(self):
        s=self.p.read(self.cancel)
        plan=build_plan(s,"initial",dict(items=["ping","web","igmp"]))
        order=plan["operations"][-1]
        self.assertEqual(order["action"],"order")
        self.assertEqual(order["sections"][:4],["lxg_initial_ping4","lxg_initial_ping6","lxg_initial_web","lxg_initial_igmp"])
        self.p._transaction(s,plan["operations"],self.cancel)
        rules=self.d.config["firewall"]
        self.assertEqual(rules["lxg_initial_ping6"]["icmp_type"],["echo-request"])
        self.assertEqual(list(rules)[:4],order["sections"][:4])
        self.assertEqual(rules["lxg_initial_web"]["dest_port"],["80","443"])

    def test_station_batch_preserves_credentials_and_existing_identity_restore_contract(self):
        s=self.p.read(self.cancel);plan=build_plan(s,"station")
        self.p._transaction(s,plan["operations"],self.cancel)
        row=self.d.config["wireless"]["station"]
        self.assertEqual(row["key"],"preserve-station-password")
        saved=json.loads(row["rm_identity_backup"])
        self.assertEqual(saved["network"],"wwan");self.assertEqual(saved["hostname"],"OldPhone")
        self.assertEqual(saved["applied_mac"],row["macaddr"])
        self.assertNotEqual(row["macaddr"],self.d.config["wireless"]["home"].get("macaddr"))

    def test_shared_network_with_access_point_is_never_rewritten(self):
        self.d.config["wireless"]["other"]=dict(self.d.config["wireless"]["station"],mode="ap")
        self.d.files["/etc/config/wireless"]=json.dumps(self.d.config["wireless"],sort_keys=True)
        with self.assertRaises(ValueError):build_plan(self.p.read(self.cancel),"station")
        self.assertFalse(self.d.commits)

    def test_ap_batch_retains_each_password_and_station_address(self):
        s=self.p.read(self.cancel);plan=build_plan(s,"ap")
        self.p._transaction(s,plan["operations"],self.cancel)
        self.assertEqual(self.d.config["wireless"]["home"]["key"],"original-password")
        self.assertNotEqual(self.d.config["wireless"]["home"]["ssid"],"Home")
        self.assertNotIn("macaddr",self.d.config["wireless"]["station"])

    def test_missing_or_ambiguous_24g_preserves_default_before_any_commit(self):
        s=self.p.read(self.cancel)
        for env in (dict(radios=[]),dict(radios=[dict(name="radio7",band="5g",channels=[dict(mhz=5180)])])):
            with self.assertRaises(ValueError):build_plan(s,"initial",dict(items=["replace_ap"]),env)
        self.assertIn("default_radio7",self.d.config["wireless"]);self.assertFalse(self.d.commits)

    def test_lan_conflict_is_rejected_and_no_radio_number_is_assumed(self):
        s=self.p.read(self.cancel)
        with self.assertRaises(ValueError):build_plan(s,"initial",dict(items=["lan"],lan_ip="10.2.3.1"))
        p=build_plan(s,"initial",dict(items=["replace_ap"]),environment())
        self.assertEqual(p["new_ap"]["radio"],"radio7")

    def test_failed_commit_restores_all_previously_committed_configs(self):
        s=self.p.read(self.cancel);plan=build_plan(s,"initial",dict(items=["timezone","ssh"]))
        before=copy.deepcopy(self.d.config);self.d.commit_fail="dropbear"
        with self.assertRaises(RuntimeError):self.p._transaction(s,plan["operations"],self.cancel)
        self.assertEqual(self.d.config,before);self.assertFalse(self.d.sessions)

    def test_cancel_after_stage_and_stale_review_never_commit(self):
        s=self.p.read(self.cancel);plan=build_plan(s,"ap")
        self.cancel.set()
        with self.assertRaises(InterruptedError):self.p._transaction(s,plan["operations"],self.cancel)
        self.assertFalse(self.d.commits)
        self.cancel.clear();self.d.files["/etc/config/network"]+=" "
        with self.assertRaises(RuntimeError):self.p._transaction(s,plan["operations"],self.cancel)
        self.assertFalse(self.d.commits)

    def test_fixed_files_are_backed_up_verified_and_conditionally_restored(self):
        s=self.p.read(self.cancel)
        p=build_plan(s,"initial",dict(items=["homepage"]))
        old={x["path"]:self.d.files[x["path"]] for x in p["files"]}
        value=self.p._call("files_apply",self.cancel,files=p["files"],token="a"*16)
        self.assertIn("luxiaoguan-policy",value["backup_dir"])
        self.assertTrue(self.d.files[value["backup_dir"]+"/journal.json"])
        self.p._call("files_restore",self.cancel,backup_dir=value["backup_dir"])
        self.assertEqual({k:self.d.files[k] for k in old},old)

    def test_modified_rescue_script_is_not_overwritten(self):
        self.d.files["/etc/rc.button/failsafe"]="custom"
        p=build_plan(self.p.read(self.cancel),"initial",dict(items=["rescue"]))
        self.assertFalse(p["files"]);self.assertTrue(p["skipped"])

    def test_boot_conflict_rejected_and_only_owned_disabled_stations_restored(self):
        s=self.p.read(self.cancel)
        p=build_plan(s,"boot",dict(enabled=False))
        self.assertEqual(p["files"][0]["content"],BOOT_SCRIPT);self.assertFalse(p["reboot"])
        self.d.flags["old_watch"]=True
        s=self.p.read(self.cancel);self.assertIsNone(s["boot_state"])
        with self.assertRaises(ValueError):build_plan(s,"boot",dict(enabled=False))

    def test_connection_switch_blocks_all_remote_calls(self):
        self.d.ssh._client=object()
        with self.assertRaises(RuntimeError):self.p.read(self.cancel)
        self.assertFalse(self.d.commits)

    def test_backup_failure_stops_before_any_policy_write(self):
        plan=self.p.preview("ap",{},self.cancel)
        with patch("modules.capture_services.openwrt.router_policy.RouterBackupManager") as backup:
            backup.return_value.export.side_effect=RuntimeError("disk full")
            with self.assertRaisesRegex(RuntimeError,"disk full"):self.p.apply(plan,"C:/backup.tar.gz",None,self.cancel,Mock())
        self.assertFalse(self.d.commits)
