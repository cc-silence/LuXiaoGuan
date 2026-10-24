"""Reboot capability, disconnect and GUI worker completion regressions."""
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
from modules.capture_services.openwrt.router_reboot import reboot_router
from modules.capture_services.openwrt.command_runner import RemoteCommandResult


class RouterRebootTests(unittest.TestCase):
    def fixture(self, version="25.12", failure=None):
        ssh=NS(connected=True,_client=object(),host_fingerprint="pinned")
        old=ssh._client
        def invalidate(client):
            if ssh._client is client:
                ssh._client=None;ssh.connected=False
        ssh._invalidate=Mock(side_effect=invalidate)
        def run(name,command,timeout,cancel_event):
            if name.endswith("_probe"):
                return RemoteCommandResult(name,0,json.dumps({"release":{"distribution":"OpenWrt","version":version}}))
            if failure=="switch":
                ssh._client=object()
            elif failure=="timeout":
                return RemoteCommandResult(name,-1,timed_out=True)
            elif failure=="disconnect":
                raise OSError("Disconnected during reboot")
            return RemoteCommandResult(name,0)
        runner=NS(run=Mock(side_effect=run))
        return NS(ssh=ssh,runner=runner),old

    def test_all_supported_official_sources_and_capability_bound_command(self):
        contracts=json.loads((Path(__file__).parent/"fixtures/official_reboot_contracts.json").read_text(encoding="utf-8"))
        self.assertEqual({c["series"] for c in contracts},{"19.07","21.02","22.03","23.05","24.10","25.12"})
        for row in contracts:
            with self.subTest(series=row["series"]):
                self.assertIn("reboot",row["source"])
                self.assertIn(row["commit"],row["url"])
                manager,old=self.fixture(row["series"])
                result=reboot_router(manager,threading.Event())
                self.assertEqual(result["outcome"],"reconnect")
                command=manager.runner.run.call_args
                self.assertEqual(command.args[1],"sync && /sbin/reboot")
                self.assertEqual(command.kwargs["timeout"],12)
                manager.ssh._invalidate.assert_called_once_with(old)

    def test_disconnect_and_timeout_are_uncertain_and_not_retried(self):
        for reason in ("timeout","disconnect"):
            manager,old=self.fixture(failure=reason)
            result=reboot_router(manager,threading.Event())
            self.assertEqual(result["outcome"],"uncertain")
            self.assertEqual(manager.runner.run.call_count,2)
            manager.ssh._invalidate.assert_called_once_with(old)

    def test_cancellation_and_missing_capabilities_never_reboot(self):
        manager,_=self.fixture()
        event=threading.Event();event.set()
        with self.assertRaises(InterruptedError):reboot_router(manager,event)
        self.assertEqual(manager.runner.run.call_count,1)
        manager.ssh._invalidate.assert_not_called()
        manager,_=self.fixture()
        manager.runner.run.return_value=None
        manager.runner.run.side_effect=lambda name,*a,**kw:RemoteCommandResult(name,43)
        with self.assertRaises(RuntimeError):reboot_router(manager,threading.Event())
        self.assertEqual(manager.runner.run.call_count,1)
        manager.ssh._invalidate.assert_not_called()

    def test_later_connection_is_never_closed(self):
        manager,_=self.fixture(failure="switch")
        reboot_router(manager,threading.Event())
        manager.ssh._invalidate.assert_not_called()


class RebootWorkerTests(unittest.TestCase):
    def test_real_worker_disconnect_releases_task_and_gui_remains_responsive(self):
        from PySide6.QtCore import QObject,Signal,Qt
        from PySide6.QtTest import QSignalSpy
        from tests.test_standalone import APP
        from modules.capture_services.router_management_service import RouterManagementService
        from modules.capture_services.task_controller import CaptureTaskController
        from modules.capture_services.task_completion import start_capture_task
        manager,_=RouterRebootTests().fixture()
        class Owner(QObject):
            activityChanged=Signal()
            openwrtSnapshotChanged=Signal(object)
            settingsMessageGenerated=Signal(str,str)
            def __init__(self):
                super().__init__()
                self._shutting_down=False
                self._openwrt_workers={}
                self.task_controller=CaptureTaskController(self)
                self.openwrt_backend=NS(ssh_client=manager.ssh,command_runner=manager.runner)
            @property
            def is_busy(self):
                return self.task_controller.busy or bool(self._openwrt_workers)
            def _start_openwrt_worker(self,key,operation,cancel,success,failure_slot=None,progress_slot=None):
                start_capture_task(self,key,operation,cancel,success,failure_slot,progress_slot)
            def _cleanup_openwrt_worker(self,key):
                self._openwrt_workers.pop(key,None)
                self.activityChanged.emit()
        owner=Owner();service=RouterManagementService(owner)
        released=threading.Event();callbacks=[];stopped=[]
        class Probe(QObject):
            ping=Signal()
        probe=Probe()
        def gui_callback():
            callbacks.append(threading.current_thread() is threading.main_thread())
            released.set()
        probe.ping.connect(gui_callback,Qt.QueuedConnection)
        original=manager.runner.run.side_effect
        def run(name,*args,**kwargs):
            if name=="maintenance_reboot":
                probe.ping.emit()
                if not released.wait(3):raise RuntimeError("GUI did not process queued callback")
                raise OSError("Router restarted")
            return original(name,*args,**kwargs)
        manager.runner.run.side_effect=run
        completed=QSignalSpy(service.completed)
        service.completed.connect(lambda key,result:stopped.append(not owner._openwrt_workers[key][0].isRunning()))
        key=service.submit("maintenance_reboot")
        self.assertTrue(owner.task_controller.busy)
        deadline=time.monotonic()+5
        while not completed.count() and time.monotonic()<deadline:
            APP.processEvents();time.sleep(.01)
        try:
            self.assertEqual(completed.count(),1)
            self.assertEqual(callbacks,[True])
            self.assertEqual(stopped,[True])
            self.assertFalse(owner.task_controller.busy)
            self.assertIsNone(service.current)
            self.assertEqual(completed.at(0)[0],key)
            self.assertEqual(completed.at(0)[1]["outcome"],"uncertain")
        finally:
            released.set()
            self.assertFalse(owner._openwrt_workers)
            owner.deleteLater()
