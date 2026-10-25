"""Regression coverage for transparent tracing of actual cleanup managers."""
import json
import threading
import unittest
from unittest.mock import Mock
from tests.test_relocation import MovingDevice
from tests.test_orphan_interfaces import OrphanDevice, ORPHAN, LuaRuntime
from modules.capture_services.openwrt.wireless_setup_manager import WirelessSetupManager
from modules.capture_services.openwrt.relocation_manager import RelocationManager
from modules.capture_services.openwrt.orphan_interface_manager import OrphanInterfaceManager
from modules.capture_services.management_diagnostics import ManagementTrace
from modules.capture_services.openwrt.operation_diagnostics import OperationDiagnostics, sanitized_operation_evidence
from modules.display_privacy import masker_for


class CallbackContractTests(unittest.TestCase):
    def test_callback_arguments_return_and_exceptions_are_preserved(self):
        device=Mock(operation_diagnostics=OperationDiagnostics())
        trace=ManagementTrace(device,"relocate",{})
        callback=Mock(return_value=object())
        def operation(event, report):
            value=report("private-message", "warning")
            self.assertIs(value, callback.return_value)
            report(message="private-message",level="info")
            report(dict(stage="private-stage",completed=1,total=2))
            return {}
        trace.execute(operation,threading.Event(),callback)
        self.assertEqual(callback.call_count,3)
        self.assertEqual(callback.call_args_list[0].args,("private-message","warning"))
        self.assertEqual(callback.call_args_list[1].kwargs,dict(message="private-message",level="info"))
        self.assertNotIn("private",json.dumps(device.operation_diagnostics.snapshot()))
        failure=RuntimeError("private-failure")
        with self.assertRaises(RuntimeError) as raised:
            ManagementTrace(device,"relocate",{}).execute(lambda e,r:r("private","info"),threading.Event(),Mock(side_effect=failure))
        self.assertIs(raised.exception,failure)


@unittest.skipUnless(LuaRuntime,"Lua runtime unavailable")
class TracedCleanupTests(unittest.TestCase):
    def test_real_relocation_applies_and_records_safe_result_counts(self):
        device=MovingDevice();device.operation_diagnostics=OperationDiagnostics()
        manager=RelocationManager(WirelessSetupManager(device));event=threading.Event()
        review=manager.preview(event);report=Mock()
        result=ManagementTrace(device,"relocate",{}).execute(lambda e,r:manager.apply(review,e,r),event,report)
        self.assertGreater(report.call_count,0)
        self.assertEqual(len(report.call_args_list[0].args),2)
        self.assertFalse(device.sessions)
        data=sanitized_operation_evidence(device.operation_diagnostics.snapshot(),masker_for([]))
        row=data["management"][-1]
        self.assertEqual(row["status"],"completed")
        self.assertEqual(row["removed_count"],result["removed"])
        self.assertEqual(row["mac_count"],result["macs"])
        self.assertNotIn("hostname",row)
        self.assertNotIn("original-password",json.dumps(data))

    def test_real_orphan_cleanup_saves_without_apply_and_records_counts(self):
        device=OrphanDevice();device.operation_diagnostics=OperationDiagnostics()
        manager=OrphanInterfaceManager(WirelessSetupManager(device));event=threading.Event()
        review=manager.preview(event);report=Mock()
        result=ManagementTrace(device,"orphan_cleanup",{}).execute(lambda e,r:manager.cleanup(review,[ORPHAN],e,r),event,report)
        self.assertNotIn(ORPHAN,device.config["network"])
        self.assertFalse(device.sessions)
        self.assertEqual(len(report.call_args_list[0].args),2)
        data=sanitized_operation_evidence(device.operation_diagnostics.snapshot(),masker_for([]))
        row=data["management"][-1]
        self.assertEqual(row["status"],"completed")
        self.assertEqual(row["removed_count"],1)
        self.assertEqual(row["applied"],False)
        self.assertNotIn(ORPHAN,json.dumps(data))


class CleanupServiceWorkerTests(unittest.TestCase):
    def fixture(self):
        from types import SimpleNamespace as NS
        from PySide6.QtCore import QObject, Signal
        from tests.test_router_policy_worker import Owner
        from modules.capture_services.task_completion import start_capture_task
        from modules.capture_services.wireless_setup_service import WirelessSetupService
        class NativeOwner(Owner):
            def _start_openwrt_worker(self,key,operation,cancel,success,failure=None,progress=None,message=None):
                start_capture_task(self,key,operation,cancel,success,failure,progress,message)
        class Signals(QObject):
            snapshotChanged=Signal(object)
            def __init__(self,owner):
                super().__init__(owner);self.owner=owner;self.snapshot=NS(connected=True)
                self._message=Mock();self.refresh=Mock();self._auto_refresh_blocked=False
            def _forward_worker_message(self,message,level):self._message(message,level)
        device=OrphanDevice();device.operation_diagnostics=OperationDiagnostics()
        device.ssh_client.connected=True;device.ssh_client._client=object()
        owner=NativeOwner(device.ssh_client,device);signals=Signals(owner)
        return device,owner,signals,WirelessSetupService(signals)

    @unittest.skipUnless(LuaRuntime,"Lua runtime unavailable")
    def test_real_cleanup_worker_forwards_messages_and_releases_task(self):
        from PySide6.QtTest import QSignalSpy
        from tests.test_router_policy_worker import wait_for
        device,owner,signals,service=self.fixture()
        review=OrphanInterfaceManager(service.manager).preview(threading.Event())
        done=QSignalSpy(service.completed)
        service.completed.connect(lambda result:setattr(service,"_pending",False))
        self.assertTrue(service.cleanup_orphans(review,[ORPHAN]));wait_for(done,1)
        self.assertEqual(done.count(),1)
        self.assertFalse(done.at(0)[0].error)
        self.assertFalse(service.active);self.assertFalse(owner.task_controller.busy)
        self.assertFalse(owner._openwrt_workers)
        self.assertGreaterEqual(signals._message.call_count,2)
        self.assertEqual(device.operation_diagnostics.snapshot()["management"][-1]["status"],"completed")
        owner.deleteLater()

    def test_worker_start_failure_has_terminal_management_record(self):
        from unittest.mock import patch
        device,owner,signals,service=self.fixture()
        with patch.object(owner,"_start_openwrt_worker",side_effect=RuntimeError("private-start-error")):
            self.assertFalse(service.orphan_preview())
        row=device.operation_diagnostics.snapshot()["management_failures"][-1]
        self.assertEqual(row["operation"],"orphan_preview")
        self.assertEqual(row["status"],"failed")
        self.assertFalse(service.active);self.assertFalse(owner.task_controller.busy)
        self.assertNotIn("private-start-error",json.dumps(device.operation_diagnostics.snapshot()))
        owner.deleteLater()

    def test_unconfirmed_native_code_is_preserved(self):
        from modules.capture_services.openwrt.signal_manager import SignalError
        device=Mock(operation_diagnostics=OperationDiagnostics())
        def operation(event,report):raise SignalError("unconfirmed","private-result")
        with self.assertRaises(SignalError):
            ManagementTrace(device,"relocate",{}).execute(operation,threading.Event(),Mock())
        self.assertEqual(device.operation_diagnostics.snapshot()["management_failures"][-1]["reason"],"unconfirmed")


if __name__=="__main__":unittest.main()
