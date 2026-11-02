"""Both signal toggle routes retain bounded, secret-free execution evidence."""
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch
import json
import threading
import unittest
from modules.capture_services.openwrt.signal_manager import RouterSignalManager, SignalError
from modules.capture_services.openwrt.signal_models import RouterSignal, SignalInventory
from modules.capture_services.openwrt.operation_diagnostics import OperationDiagnostics, sanitized_operation_evidence


class DirectToggleEvidenceTests(unittest.TestCase):
    def fixture(self):
        journal=OperationDiagnostics()
        manager=RouterSignalManager(SimpleNamespace(operation_diagnostics=journal))
        manager._run=Mock(return_value={"committed":True})
        row=RouterSignal("home","private-ssid","ap","radio7","5g",True,False,"inactive")
        inventory=SignalInventory((row,),"a"*64)
        event=Mock(spec=threading.Event)
        event.is_set.return_value=False;event.wait.return_value=False
        return manager,journal,row,inventory,event

    def test_repeated_toggles_each_keep_their_own_requested_and_observed_states(self):
        manager,journal,row,inventory,event=self.fixture()
        for enabled in (True,False,True,False,True):
            result=SignalInventory((replace(row,disabled=not enabled,running="active" if enabled else "inactive"),),"b"*64)
            manager.read=Mock(return_value=result)
            self.assertIs(manager.change(inventory,"home",enabled,event),result)
            final=journal.snapshot()["configuration"][-1]
            self.assertEqual(final["source"],"direct_toggle")
            self.assertEqual(final["requested_enabled"],enabled)
            self.assertEqual(final["configured_enabled"],enabled)
            self.assertEqual(final["status"],"completed")
            self.assertEqual(final["band"],"5g")
            row=result.rows[0];inventory=result
        self.assertEqual(manager._run.call_count,5)
        exported=sanitized_operation_evidence(journal.snapshot(),SimpleNamespace(text=lambda value:value))
        self.assertNotIn("private-ssid",json.dumps(exported))

    def test_config_enabled_but_runtime_inactive_is_distinguished_from_config_not_applied(self):
        for disabled,reason in ((False,"wireless_runtime_pending"),(True,"wireless_config_disabled")):
            with self.subTest(disabled=disabled):
                manager,journal,row,inventory,event=self.fixture()
                manager.read=Mock(return_value=SignalInventory((replace(row,disabled=disabled),),"b"*64))
                with patch("modules.capture_services.openwrt.signal_manager.time.monotonic",side_effect=[0,0,46,46]):
                    with self.assertRaises(SignalError):manager.change(inventory,"home",True,event)
                final=journal.snapshot()["configuration_failures"][-1]
                self.assertEqual(final["reason"],reason)
                self.assertEqual(final["interface_state"],"inactive")
                self.assertEqual(final["verify_attempts"],1)
                self.assertEqual(len(journal.snapshot()["configuration"]),2)
                manager._run.assert_called_once()

    def test_config_conflict_before_polling_keeps_its_reason(self):
        manager,journal,_,inventory,event=self.fixture()
        manager._run.side_effect=SignalError("changed","Configuration changed")
        with self.assertRaises(SignalError):manager.change(inventory,"home",True,event)
        final=journal.snapshot()["configuration_failures"][-1]
        self.assertEqual(final["reason"],"changed")
        self.assertEqual(final["source"],"direct_toggle")
        self.assertEqual(final["verify_attempts"],0)
