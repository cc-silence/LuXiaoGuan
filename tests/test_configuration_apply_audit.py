"""Second-pass checks independent of the happy-path apply fixtures."""
import copy
import threading
import unittest
from unittest.mock import Mock
from tests.test_checked_apply import ApplyDevice,TestChannel,LuaRuntime
from tests.test_wireless_setup import LuaDevice
from modules.capture_services.openwrt.configuration_runtime import network_ready
from modules.capture_services.openwrt.common_settings import read_settings
from modules.capture_services.openwrt.wireless_setup_manager import WirelessSetupManager

class InterfaceRuntimeTests(unittest.TestCase):
    def fixture(self):
        values=dict(proto="static",auto="1",device="br-lan",ipaddr="192.168.22.1",netmask="255.255.255.0",
                    gateway="192.168.22.254",ip6addr=["fd00::1/64"],ip6gw="fd00::ff",ip6prefix=["2001:db8::/48"])
        state=dict(up=True,pending=False,proto="static",device="br-lan",**{
            "ipv4-address":[dict(address="192.168.22.1",mask=24)],
            "ipv6-address":[dict(address="fd00::1",mask=64)],
            "ipv6-prefix":[dict(address="2001:db8::",mask=48)],
            "route":[dict(target="0.0.0.0",mask=0,nexthop="192.168.22.254"),dict(target="::",mask=0,nexthop="fd00::ff")]})
        return values,state
    def test_full_runtime_and_ipv6_normalization(self):
        values,state=self.fixture();state["ipv6-address"][0]["address"]="fd00:0000:0000:0000::1"
        self.assertTrue(network_ready(state,values))
    def test_stale_runtime_fields_do_not_end_waiting(self):
        values,state=self.fixture()
        bad=[dict(proto="dhcp"),dict(device="br-other"),dict(pending=True),{"ipv4-address":[dict(address="192.168.22.1",mask=16)]},
             {"ipv6-address":[]},{"ipv6-prefix":[]},{"route":[]}]
        for change in bad:
            with self.subTest(change=change):self.assertFalse(network_ready(dict(state,**change),values))
    def test_dhcp_ignores_disabled_static_gateway_field(self):
        values=dict(proto="dhcp",auto="1",gateway="192.168.8.254")
        self.assertTrue(network_ready(dict(up=True,pending=False,proto="dhcp"),values))
    def test_none_protocol_is_not_the_same_as_a_stopped_bridge(self):
        values=dict(proto="none",auto="1")
        self.assertTrue(network_ready(dict(up=True,pending=False,proto="none",**{"ipv4-address":[]}),values))
        self.assertFalse(network_ready(dict(up=False,pending=False,proto="static"),values))
        self.assertFalse(network_ready(dict(up=True,pending=False,proto="none",**{"ipv4-address":[dict(address="192.168.8.1",mask=24)]}),values))

    def test_ipv6_prefix_lengths_rejected_by_netifd_are_rejected_before_write(self):
        from modules.capture_services.openwrt.common_settings import validate_values
        for prefix in ("::/0","2001:db8::/80","2001:db8::/128"):
            with self.subTest(prefix=prefix),self.assertRaises(ValueError):
                validate_values(dict(config="network"),dict(proto="static",auto="1",ipaddr="192.168.22.1/24",ip6prefix=[prefix]))

    def test_old_removed_ipv6_address_must_disappear(self):
        values,state=self.fixture();state["ipv6-address"].append(dict(address="fd00::2",mask=64))
        self.assertFalse(network_ready(state,values,dict(ip6addr=["fd00::2/64"])))
    def test_missing_status_is_never_evidence_of_stopped_lan(self):
        for state in (None,{},dict(pending=True),dict(up=False,pending=True)):
            self.assertFalse(network_ready(state,dict(auto="0",proto="static")))

@unittest.skipUnless(LuaRuntime,"Lua runtime unavailable")
class LoadedWirelessTests(unittest.TestCase):
    def test_same_ssid_with_old_loaded_password_is_not_ready(self):
        device=LuaDevice();manager=WirelessSetupManager(device);manager.signal._lua_bin="lua"
        old=copy.deepcopy(device.config["wireless"]["home"])
        device.config["wireless"]["home"]["key"]="new-password"
        original=device.call
        def call(obj,method,args):
            value,error=original(obj,method,args)
            if obj=="network.wireless" and method=="status":
                value=device.unwrap(value);value["radio7"]["interfaces"][0]["config"]=old;value=device.wrap(value)
            return value,error
        device.lua.globals().bridge_call=call
        values=dict(radio="radio7",section="home",mode="ap")
        self.assertFalse(manager._call("status",threading.Event(),**values)["ready"])
        device.lua.globals().bridge_call=original
        self.assertTrue(manager._call("status",threading.Event(),**values)["ready"])
    def test_old_loaded_radio_channel_is_not_ready(self):
        device=LuaDevice();manager=WirelessSetupManager(device);manager.signal._lua_bin="lua"
        device.config["wireless"]["radio7"]["channel"]="44";original=device.call
        def call(obj,method,args):
            value,error=original(obj,method,args)
            if obj=="network.wireless" and method=="status":
                value=device.unwrap(value);value["radio7"]["config"]["channel"]="36";value=device.wrap(value)
            return value,error
        device.lua.globals().bridge_call=call
        self.assertFalse(manager._call("status",threading.Event(),radio="radio7",section="home",mode="ap")["ready"])
    def test_failed_status_rpc_cannot_confirm_disabled_lan(self):
        device=ApplyDevice();channel=TestChannel(device);event=threading.Event()
        review=read_settings(device,"network",event);values=dict(review["values"],auto="0")
        sid=channel.call("session",relocation=True)["sid"]
        channel.call("common_stage",sid=sid,config="network",review=review,values=values)
        channel.call("checked_apply",sid=sid,hashes={"network":review["token"]})
        original=device.call
        def call(obj,method,args):
            return (None,4) if obj=="network.interface.lan" else original(obj,method,args)
        device.lua.globals().bridge_call=call
        self.assertFalse(channel.call("common_applied",config="network",review=review,values=values)["ready"])

    def test_offline_station_identity_change_does_not_force_an_unrequested_connection(self):
        from tests.test_router_policy import PolicyDevice
        from tests.test_checked_apply import fast_deadlines
        device=PolicyDevice();device.complete=False
        plan=device.policy.preview("station",{},device.cancel)
        with fast_deadlines():result=device.policy.apply(plan,None,None,device.cancel,Mock())
        self.assertEqual(result["outcome"],"applied")
        self.assertFalse(device.complete)

    def test_station_identity_without_wpa_cli_verifies_loaded_identity_without_forcing_association(self):
        from tests.test_router_policy import PolicyDevice
        from tests.test_checked_apply import fast_deadlines
        device=PolicyDevice();read=device.read
        device.lua.globals().bridge_read=lambda command:"" if command.startswith("wpa_cli") else read(command)
        plan=device.policy.preview("station",{},device.cancel)
        with fast_deadlines():result=device.policy.apply(plan,None,None,device.cancel,Mock())
        self.assertEqual(result["outcome"],"applied")
