"""Recovery observes asynchronous teardown/setup without another reload."""
import copy
import json
import unittest
from unittest.mock import Mock, patch
from tests import test_password_restore as fixtures
from tests.test_password_restore import STATE, FLAGS, DEVICES
from modules.capture_services.openwrt.command_runner import RemoteCommandResult

MODULE = 'modules.capture_services.openwrt.password_restore'


class RestoreSettleTests(unittest.TestCase):
    def run_state(self, states, seconds=3):
        runner, check = fixtures.RestoreTests().fixture()
        old_run = runner.run.side_effect
        states = iter(states)
        latest = [None]
        clock = [0.0]
        reads = []
        def run(operation, command, **options):
            reads.append((operation, options['timeout']))
            if operation == 'password_restore_wireless_check':
                latest[0] = next(states, latest[0])
                return RemoteCommandResult(operation, 0, json.dumps(latest[0]))
            return old_run(operation, command, **options)
        runner.run.side_effect = run
        event = Mock()
        event.is_set.return_value = False
        event.wait.side_effect = lambda seconds: clock.__setitem__(0, clock[0]+seconds) or False
        with patch(MODULE+'.time.monotonic', side_effect=lambda: clock[0]), patch(MODULE+'.Event', return_value=event):
            message = check.verify('owned_sta','owned_net',FLAGS,DEVICES,set(FLAGS),wait_seconds=seconds)
        return message, reads, clock[0]

    def test_setup_in_progress_then_recovers(self):
        pending = copy.deepcopy(STATE)
        pending['radio7']['pending'] = True
        pending['radio7']['up'] = False
        message, reads, elapsed = self.run_state([pending, STATE])
        self.assertEqual(message, '')
        self.assertEqual(elapsed, 1)
        self.assertEqual(sum(op == 'password_restore_config_check' for op, _ in reads), 1)
        self.assertEqual(sum(op == 'password_restore_wireless_check' for op, _ in reads), 2)
        self.assertTrue(all('reload' not in op for op, _ in reads))

    def test_permanent_pending_times_out_with_warning(self):
        pending = copy.deepcopy(STATE)
        pending['radio7']['pending'] = True
        message, reads, elapsed = self.run_state([pending])
        self.assertTrue(message)
        self.assertEqual(elapsed, 3)
        self.assertTrue(all(timeout <= 3 for op, timeout in reads if op != 'password_restore_config_check'))

    def test_recovered_state_returns_immediately(self):
        message, reads, elapsed = self.run_state([STATE])
        self.assertEqual(message, '')
        self.assertEqual(elapsed, 0)
        self.assertEqual(len(reads), 3)

    def test_missing_interface_during_teardown_is_rechecked(self):
        message, reads, elapsed = self.run_state([{}, STATE])
        self.assertEqual(message, '')
        self.assertEqual(elapsed, 1)
