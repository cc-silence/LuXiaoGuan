"""Failed wireless writes re-read once; failed reads never form a retry loop."""
import unittest
from unittest.mock import Mock
from tests.test_feedback_regressions import APP, Owner
from modules.capture_services.router_signal_service import RouterSignalService
from modules.capture_services.openwrt.signal_models import RouterSignal, SignalInventory, SignalSnapshot
from modules.capture_services.openwrt.signal_manager import SignalError


class FailedWriteReadbackTests(unittest.TestCase):
    def create(self):
        owner=Owner();self.addCleanup(owner.deleteLater)
        service=RouterSignalService(owner)
        row=RouterSignal("home","Home","ap","radio7","5g",True,False,"inactive")
        service.snapshot=SignalSnapshot(SignalInventory(rows=(row,),revision="a"*64),connected=True,stale=False)
        service._verified=True
        service.manager.change=Mock(side_effect=SignalError("authentication","Startup not confirmed"))
        service.manager.read=Mock(return_value=service.snapshot.inventory)
        return owner,service

    def finish_next(self,owner):
        operation,event,success,_=owner.workers.pop(0)
        success(operation(event))

    def test_failed_start_rechecks_and_unlocks_without_manual_refresh(self):
        owner,service=self.create()
        self.assertTrue(service.change("home",True,confirmed=True))
        self.finish_next(owner)
        self.assertTrue(service.snapshot.stale)
        self.assertFalse(owner.task_controller.busy)
        APP.processEvents()
        self.assertEqual(len(owner.workers),1)
        self.finish_next(owner)
        self.assertFalse(service.snapshot.stale)
        self.assertFalse(service.snapshot.busy)
        self.assertTrue(any("Startup not confirmed" in text for text,_ in service.messages))
        service.manager.change.assert_called_once()
        service.manager.read.assert_called_once()

    def test_failed_readback_stays_locked_and_does_not_loop(self):
        owner,service=self.create()
        service.manager.read.side_effect=SignalError("timeout","Read failed")
        service.change("home",True,confirmed=True);self.finish_next(owner)
        APP.processEvents();self.finish_next(owner)
        for _ in range(10):APP.processEvents()
        self.assertEqual(owner.workers,[])
        self.assertTrue(service.snapshot.stale)
        self.assertFalse(service.snapshot.busy)
        service.manager.read.assert_called_once()

    def test_initial_read_failure_does_not_retry(self):
        owner,service=self.create()
        service.manager.read.side_effect=SignalError("timeout","Read failed")
        service.refresh();self.finish_next(owner)
        for _ in range(5):APP.processEvents()
        self.assertEqual(owner.workers,[])
        service.manager.read.assert_called_once()

    def test_uncertain_recovery_disconnect_and_cancellation_remain_protected(self):
        for code in ("recovery","disconnected","transport","cancelled"):
            with self.subTest(code=code):
                owner,service=self.create()
                service.manager.change.side_effect=SignalError(code,"Cannot confirm")
                service.change("home",True,confirmed=True);self.finish_next(owner)
                for _ in range(5):APP.processEvents()
                self.assertEqual(owner.workers,[])
                self.assertTrue(service.snapshot.stale)
                service.manager.read.assert_not_called()
