"""Enable/disable publishes inventory and editing metadata in one busy cycle."""
from dataclasses import replace
from unittest.mock import Mock
import unittest
from tests.test_feedback_regressions import APP, Owner
from modules.capture_services.router_signal_service import RouterSignalService
from modules.capture_services.openwrt.signal_models import SignalInventory, SignalSnapshot, RouterSignal
from modules.capture_services.openwrt.wireless_setup_models import WirelessEnvironment, WirelessState
from modules.capture_services.openwrt.signal_manager import SignalError


class MutationCompletionTests(unittest.TestCase):
    def create(self):
        owner=Owner();self.addCleanup(owner.deleteLater)
        service=RouterSignalService(owner)
        row=RouterSignal("home","Home","ap","radio7","5g",True,False,"inactive")
        service.snapshot=SignalSnapshot(SignalInventory((row,),"a"*64),connected=True,stale=False)
        service._verified=True
        service.setup._enabled=True
        service.setup._inspection_revision="a"*64
        service.setup.snapshot=WirelessState(environment=WirelessEnvironment(data={"revision":"a"*192}))
        current=SignalInventory((replace(row,disabled=False,running="active"),),"b"*64)
        environment=WirelessEnvironment(data={"revision":"b"*64+"c"*128})
        service.manager.change=Mock(return_value=current)
        service.setup.manager.inspect=Mock(return_value=environment)
        service.manager.read=Mock(return_value=current)
        return owner,service,current,environment

    def test_enable_has_one_busy_cycle_and_no_second_inspection_worker(self):
        owner,service,current,environment=self.create()
        states=[];service.snapshotChanged.connect(lambda value:states.append((value.busy,value.stale)))
        service.change("home",True,confirmed=True)
        operation,event,success,_=owner.workers.pop()
        result=operation(event)
        self.assertTrue(service.snapshot.busy)
        service.setup.manager.inspect.assert_called_once_with(event)
        success(result)
        for _ in range(10):APP.processEvents()
        self.assertEqual(owner.workers,[])
        self.assertFalse(service.snapshot.busy)
        self.assertFalse(service.snapshot.stale)
        self.assertIs(service.snapshot.inventory,current)
        self.assertIs(service.setup.snapshot.environment,environment)
        first_idle=states.index((False,False))
        self.assertNotIn((True,True),states[first_idle:])
        service.manager.read.assert_not_called()

    def test_unopened_setup_does_not_add_an_environment_probe(self):
        owner,service,current,_=self.create();service.setup._enabled=False
        service.change("home",True,confirmed=True)
        operation,event,success,_=owner.workers.pop();success(operation(event))
        APP.processEvents()
        service.setup.manager.inspect.assert_not_called()
        self.assertIs(service.snapshot.inventory,current)
        self.assertEqual(owner.workers,[])

    def test_concurrent_configuration_change_is_not_published_as_verified(self):
        owner,service,_,_=self.create()
        service.setup.manager.inspect.return_value=WirelessEnvironment(data={"revision":"d"*192})
        service.change("home",True,confirmed=True)
        operation,event,success,_=owner.workers.pop();success(operation(event))
        self.assertTrue(service.snapshot.stale)
        self.assertEqual(service.snapshot.inventory.revision,"a"*64)
        service._auto_refresh_blocked=True
        APP.processEvents()
        self.assertEqual(owner.workers,[])

    def test_failed_mutation_does_not_probe_environment_before_recovery(self):
        owner,service,_,_=self.create()
        service.manager.change.side_effect=SignalError("recovery","Recovery unconfirmed")
        service.change("home",True,confirmed=True)
        operation,event,success,_=owner.workers.pop();success(operation(event))
        APP.processEvents()
        service.setup.manager.inspect.assert_not_called()
        self.assertTrue(service.snapshot.stale)
        self.assertEqual(owner.workers,[])

    def test_verified_mutation_is_retained_when_followup_read_loses_a_channel(self):
        owner,service,current,_=self.create()
        service.setup.manager.inspect.side_effect=SignalError("transport","Status read channel closed")
        service.change("home",True,confirmed=True)
        operation,event,success,_=owner.workers.pop();success(operation(event))
        for _ in range(5):APP.processEvents()
        self.assertIs(service.snapshot.inventory,current)
        self.assertFalse(service.snapshot.stale)
        self.assertFalse(service.snapshot.busy)
        self.assertIsNone(service.setup.snapshot.environment)
        self.assertIn("Status read channel closed",service.setup.snapshot.detail)
        self.assertEqual(owner.workers,[])
        service.manager.change.assert_called_once()
        self.assertTrue(service.messages)

    def test_completed_old_router_mutation_is_not_published_after_reconnection(self):
        owner,service,current,_=self.create()
        service.change("home",True,confirmed=True)
        operation,event,success,_=owner.workers.pop();result=operation(event)
        owner.ssh._client=object()
        success(result)
        APP.processEvents()
        self.assertNotEqual(service.snapshot.inventory.revision,current.revision)
        self.assertTrue(service.snapshot.stale)
        self.assertEqual(owner.workers,[])

    def test_unknown_followup_exception_is_not_silently_converted_to_success(self):
        owner,service,_,_=self.create()
        service.setup.manager.inspect.side_effect=RuntimeError("Internal inspection error")
        service.change("home",True,confirmed=True)
        operation,event,_,failure=owner.workers.pop()
        with self.assertRaises(RuntimeError):operation(event)
        service._auto_refresh_blocked=True
        failure("Worker failed","Internal error")
        self.assertTrue(service.snapshot.stale)
