"""LAN save/apply covers persistence, reboot failure and the actual GUI worker."""
import copy
import json
import threading
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock,patch
from tests.test_common_settings import CommonDevice
import tests.test_router_reboot as reboot_tests
from modules.capture_services.openwrt.common_settings import read_settings,save_and_reboot
from modules.capture_services.openwrt.command_runner import RemoteCommandResult


class LanApplyTests(unittest.TestCase):
    def setUp(self):
        self.device=CommonDevice()
        self.cancel=threading.Event()
        self.review=read_settings(self.device,"network",self.cancel)
        self.values=dict(self.review["values"],auto="1",ipaddr="192.168.22.1",gateway="",broadcast="")
        self.manager,self.old=reboot_tests.RouterRebootTests().fixture()
        native=self.manager.runner.run.side_effect
        def run(name,command,timeout,cancel_event=None):
            if name.startswith("maintenance_"):
                if name=="maintenance_reboot":self.assertEqual(self.device.commits,["network"])
                return native(name,command,timeout,cancel_event)
            return self.device.run(name,command,timeout,cancel_event)
        self.manager.runner.run.side_effect=run

    def reboot_calls(self):
        return [c for c in self.manager.runner.run.call_args_list if c.args[0]=="maintenance_reboot"]

    def test_verified_save_precedes_single_reboot_and_reports_new_address(self):
        progress=[]
        result=save_and_reboot(self.manager,self.review,self.values,self.cancel,progress.append)
        self.assertEqual(self.device.config["network"]["lan"]["ipaddr"],"192.168.22.1")
        self.assertEqual(len(self.reboot_calls()),1)
        self.assertTrue(result["saved"]);self.assertFalse(result["applied"])
        self.assertEqual(result["outcome"],"reconnect")
        self.assertIn("192.168.22.1",result["note"])
        self.manager.ssh._invalidate.assert_called_once_with(self.old)
        self.assertEqual(len(progress),3)
        self.assertTrue(progress[-1]["uncancellable"])

    def test_switch_after_preflight_never_saves_to_replacement_connection(self):
        replacement=object()
        def report(value):
            if value.get("uncancellable"):
                self.manager.ssh._client=replacement
        with self.assertRaises(RuntimeError):
            save_and_reboot(self.manager,self.review,self.values,self.cancel,report)
        self.assertFalse(self.device.commits)
        self.assertFalse(self.reboot_calls())
        self.assertIs(self.manager.ssh._client,replacement)

    def test_unsupported_reboot_stops_before_saving(self):
        self.manager.runner.run.side_effect=lambda *a,**k:RemoteCommandResult(a[0],43)
        with self.assertRaises(RuntimeError):save_and_reboot(self.manager,self.review,self.values,self.cancel)
        self.assertFalse(self.device.commits)
        self.assertFalse(self.reboot_calls())

    def test_save_failure_or_uncertain_commit_never_reboots(self):
        with patch("modules.capture_services.openwrt.common_settings.save_settings",side_effect=RuntimeError("commit unconfirmed")):
            with self.assertRaisesRegex(RuntimeError,"commit unconfirmed"):
                save_and_reboot(self.manager,self.review,self.values,self.cancel)
        self.assertFalse(self.reboot_calls())
        self.manager.ssh._invalidate.assert_not_called()

    def test_lost_reboot_reply_is_reported_as_saved_but_uncertain_without_retry(self):
        original=self.manager.runner.run.side_effect
        def run(name,*a,**k):
            if name=="maintenance_reboot":raise OSError("router restarted")
            return original(name,*a,**k)
        self.manager.runner.run.side_effect=run
        result=save_and_reboot(self.manager,self.review,self.values,self.cancel)
        self.assertTrue(result["saved"])
        self.assertEqual(result["outcome"],"uncertain")
        self.assertEqual(len(self.reboot_calls()),1)

    def test_cancel_or_connection_change_after_save_never_reboots_new_connection(self):
        from modules.capture_services.openwrt.common_settings import save_settings
        for switch in (False,True):
            self.setUp()
            replacement=object()
            def saved(*a,**k):
                result=save_settings(*a,**k)
                if switch:self.manager.ssh._client=replacement
                else:self.cancel.set()
                return result
            with patch("modules.capture_services.openwrt.common_settings.save_settings",side_effect=saved):
                result=save_and_reboot(self.manager,self.review,self.values,self.cancel)
            self.assertTrue(result["saved"])
            self.assertFalse(result["reboot_requested"])
            self.assertFalse(self.reboot_calls())
            self.manager.ssh._invalidate.assert_not_called()
            if switch:self.assertIs(self.manager.ssh._client,replacement)

    def test_combined_operation_runs_off_gui_thread_and_releases_slot_after_disconnect(self):
        from PySide6.QtCore import QObject,Signal,Qt
        from PySide6.QtTest import QSignalSpy
        from tests.test_standalone import APP
        from tests.test_router_policy_worker import Owner,wait_for
        from modules.capture_services.router_management_service import RouterManagementService
        class Ping(QObject):signal=Signal()
        probe=Ping()
        gate=threading.Event()
        callbacks=[]
        def on_ping():
            callbacks.append(threading.current_thread() is threading.main_thread())
            gate.set()
        probe.signal.connect(on_ping,Qt.QueuedConnection)
        original=self.manager.runner.run.side_effect
        def run(name,*a,**k):
            self.assertIsNot(threading.current_thread(),threading.main_thread())
            if name=="maintenance_reboot":
                probe.signal.emit()
                if not gate.wait(3):raise RuntimeError("GUI stalled")
            return original(name,*a,**k)
        self.manager.runner.run.side_effect=run
        owner=Owner(self.manager.ssh,self.manager.runner)
        service=RouterManagementService(owner)
        done=QSignalSpy(service.completed)
        failed=QSignalSpy(service.failed)
        try:
            key=service.submit("common_save",review=self.review,values=self.values,apply=True)
            wait_for(done,1)
            self.assertEqual(done.count(),1)
            self.assertEqual(failed.count(),0)
            self.assertEqual(done.at(0)[0],key)
            self.assertEqual(callbacks,[True])
            self.assertFalse(owner.task_controller.busy)
            self.assertFalse(owner._openwrt_workers)
            self.assertIsNone(service.current)
        finally:
            gate.set();owner.deleteLater()


if __name__=="__main__":unittest.main()
