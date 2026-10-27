"""LAN native apply entry point and GUI worker integration."""
import copy
import threading
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock,patch
from tests.test_checked_apply import ApplyDevice,TestChannel,LuaRuntime
from modules.capture_services.openwrt.checked_apply import apply_common
from modules.capture_services.openwrt.common_settings import read_settings

@unittest.skipUnless(LuaRuntime,"Lua runtime unavailable")
class LanApplyTests(unittest.TestCase):
    def setUp(self):
        self.device=ApplyDevice();self.channel=TestChannel(self.device)
        self.cancel=threading.Event()
        self.review=read_settings(self.device,"network",self.cancel)
        self.values=dict(self.review["values"],auto="1",ipaddr="192.168.22.1",gateway="",broadcast="")
        self.ssh=NS(_host="192.168.8.1",_port=22,_user="root",_client=object(),connected=True,host_fingerprint="PIN")
        self.manager=NS(ssh=self.ssh,runner=self.device)
    def apply(self):
        with patch("modules.capture_services.openwrt.checked_apply.ApplyChannel",return_value=self.channel) as factory:
            result=apply_common(self.manager,self.review,self.values,self.cancel,Mock(),{},"password","PIN")
        self.assertEqual(factory.call_args.args[0],dict(host="192.168.8.1",port=22,user="root"))
        return result
    def test_native_save_verify_confirm_reports_new_address_without_reboot(self):
        result=self.apply()
        self.assertTrue(result["saved"]);self.assertTrue(result["applied"])
        self.assertIn("192.168.22.1",result["note"])
        self.assertEqual(self.channel.calls.count("checked_apply"),1)
        self.assertFalse(any("reboot" in command for _,command in self.device.commands))
    def test_cancel_before_apply_preserves_configuration(self):
        before=copy.deepcopy(self.device.config);self.cancel.set()
        with self.assertRaises(InterruptedError):self.apply()
        self.assertEqual(before,self.device.config)
    def test_stale_review_is_rejected_without_apply(self):
        import json
        self.device.config["network"]["lan"]["ipaddr"]="192.168.55.1"
        self.device.files["/etc/config/network"]=json.dumps(self.device.config["network"],sort_keys=True)
        with self.assertRaises(RuntimeError):self.apply()
        self.assertNotIn("checked_apply",self.channel.calls)
    def test_failed_new_runtime_is_not_reported_restored_until_old_runtime_verified(self):
        from tests.test_checked_apply import fast_deadlines
        before=copy.deepcopy(self.device.config);call=self.channel.call
        def verify(action,**values):
            if action=="common_applied" and values.get("values",{}).get("ipaddr")=="192.168.22.1":return dict(ready=False)
            return call(action,**values)
        self.channel.call=verify
        with fast_deadlines(),self.assertRaises(RuntimeError) as error:self.apply()
        self.assertEqual(self.device.config,before)
        self.assertNotEqual(getattr(error.exception,"code",None),"configuration_apply_uncertain")
        self.assertIn("checked_rollback",self.channel.calls)
    def test_combined_operation_runs_off_gui_thread_and_releases_slot(self):
        from PySide6.QtCore import QObject,Signal,Qt
        from PySide6.QtTest import QSignalSpy
        from tests.test_router_policy_worker import Owner,wait_for
        from modules.capture_services.router_management_service import RouterManagementService
        class Ping(QObject):signal=Signal()
        probe=Ping();gate=threading.Event();callbacks=[]
        def ping():callbacks.append(threading.current_thread() is threading.main_thread());gate.set()
        probe.signal.connect(ping,Qt.QueuedConnection)
        original=self.channel.call
        def call(action,**values):
            self.assertIsNot(threading.current_thread(),threading.main_thread())
            if action=="checked_apply":
                probe.signal.emit()
                if not gate.wait(3):raise RuntimeError("GUI stalled")
            return original(action,**values)
        self.channel.call=call
        owner=Owner(self.ssh,self.device);service=RouterManagementService(owner)
        done=QSignalSpy(service.completed);failed=QSignalSpy(service.failed)
        try:
            with patch("modules.capture_services.openwrt.checked_apply.ApplyChannel",return_value=self.channel):
                key=service.submit("common_save",review=self.review,values=self.values,apply=True)
                wait_for(done,1)
            self.assertEqual(done.count(),1,str([failed.at(i) for i in range(failed.count())]))
            self.assertEqual(failed.count(),0);self.assertEqual(done.at(0)[0],key)
            self.assertEqual(callbacks,[True]);self.assertFalse(owner.task_controller.busy)
            self.assertFalse(owner._openwrt_workers);self.assertIsNone(service.current)
        finally:gate.set();owner.deleteLater()
