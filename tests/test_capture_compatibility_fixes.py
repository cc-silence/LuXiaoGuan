"""Parallel capture initialization regression; no router required."""
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock
from modules.capture_services.parallel_handshake import _RadioBackend, RadioCapability

class ParallelInitializationTests(unittest.TestCase):
    def test_lane_uses_complete_common_resource_initialization_and_monitor_cleanup(self):
        ssh=NS(connected=True,host_fingerprint='')
        unit=_RadioBackend(ssh,RadioCapability('phy0',frozenset({('2.4G',6)})))
        self.assertIs(unit.ssh_client,ssh)
        self.assertIs(unit.radio_takeover.command_runner,unit.command_runner)
        self.assertFalse(unit.shutdown_event.is_set())
        unit.radio_session.restore=Mock(return_value=True)
        errors=unit._restore_task_wireless(NS(remote_dir='/tmp/owned'),None)
        self.assertEqual(errors,[]);unit.radio_session.restore.assert_called_once()
