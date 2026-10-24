"""Real Qt workers verify automatic status and chained management operations."""
import threading
import time
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock,patch
from PySide6.QtCore import QObject,Signal,Qt
from PySide6.QtTest import QSignalSpy
from tests.test_standalone import APP
from modules.capture_services.router_management_service import RouterManagementService
from modules.capture_services.task_controller import CaptureTaskController
from modules.capture_services.task_completion import start_capture_task
from modules.capture_services.openwrt.command_runner import RemoteCommandResult


class Owner(QObject):
    activityChanged=Signal()
    openwrtSnapshotChanged=Signal(object)
    settingsMessageGenerated=Signal(str,str)
    def __init__(self,ssh,runner):
        super().__init__();self._shutting_down=False;self._openwrt_workers={}
        self.task_controller=CaptureTaskController(self)
        self.openwrt_backend=NS(ssh_client=ssh,command_runner=runner)
    @property
    def is_busy(self):return self.task_controller.busy or bool(self._openwrt_workers)
    def _start_openwrt_worker(self,key,operation,cancel,success,failure_slot=None,progress_slot=None):
        start_capture_task(self,key,operation,cancel,success,failure_slot,progress_slot)
    def _cleanup_openwrt_worker(self,key):
        self._openwrt_workers.pop(key,None);self.activityChanged.emit()


def wait_for(spy,count):
    deadline=time.monotonic()+5
    while spy.count()<count and time.monotonic()<deadline:
        APP.processEvents();time.sleep(.01)


class PolicyWorkerTests(unittest.TestCase):
    def fixture(self):
        ssh=NS(connected=True,_client=object(),host_fingerprint="pinned")
        runner=Mock();owner=Owner(ssh,runner)
        return owner,RouterManagementService(owner),ssh,runner

    def test_login_probe_is_worker_only_once_per_connection_and_gui_remains_live(self):
        owner,service,ssh,runner=self.fixture()
        gate=threading.Event();threads=[]
        class Probe(QObject):ping=Signal()
        probe=Probe()
        def gui_ping():
            threads.append(threading.current_thread() is threading.main_thread());gate.set()
        probe.ping.connect(gui_ping,Qt.QueuedConnection)
        def run(name,*a,**kw):
            self.assertEqual(name,"router_policy_status")
            self.assertIsNot(threading.current_thread(),threading.main_thread())
            probe.ping.emit()
            if not gate.wait(3):raise RuntimeError("GUI blocked")
            return RemoteCommandResult(name,0,"on")
        runner.run.side_effect=run
        done=QSignalSpy(service.completed)
        owner.openwrtSnapshotChanged.emit(None);owner.openwrtSnapshotChanged.emit(None)
        wait_for(done,1)
        try:
            self.assertEqual(done.count(),1);self.assertEqual(threads,[True])
            self.assertEqual(runner.run.call_count,1)
            self.assertEqual(service.policy_status,dict(boot_state=True))
            owner.openwrtSnapshotChanged.emit(None);APP.processEvents()
            self.assertEqual(runner.run.call_count,1)
            ssh.connected=False;owner.openwrtSnapshotChanged.emit(None)
            self.assertIsNone(service.policy_status)
            self.assertFalse(owner._openwrt_workers);self.assertIsNone(service.current)
            self.assertFalse(owner.task_controller.busy)
        finally:gate.set();owner.deleteLater()

    def test_read_preview_apply_chains_after_finished_worker_without_false_busy(self):
        owner,service,ssh,runner=self.fixture()
        done=QSignalSpy(service.completed);phases=[];errors=[]
        def completed(key,result):
            phases.append(result["phase"])
            try:
                if result["phase"]=="read":service.submit("policy_preview",kind="ap",options={})
                elif result["phase"]=="preview":service.submit("policy_apply",review={},backup_path="C:/before.tar.gz")
            except Exception as exc:errors.append(str(exc))
        service.completed.connect(completed)
        with patch("modules.capture_services.openwrt.router_policy.RouterPolicyManager") as manager:
            manager.return_value.read.return_value=dict(phase="read",boot_state=True)
            manager.return_value.preview.return_value=dict(phase="preview")
            manager.return_value.apply.return_value=dict(phase="apply")
            service.submit("policy_read");wait_for(done,3)
        try:
            self.assertFalse(errors,errors);self.assertEqual(phases,["read","preview","apply"])
            self.assertEqual(done.count(),3);self.assertFalse(owner._openwrt_workers)
            self.assertFalse(owner.task_controller.busy);self.assertIsNone(service.current)
        finally:owner.deleteLater()
