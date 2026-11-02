"""First inventory and editing metadata settle before controls unlock."""
from unittest.mock import Mock
import unittest
from tests.test_feedback_regressions import APP, Owner
from modules.capture_services.router_signal_service import RouterSignalService
from modules.capture_services.openwrt.signal_models import SignalInventory, SignalSnapshot
from modules.capture_services.openwrt.signal_manager import SignalError
from modules.capture_services.openwrt.wireless_setup_models import WirelessEnvironment


class InitialCompletionTests(unittest.TestCase):
    def create(self):
        owner = Owner()
        self.addCleanup(owner.deleteLater)
        service = RouterSignalService(owner)
        service.snapshot = SignalSnapshot(connected=True)
        inventory = SignalInventory(revision="a" * 64)
        environment = WirelessEnvironment(data={"revision": "a" * 192})
        service.manager.read = Mock(return_value=inventory)
        service.setup.manager.inspect = Mock(return_value=environment)
        return owner, service, inventory, environment

    def complete(self, owner):
        operation, event, success, failure = owner.workers.pop(0)
        success(operation(event))
        for _ in range(10):
            APP.processEvents()

    def test_login_prepares_unopened_page_without_second_busy_cycle(self):
        owner, service, inventory, environment = self.create()
        states = []
        service.snapshotChanged.connect(lambda value: states.append(value.busy))
        self.assertTrue(service.refresh())
        self.complete(owner)
        self.assertIs(service.snapshot.inventory, inventory)
        self.assertIs(service.setup.snapshot.environment, environment)
        service.setup.enable()
        for _ in range(10):
            APP.processEvents()
        self.assertEqual(owner.workers, [])
        service.manager.read.assert_called_once()
        service.setup.manager.inspect.assert_called_once()
        first_idle = states.index(False)
        self.assertNotIn(True, states[first_idle:])

    def test_page_opened_during_read_uses_same_completion(self):
        owner, service, _, _ = self.create()
        service.refresh()
        service.setup.enable()
        APP.processEvents()
        self.assertEqual(len(owner.workers), 1)
        self.complete(owner)
        self.assertEqual(owner.workers, [])
        service.setup.manager.inspect.assert_called_once()

    def test_followup_probe_failure_preserves_inventory_without_retry(self):
        owner, service, inventory, _ = self.create()
        service.setup.manager.inspect.side_effect = SignalError("transport", "Channel closed")
        service.refresh()
        service.setup.enable()
        self.complete(owner)
        self.assertIs(service.snapshot.inventory, inventory)
        self.assertFalse(service.snapshot.busy)
        self.assertFalse(service.snapshot.stale)
        self.assertEqual(owner.workers, [])
        self.assertIn("Channel closed", service.setup.snapshot.detail)

    def test_current_environment_is_reused_by_next_refresh(self):
        owner, service, _, _ = self.create()
        service.refresh()
        self.complete(owner)
        service.setup.enable()
        service.refresh()
        self.complete(owner)
        service.setup.manager.inspect.assert_called_once()
        self.assertEqual(owner.workers, [])

    def test_old_connection_result_is_rejected(self):
        owner, service, _, _ = self.create()
        service.refresh()
        operation, event, success, failure = owner.workers.pop()
        result = operation(event)
        owner.ssh._client = object()
        success(result)
        APP.processEvents()
        self.assertNotEqual(service.snapshot.inventory.revision, "a" * 64)
        self.assertTrue(service.snapshot.stale)


if __name__ == "__main__":
    unittest.main()
