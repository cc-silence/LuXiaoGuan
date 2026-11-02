"""Native runtime samples replace transient reload notes without config refresh."""
from dataclasses import replace
import json
import unittest
from tests.test_wireless_setup import LuaRuntime
from tests.test_feedback_regressions import APP, Owner
from modules.capture_services.openwrt.wireless_status_commands import STATUS_LUA
from modules.capture_services.openwrt.wireless_status_models import parse_wireless_status, measured_status
from modules.capture_services.openwrt.wireless_runtime_observations import merge_runtime
from modules.capture_services.openwrt.signal_models import RouterSignal, SignalInventory, SignalSnapshot
from modules.capture_services.router_signal_service import RouterSignalService


class RuntimeReloadCompletionTests(unittest.TestCase):
    def inventory(self):
        return SignalInventory((RouterSignal("home","Home","ap","radio1","5g",False,False,"unknown",runtime_note="old pending"),),"a"*64)

    def sample(self,state="active",note=""):
        return parse_wireless_status(dict(schema=1,runtime=[dict(section="home",radio="radio1",mode="ap",running=state,note=note)]))

    def test_fresh_runtime_clears_old_reload_note_but_keeps_configuration_and_revision(self):
        before=self.inventory()
        after=merge_runtime(before,self.sample().runtime)
        self.assertEqual((after.rows[0].running,after.rows[0].runtime_note),("active",""))
        self.assertEqual(after.revision,before.revision)
        self.assertEqual(after.rows[0].disabled,before.rows[0].disabled)
        self.assertEqual(merge_runtime(after,self.sample("inactive").runtime).rows[0].running,"inactive")

    def test_missing_observation_does_not_invent_success_and_wrong_identity_does_not_apply(self):
        before=self.inventory()
        self.assertIs(merge_runtime(before,()),before)
        current=replace(self.sample().runtime[0],radio="other_radio")
        self.assertIs(merge_runtime(before,(current,)),before)

    def test_measurement_retains_latest_runtime_observation(self):
        after=self.sample()
        result=measured_status(self.sample("unknown","pending"),after,2,"",{})
        self.assertEqual(result.runtime,after.runtime)

    def test_service_publishes_runtime_change_without_starting_another_refresh(self):
        owner=Owner();self.addCleanup(owner.deleteLater)
        service=RouterSignalService(owner)
        service.snapshot=SignalSnapshot(self.inventory(),connected=True,stale=False)
        service._verified=True
        service.refresh_status(False)
        owner.workers.pop()[2](self.sample())
        APP.processEvents()
        self.assertEqual(service.snapshot.inventory.rows[0].running,"active")
        self.assertEqual(service.snapshot.inventory.rows[0].runtime_note,"")
        self.assertFalse(service.snapshot.busy)
        self.assertEqual(owner.workers,[])

    def test_invalid_or_duplicate_observation_is_rejected(self):
        row=dict(section="home",radio="radio1",mode="ap",running="active")
        for rows in ([row,row],[dict(row,running="invented")],[dict(row,note="private text")]):
            with self.assertRaises(ValueError):parse_wireless_status(dict(schema=1,runtime=rows))

    @unittest.skipUnless(LuaRuntime,"Lua unavailable")
    def test_actual_lua_reports_pending_running_and_down(self):
        for pending,up,wanted,note in ((True,True,"unknown","pending"),(False,True,"active",""),(False,False,"inactive","")):
            with self.subTest(pending=pending,up=up):
                lua=LuaRuntime(unpack_returned_tuples=True)
                def wrap(v):
                    if isinstance(v,dict):return lua.table_from({k:wrap(x) for k,x in v.items()})
                    if isinstance(v,list):return lua.table_from([wrap(x) for x in v])
                    return v
                def unwrap(v):
                    if not hasattr(v,"items"):return v
                    rows=dict(v.items())
                    if rows and all(type(k) is int for k in rows):return [unwrap(rows[i]) for i in range(1,len(rows)+1)]
                    return {k:unwrap(x) for k,x in rows.items()}
                def call(obj,method,args):
                    if obj=="network.wireless":return wrap(dict(radio1=dict(up=up,pending=pending,interfaces=[dict(section="home",ifname="phy1-ap0")])))
                    if obj=="network.device":return wrap(dict(up=up))
                    if obj.startswith("hostapd.") and method=="get_status":return wrap(dict(status="ENABLED",ssid="Home"))
                    if obj=="iwinfo" and method=="info":return wrap(dict(ssid="Home",bssid="02:11:22:33:44:55"))
                    if obj=="iwinfo":return wrap(dict(results=[]))
                    return wrap({})
                output=[]
                lua.globals().py_call=call;lua.globals().py_json=lambda value:json.dumps(unwrap(value));lua.globals().py_write=output.append
                lua.execute("""
                package.preload['luci.jsonc']=function() return {stringify=py_json} end
                package.preload['ubus']=function() return {connect=function() return {call=function(self,o,m,a) return py_call(o,m,a) end,close=function() end} end} end
                package.preload['uci']=function() return {cursor=function() return {foreach=function(self,f,k,cb) cb({['.name']='home',mode='ap',device='radio1',ssid='Home'}) end} end} end
                io.write=py_write;io.open=function() return nil end
                """)
                lua.execute(STATUS_LUA)
                status=parse_wireless_status(json.loads("".join(output))["result"])
                self.assertEqual((status.runtime[0].running,status.runtime[0].note),(wanted,note))
