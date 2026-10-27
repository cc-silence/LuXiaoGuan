"""Execute real Lua apply/confirm/rollback, including a lost apply reply."""
import copy
from contextlib import contextmanager
import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
from tests.test_common_settings import CommonDevice,LuaRuntime
from modules.capture_services.openwrt.checked_apply import ApplyChannel,run_checked,address_candidates
from modules.capture_services.openwrt.common_settings import read_settings
from modules.capture_services.openwrt.wireless_setup_manager import WirelessSetupManager
from modules.capture_services.openwrt.signal_manager import SignalError


class ApplyDevice(CommonDevice):
    def read(self,command):
        if command.startswith("ubus -v list uci"):return '"apply" "confirm" "rollback"'
        return super().read(command)

    def call(self,obj,method,args):
        if obj=="network" and method=="reload":
            self.calls.append((obj,method))
            return (None,2) if self.fail==(obj,method) else (self.wrap({}),None)
        if obj=="network.interface.lan":
            row=self.config["network"]["lan"]
            addresses=row.get("ipaddr",[])
            if isinstance(addresses,str):addresses=[addresses]
            return self.wrap(dict(up=True,pending=False,proto=row.get("proto"),device=row.get("device"),**{"ipv4-address":[dict(address=v.split('/')[0],mask=24) for v in addresses]})),None
        result=super().call(obj,method,args)
        if obj=="uci" and method in {"apply","rollback"} and result[1] is None:
            self.files.update({"/etc/config/"+k:json.dumps(v,sort_keys=True) for k,v in self.config.items()})
        return result


class TestChannel:
    def __init__(self,device):
        self.device=device;self.manager=WirelessSetupManager(device);self.manager.signal._lua_bin="lua"
        self.ssh=Mock();self.hosts=[];self.calls=[];self.lost_reply=False;self.bad_runtime=False
    def connect(self,host,timeout):self.hosts.append(host)
    def call(self,action,timeout=8,**values):
        self.calls.append(action)
        result=self.manager._call(action,threading.Event(),timeout=timeout,**values)
        if action=="checked_apply" and self.lost_reply:
            self.lost_reply=False
            raise SignalError("transport","lost reply")
        if action=="common_applied" and self.bad_runtime:return dict(ready=False)
        return result
    def close(self):pass


@unittest.skipUnless(LuaRuntime,"Lua runtime unavailable")
class CheckedApplyTests(unittest.TestCase):
    def fixture(self):
        device=ApplyDevice();channel=TestChannel(device);event=threading.Event()
        review=read_settings(device,"network",event)
        values=dict(review["values"],auto="1",ipaddr="192.168.22.1")
        def stage(remote,sid):
            remote.call("common_stage",sid=sid,config="network",review=review,values=values)
            return {"network":review["token"]}
        def verify(remote,sid):
            return remote.call("common_applied",sid=sid,config="network",review=review,values=values)["ready"]
        return device,channel,event,stage,verify

    def test_native_apply_is_verified_and_confirmed_without_reboot(self):
        device,channel,event,stage,verify=self.fixture()
        result=run_checked(channel,stage,verify,event,candidates=("192.168.22.1",),original_host="192.168.8.1")
        self.assertTrue(result["applied"])
        self.assertEqual(result["target_host"],"192.168.22.1")
        self.assertEqual(channel.calls.count("checked_apply"),1)
        self.assertIn(("uci","confirm"),device.calls)
        self.assertFalse(device.sessions)
        self.assertEqual(device.config["network"]["lan"]["ipaddr"],"192.168.22.1")
        self.assertFalse(any("reboot" in command for _,command in device.commands))

    def test_saved_mask_is_not_confirmed_while_runtime_mask_is_old(self):
        device,channel,event,stage,verify=self.fixture()
        review=read_settings(device,"network",event)
        values=dict(review["values"],auto="1",ipaddr="192.168.22.1",netmask="255.255.0.0")
        sid=channel.call("session",relocation=True)["sid"]
        channel.call("common_stage",sid=sid,config="network",review=review,values=values)
        channel.call("checked_apply",sid=sid,hashes={"network":review["token"]})
        result=channel.call("common_applied",config="network",review=review,values=values)
        self.assertIs(result["ready"],False)
        self.assertNotIn(("uci","confirm"),device.calls)
    def test_lost_apply_reply_does_not_resubmit(self):
        device,channel,event,stage,verify=self.fixture();channel.lost_reply=True
        result=run_checked(channel,stage,verify,event,candidates=("192.168.22.1",),original_host="192.168.8.1")
        self.assertTrue(result["applied"])
        self.assertEqual(channel.calls.count("checked_apply"),1)

    def test_lost_confirmation_reply_rechecks_retained_configuration_without_reapplying(self):
        device,channel,event,stage,verify=self.fixture();call=channel.call;lost=[False]
        def action(name,**values):
            value=call(name,**values)
            if name=="checked_confirm" and not lost[0]:
                lost[0]=True;raise OSError("lost confirmation reply")
            return value
        channel.call=action
        with fast_deadlines():result=run_checked(channel,stage,verify,event,candidates=("new",),original_host="old")
        self.assertTrue(result["applied"]);self.assertEqual(channel.calls.count("checked_apply"),1)
        self.assertEqual(channel.calls.count("checked_confirm"),2);self.assertNotIn("checked_rollback",channel.calls)

    def test_no_changes_response_is_verified_and_is_not_an_apply_failure(self):
        device,channel,event,stage,verify=self.fixture();native=device.call
        def call(obj,method,args):
            if obj=="uci" and method=="apply":return None,5
            return native(obj,method,args)
        device.lua.globals().bridge_call=call
        with fast_deadlines():result=run_checked(channel,stage,lambda *_:True,event,candidates=("new",),original_host="old")
        self.assertTrue(result["applied"]);self.assertNotIn("checked_confirm",channel.calls)
        self.assertNotIn("checked_rollback",channel.calls);self.assertFalse(device.sessions)

    def test_expired_verification_does_not_send_a_late_confirmation(self):
        device,channel,event,stage,verify=self.fixture()
        with fast_deadlines() as clock:
            def slow(*_):clock.value=100;return True
            with self.assertRaises(RuntimeError):run_checked(channel,stage,slow,event,candidates=("new",),original_host="old")
        self.assertNotIn("checked_confirm",channel.calls);self.assertIn("checked_rollback",channel.calls)

    def test_edit_between_staging_and_apply_is_not_overwritten(self):
        device,channel,event,stage,verify=self.fixture()
        def changed(remote,sid):
            hashes=stage(remote,sid)
            device.config["network"]["lan"]["ipaddr"]="192.168.77.1"
            device.files["/etc/config/network"]=json.dumps(device.config["network"],sort_keys=True)
            return hashes
        with self.assertRaises(RuntimeError):run_checked(channel,changed,verify,event,candidates=("new",),original_host="old")
        self.assertEqual(device.config["network"]["lan"]["ipaddr"],"192.168.77.1")
        self.assertNotIn(("uci","apply"),device.calls);self.assertNotIn("checked_rollback",channel.calls)

    def test_failed_verification_rolls_back_and_checks_original_hash(self):
        device,channel,event,stage,verify=self.fixture();channel.bad_runtime=True
        original=copy.deepcopy(device.config)
        clock=SimpleNamespace(value=0)
        clock.monotonic=lambda:clock.value
        class Wait:
            def wait(self,seconds):clock.value+=max(seconds,.01)
        with patch("modules.capture_services.openwrt.checked_apply.time",clock),patch("modules.capture_services.openwrt.checked_apply.Event",Wait):
            with self.assertRaises(RuntimeError) as error:
                run_checked(channel,stage,verify,event,candidates=("192.168.22.1",),original_host="192.168.8.1")
        self.assertEqual(device.config,original)
        self.assertIn("checked_rollback",channel.calls)
        self.assertNotIn(("uci","confirm"),device.calls)
        self.assertFalse(device.sessions)

    def test_concurrent_edit_with_lost_confirm_never_overwrites_external_change(self):
        from modules.capture_services.openwrt.checked_apply import ApplyConflict,ApplyUncertain
        device,channel,event,stage,verify=self.fixture();call=channel.call
        def action(name,**values):
            if name=="checked_confirm":raise OSError("lost confirmation")
            return call(name,**values)
        channel.call=action
        def conflict(remote,sid):
            device.config["network"]["lan"]["ipaddr"]="192.168.99.1"
            raise ApplyConflict("external edit")
        with self.assertRaises(ApplyUncertain):run_checked(channel,stage,conflict,event,candidates=("new",),original_host="old")
        self.assertEqual(device.config["network"]["lan"]["ipaddr"],"192.168.99.1")
        self.assertNotIn("checked_rollback",channel.calls)
    def test_success_requires_a_fresh_connection_after_apply(self):
        device,channel,event,stage,verify=self.fixture()
        def check(remote,sid):
            self.assertGreater(channel.ssh.close.call_count,0)
            return verify(remote,sid)
        result=run_checked(channel,stage,check,event,candidates=("old",),original_host="old")
        self.assertTrue(result["applied"])

    def test_network_load_failure_after_apply_restores_original_configuration(self):
        device,channel,event,stage,verify=self.fixture();device.fail=("network","reload")
        original=copy.deepcopy(device.config)
        with fast_deadlines(),self.assertRaises(RuntimeError):
            run_checked(channel,stage,verify,event,candidates=("new",),original_host="old",reload_network=True)
        self.assertEqual(device.config,original);self.assertIn("checked_rollback",channel.calls)
        self.assertNotIn("checked_confirm",channel.calls)

    def test_cancel_before_apply_never_writes(self):
        device,channel,event,stage,verify=self.fixture();event.set()
        with self.assertRaises(InterruptedError):run_checked(channel,stage,verify,event,candidates=("new",),original_host="old")
        self.assertNotIn(("uci","apply"),device.calls)
        self.assertFalse(device.sessions)

    def test_apply_permission_error_never_rolls_back_another_session(self):
        device,channel,event,stage,verify=self.fixture();device.fail=("uci","apply")
        with self.assertRaises(SignalError):run_checked(channel,stage,verify,event,candidates=("new",),original_host="old")
        self.assertNotIn("checked_rollback",channel.calls)
        self.assertFalse(device.sessions)


class ApplyIdentityTests(unittest.TestCase):
    def test_independent_connection_pins_identity_and_keeps_secret_out_of_settings(self):
        with patch("modules.capture_services.openwrt.checked_apply.OpenWrtSshClient") as factory:
            factory.return_value.connected=False
            channel=ApplyChannel(dict(host="old",port=22,user="root"),"secret-value","PIN")
            channel.connect("192.168.22.1",4)
            self.assertEqual(factory.return_value.connect.call_args.kwargs["expected_fingerprint"],"PIN")
            self.assertNotIn("secret-value",str(channel.settings))
            channel.close();self.assertEqual(channel.password,"")
    def test_wan_management_address_is_retained_as_a_verified_reconnect_candidate(self):
        values=dict(proto="static",auto="1",ipaddr="192.168.22.1")
        review=dict(values=dict(ipaddr="192.168.8.1"))
        self.assertEqual(address_candidates(dict(host="10.2.3.1"),values,review),("192.168.22.1","10.2.3.1"))
        self.assertEqual(address_candidates(dict(host="192.168.8.1"),values,review),("192.168.22.1",))

    def test_candidates_only_use_explicit_target_addresses(self):
        self.assertEqual(address_candidates(dict(host="old"),dict(proto="static",auto="1",ipaddr=["192.168.2.1/24"])),("192.168.2.1",))


@contextmanager
def fast_deadlines():
    clock=SimpleNamespace(value=0)
    clock.monotonic=lambda:clock.value
    class Wait:
        def wait(self,seconds):clock.value+=max(seconds,.01)
    with patch("modules.capture_services.openwrt.checked_apply.time",clock),patch("modules.capture_services.openwrt.checked_apply.Event",Wait):
        yield clock
