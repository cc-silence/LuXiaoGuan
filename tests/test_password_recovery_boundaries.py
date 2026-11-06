"""Cancellation, total deadlines and failed durable writes during recovery."""
import copy
import json
from threading import Event
import unittest
from unittest.mock import Mock, patch
from tests import test_password_recovery as fixtures
from modules.capture_services.openwrt.password_recovery import recover_pending
from modules.capture_services.openwrt.password_recovery_store import validate


class RecoveryBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.RecoveryTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.journal = self.fixture.journal
        self.store = self.fixture.store

    def test_cancel_during_each_read_retains_record_and_stops_next_request(self):
        operations = ['password_recovery_changes', 'password_recovery_apply', 'password_restore_config_check',
                      'password_restore_wireless_check']
        for operation in operations:
            with self.subTest(operation=operation):
                runner = self.fixture.runner()
                original = runner.run.side_effect
                cancel = Event()
                def run(name, command, **options):
                    self.assertIs(options['cancel_event'], cancel)
                    result = original(name, command, **options)
                    if name == operation:
                        cancel.set()
                    return result
                runner.run.side_effect = run
                with self.assertRaises(InterruptedError):
                    self.journal.recover(runner, fixtures.FP, cancel)
                self.assertEqual(runner.run.call_args_list[-1].args[0], operation)
                self.assertEqual(len(self.store.records(fixtures.FP)), 1)

    def test_cancel_while_waiting_for_radio_preserves_record(self):
        pending = copy.deepcopy(fixtures.STATE)
        pending['radio1']['pending'] = True
        runner = self.fixture.runner(runtime=pending)
        cancel = Mock()
        cancel.is_set.return_value = False
        def wait(seconds):
            cancel.is_set.return_value = True
            return True
        cancel.wait.side_effect = wait
        with self.assertRaises(InterruptedError):
            recover_pending(self.store, runner, fixtures.FP, cancel)
        cancel.wait.assert_called_once()
        self.assertEqual(len(self.store.records(fixtures.FP)), 1)

    def test_total_budget_is_shared_by_apply_and_each_evidence_read(self):
        runner = self.fixture.runner()
        original = runner.run.side_effect
        clock = [0.0]
        def run(name, command, **options):
            self.assertGreater(options['timeout'], 0)
            self.assertLessEqual(options['timeout'], 5 - clock[0])
            result = original(name, command, **options)
            clock[0] += 1
            return result
        runner.run.side_effect = run
        with patch('time.monotonic', side_effect=lambda: clock[0]):
            with self.assertRaises(TimeoutError):
                self.journal.recover(runner, fixtures.FP, deadline=5)
        self.assertEqual(runner.run.call_count, 5)
        self.assertEqual(len(self.store.records(fixtures.FP)), 1)

    def test_expired_budget_submits_no_router_command(self):
        runner = self.fixture.runner()
        with patch('time.monotonic', return_value=10):
            with self.assertRaises(TimeoutError):
                self.journal.recover(runner, fixtures.FP, deadline=9)
        runner.run.assert_not_called()
        self.assertEqual(len(self.store.records(fixtures.FP)), 1)

    def test_failed_write_keeps_last_valid_memory_and_disk_scope(self):
        before = copy.deepcopy(self.journal.data)
        with patch.object(self.store, 'save', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.journal.arm(set(), set())
        self.assertEqual(self.journal.data, before)
        self.assertEqual(self.store.records(fixtures.FP), [before])
        self.journal.recover(self.fixture.runner(), fixtures.FP)
        self.assertEqual(self.store.records(fixtures.FP), [])

    def test_inconsistent_affected_radio_scope_is_rejected_before_mutation(self):
        path = next(self.fixture.root.glob('*.json'))
        data = copy.deepcopy(self.journal.data)
        data['radios'] = []
        path.write_bytes(json.dumps(data).encode('utf-8'))
        runner = self.fixture.runner()
        with self.assertRaises(ValueError):
            validate(data)
        with self.assertRaises(RuntimeError):
            recover_pending(self.store, runner, fixtures.FP)
        runner.run.assert_not_called()
        self.assertTrue(path.exists())


    def test_atomic_replace_failure_preserves_prior_record_and_removes_temp_file(self):
        before = copy.deepcopy(self.journal.data)
        with patch('modules.capture_services.openwrt.password_recovery_store.os.replace',
                   side_effect=OSError('replace denied')):
            with self.assertRaises(OSError):
                self.journal.arm(set(), set())
        self.assertEqual(self.journal.data, before)
        self.assertEqual(self.store.records(fixtures.FP), [before])
        self.assertEqual(list(self.fixture.root.glob('*.tmp')), [])

    def test_invalid_arm_does_not_poison_last_valid_recovery(self):
        before = copy.deepcopy(self.journal.data)
        with self.assertRaises(ValueError):
            self.journal.arm({'original'}, set())
        self.assertEqual(self.journal.data, before)
        self.journal.recover(self.fixture.runner(), fixtures.FP)
        self.assertEqual(self.store.records(fixtures.FP), [])


    def test_missing_progress_receiver_cannot_block_recovery(self):
        callback=Mock(side_effect=RuntimeError('UI receiver deleted'))
        runner=self.fixture.runner()
        with self.assertLogs('routermanager.capture', level='WARNING') as logs:
            recovered=recover_pending(self.store,runner,fixtures.FP,progress_callback=callback)
        self.assertEqual(recovered,1)
        self.assertEqual(self.store.records(fixtures.FP),[])
        self.assertNotIn('UI receiver deleted',' '.join(logs.output))

    def test_creation_owns_each_section_before_activating_its_real_type(self):
        journal=self.journal
        setup=['uci set wireless.'+journal.sta+'=wifi-iface',
               'uci set network.'+journal.net+'=interface']
        commands=journal.guarded_setup(setup)
        for package,section,kind in [('wireless',journal.sta,'wifi-iface'),('network',journal.net,'interface')]:
            pending='uci set '+package+'.'+section+'=routermanager_pw_'+fixtures.SID
            marker='uci set '+package+'.'+section+'.routermanager_session='+fixtures.SID
            active='uci set '+package+'.'+section+'='+kind
            self.assertLess(commands.index(pending),commands.index(marker))
            self.assertLess(commands.index(marker),commands.index(active))
        for invalid in [setup[:1],setup+setup]:
            with self.assertRaises(ValueError):journal.guarded_setup(invalid)


    def test_failed_progress_notification_does_not_override_explicit_cancel(self):
        cancel=Event()
        def notify(message):
            cancel.set()
            raise RuntimeError('UI receiver deleted')
        runner=self.fixture.runner()
        with self.assertLogs('routermanager.capture',level='WARNING'):
            with self.assertRaises(InterruptedError):
                recover_pending(self.store,runner,fixtures.FP,cancel,progress_callback=notify)
        runner.run.assert_not_called()
        self.assertEqual(len(self.store.records(fixtures.FP)),1)


    def test_boolean_schema_version_is_rejected_and_preserved(self):
        path=next(self.fixture.root.glob('*.json'))
        data=copy.deepcopy(self.journal.data);data['schema']=True
        path.write_bytes(json.dumps(data).encode('utf-8'))
        with self.assertRaises(ValueError):self.store.records(fixtures.FP)
        self.assertTrue(path.exists())

    def test_record_growth_after_stat_cannot_cause_unbounded_read(self):
        from pathlib import Path
        import os
        path=next(self.fixture.root.glob('*.json'))
        path.write_bytes(b' ' * 131073)
        original_stat=Path.stat
        def stat(p,*args,**options):
            result=original_stat(p,*args,**options)
            if p==path:
                fields=list(result);fields[6]=10
                return os.stat_result(fields)
            return result
        with patch.object(Path,'stat',stat):
            with self.assertRaisesRegex(ValueError,'exceeds limit'):
                self.store.records(fixtures.FP)
        self.assertTrue(path.exists())


if __name__ == '__main__':
    unittest.main()
