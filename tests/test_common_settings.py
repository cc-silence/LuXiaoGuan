"""Common settings exercise the real Lua transaction with old and new UCI layouts."""
import copy
import json
import threading
import unittest
from tests.test_save_only import PersistenceDevice
from tests.test_wireless_setup import LuaRuntime
from modules.capture_services.openwrt.common_settings import read_settings,save_settings,validate_values
from modules.capture_services.openwrt.signal_manager import SignalError
from modules.capture_services.openwrt.command_runner import RemoteCommandResult


class CommonDevice(PersistenceDevice):
    def __init__(self,legacy=False):
        super().__init__()
        self.config["system"]={"cfg01":{"%TYPE%":"system","hostname":"OldRouter","timezone":"UTC0","zonename":"Etc/UTC","unrelated":"keep"}}
        self.config["system"]["cfg01"][".type"]=self.config["system"]["cfg01"].pop("%TYPE%")
        self.config["network"]["lan"].update(netmask="255.255.255.0",type="bridge",ifname="eth0.1") if legacy else self.config["network"]["lan"].update(netmask="255.255.255.0",device="br-lan")
        self.files={ "/etc/config/"+k:json.dumps(v,sort_keys=True) for k,v in self.config.items()}
        self.commands=[]
        self.reload_failure=False
    def cursor_get(self,*args):
        value=super().cursor_get(*args)
        return self.wrap(value) if isinstance(value,(list,dict)) else value
    def call(self,obj,method,args):
        if obj=="uci" and method=="delete":
            values=self.unwrap(args)
            row=self.sessions[values["ubus_rpc_session"]].get(values["config"],{}).get(values["section"])
            if row is None or ("option" in values and values["option"] not in row):
                return None,4
        if obj=="network.device":return self.wrap({"br-lan":{},"eth0":{},"lo":{}}),None
        return super().call(obj,method,args)
    def run(self,name,command,timeout,cancel_event=None):
        self.commands.append((name,command))
        if name.startswith("common_system_"):
            return RemoteCommandResult(name,1 if self.reload_failure else 0)
        return super().run(name,command,timeout,cancel_event)


@unittest.skipUnless(LuaRuntime,"Lua runtime unavailable")
class CommonSettingsTests(unittest.TestCase):
    def setUp(self):
        self.device=CommonDevice()
        self.cancel=threading.Event()
    def test_lan_save_and_readback_preserves_other_configs_and_runtime(self):
        review=read_settings(self.device,"network",self.cancel)
        self.assertEqual(review["devices"],["br-lan","eth0"])
        untouched=copy.deepcopy(self.device.config["wireless"])
        values=dict(review["values"],auto="1",ipaddr=["192.168.22.1/24","10.0.0.1/24"],gateway="",ip6addr=["fd00::1/64"],ip6prefix=["2001:db8::/48"])
        result=save_settings(self.device,review,values,self.cancel)
        self.assertFalse(result["applied"])
        self.assertEqual(read_settings(self.device,"network",self.cancel)["values"]["ipaddr"],values["ipaddr"])
        self.assertEqual(self.device.commits,["network"])
        self.assertEqual(self.device.config["wireless"],untouched)
        self.assertFalse(self.device.sessions)
        self.assertFalse(any(method=="apply" for _,method in self.device.calls))
    def test_absent_optional_lan_fields_are_not_deleted(self):
        review=read_settings(self.device,"network",self.cancel)
        values=dict(review["values"],auto="1",gateway="",broadcast="",ip6gw="",ip6addr=[],ip6prefix=[])
        result=save_settings(self.device,review,values,self.cancel)
        self.assertTrue(result["saved"])
        self.assertNotIn(("uci","delete"),self.device.calls)
        self.assertFalse(self.device.sessions)

    def test_clearing_existing_lan_option_still_deletes_it(self):
        self.device.config["network"]["lan"]["gateway"]="192.168.8.254"
        self.device.files["/etc/config/network"]=json.dumps(self.device.config["network"],sort_keys=True)
        review=read_settings(self.device,"network",self.cancel)
        save_settings(self.device,review,dict(review["values"],auto="1",gateway=""),self.cancel)
        self.assertNotIn("gateway",self.device.config["network"]["lan"])
        self.assertIn(("uci","delete"),self.device.calls)

    def test_real_delete_failure_is_not_ignored_or_committed(self):
        self.device.config["network"]["lan"]["gateway"]="192.168.8.254"
        self.device.files["/etc/config/network"]=json.dumps(self.device.config["network"],sort_keys=True)
        review=read_settings(self.device,"network",self.cancel)
        self.device.fail=("uci","delete")
        with self.assertRaises(SignalError) as error:
            save_settings(self.device,review,dict(review["values"],auto="1",gateway=""),self.cancel)
        self.assertEqual(error.exception.code,"rpc_uci_delete_2")
        self.assertEqual(self.device.config["network"]["lan"]["gateway"],"192.168.8.254")
        self.assertFalse(self.device.commits)
        self.assertFalse(self.device.sessions)

    def test_legacy_binding_preserved(self):
        self.device=CommonDevice(True)
        review=read_settings(self.device,"network",self.cancel)
        values=dict(review["values"],auto="1",ipaddr="192.168.9.1")
        save_settings(self.device,review,values,self.cancel)
        self.assertEqual(self.device.config["network"]["lan"]["ifname"],"eth0.1")
        self.assertEqual(self.device.config["network"]["lan"]["type"],"bridge")
        with self.assertRaises(ValueError):validate_values(review,dict(values,device="eth0"))
    def test_hostname_and_timezone_save_only_system_and_verify_runtime(self):
        review=read_settings(self.device,"system",self.cancel)
        values=dict(hostname="CMCC_G2T7",timezone="CST-8",zonename="Asia/Shanghai")
        result=save_settings(self.device,review,values,self.cancel)
        self.assertTrue(result["applied"])
        self.assertEqual(self.device.commits,["system"])
        self.assertEqual(self.device.config["system"]["cfg01"]["unrelated"],"keep")
        self.assertFalse(self.device.sessions)
    def test_saved_but_reload_failure_is_not_claimed_applied(self):
        review=read_settings(self.device,"system",self.cancel)
        self.device.reload_failure=True
        result=save_settings(self.device,review,dict(hostname="Phone123",timezone="UTC0",zonename="Etc/UTC"),self.cancel)
        self.assertTrue(result["saved"]);self.assertFalse(result["applied"])
    def test_stale_review_cancel_and_backup_failure_never_commit(self):
        review=read_settings(self.device,"network",self.cancel)
        values=dict(review["values"],auto="1")
        self.device.files["/etc/config/network"]+=" "
        with self.assertRaises(SignalError):save_settings(self.device,review,values,self.cancel)
        self.assertFalse(self.device.commits)
        self.assertFalse(self.device.sessions)
        review=read_settings(self.device,"network",self.cancel)
        self.device.backup_fail=True
        with self.assertRaises(SignalError):save_settings(self.device,review,values,self.cancel)
        self.assertFalse(self.device.commits)
        self.assertFalse(self.device.sessions)
    def test_invalid_inputs_rejected_before_remote_write(self):
        review=read_settings(self.device,"network",self.cancel)
        for update in ({"ipaddr":"garbage"},{"netmask":"255.0.255.0"},{"ip6addr":["oops"]},{"device":"x;reboot"},{"proto":"bad"}):
            with self.assertRaises(ValueError):validate_values(review,dict(review["values"],auto="1",**update))
        review=read_settings(self.device,"system",self.cancel)
        for host in ("bad name","-bad","bad;reboot","x"*64):
            with self.assertRaises(ValueError):validate_values(review,dict(hostname=host,timezone="UTC0",zonename="Etc/UTC"))


    def test_system_settings_do_not_require_wireless_config(self):
        for config in ("wireless","firewall","network"):
            self.device.config.pop(config)
            self.device.files.pop("/etc/config/"+config)
        review=read_settings(self.device,"system",self.cancel)
        result=save_settings(self.device,review,dict(hostname="Phone123",timezone="UTC0",zonename="Etc/UTC"),self.cancel)
        self.assertTrue(result["saved"])
        self.assertEqual(self.device.commits,["system"])

    def test_commit_failure_preserves_original_system_config(self):
        review=read_settings(self.device,"system",self.cancel)
        before=copy.deepcopy(self.device.config["system"])
        self.device.commit_fail="system"
        with self.assertRaises(SignalError):
            save_settings(self.device,review,dict(hostname="Phone123",timezone="UTC0",zonename="Etc/UTC"),self.cancel)
        self.assertEqual(self.device.config["system"],before)
        self.assertFalse(self.device.sessions)


    def test_cancel_after_staging_discards_without_commit(self):
        review=read_settings(self.device,"network",self.cancel)
        values=dict(review["values"],auto="1",ipaddr="192.168.55.1")
        original=self.device.run
        def run(name,command,timeout,cancel_event=None):
            result=original(name,command,timeout,cancel_event)
            if name=="wireless_setup_common_stage":
                self.cancel.set()
            return result
        self.device.run=run
        with self.assertRaises(InterruptedError):
            save_settings(self.device,review,values,self.cancel)
        self.assertFalse(self.device.commits)
        self.assertFalse(self.device.sessions)
        self.assertEqual(self.device.config["network"]["lan"]["ipaddr"],"192.168.8.1")


    def test_external_edit_between_commit_review_and_backup_is_preserved(self):
        review=read_settings(self.device,"system",self.cancel)
        values=dict(hostname="Phone123",timezone="UTC0",zonename="Etc/UTC")
        original_run=self.device.run
        original_read=self.device.read
        in_commit=False
        hashes=0
        def run(name,command,timeout,cancel_event=None):
            nonlocal in_commit
            in_commit=name=="wireless_setup_common_commit"
            return original_run(name,command,timeout,cancel_event)
        def read(command):
            nonlocal hashes
            if in_commit and "sha256sum" in command and "/etc/config/system" in command:
                hashes+=1
                if hashes==2:
                    self.device.config["system"]["cfg01"]["hostname"]="ExternalEdit"
                    self.device.files["/etc/config/system"]=json.dumps(self.device.config["system"],sort_keys=True)
            return original_read(command)
        self.device.run=run
        self.device.read=read
        self.device.lua.globals().bridge_read=read
        with self.assertRaises(SignalError) as error:
            save_settings(self.device,review,values,self.cancel)
        self.assertEqual(error.exception.code,"changed")
        self.assertEqual(self.device.config["system"]["cfg01"]["hostname"],"ExternalEdit")
        self.assertFalse(self.device.commits)

    def test_system_read_uses_installed_luci_timezone_table_when_present(self):
        self.device.lua.execute('package.preload["luci.sys.zoneinfo.tzdata"]=function() return {TZ={{"Europe/London","GMT0BST,M3.5.0/1,M10.5.0"},{"Asia/Shanghai","CST-8"}}} end')
        review=read_settings(self.device,"system",self.cancel)
        self.assertEqual(review["timezones"],[["Europe/London","GMT0BST,M3.5.0/1,M10.5.0"],["Asia/Shanghai","CST-8"]])
        self.assertFalse(self.device.commits)
