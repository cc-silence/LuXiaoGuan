"""Cleanup reports outcomes and only touches resources from this attempt."""
from pathlib import Path
from unittest.mock import Mock
import unittest
from modules.capture_services.openwrt.auto_capture_service import AutoCaptureRadioSession
from modules.capture_services.openwrt.auto_capture_cleanup import remove_monitor
from modules.capture_services.openwrt.command_runner import RemoteCommandResult
from modules.capture_services.openwrt.models import OpenWrtRadioInfo


class CleanupTests(unittest.TestCase):
    def session(self):
        service = Mock()
        runner = Mock()
        runner.run.return_value = RemoteCommandResult('cleanup', 0, 'REMOVED')
        session = AutoCaptureRadioSession(
            OpenWrtRadioInfo(name='radio1', interface='phy7'), runner, Mock(), Path('.'), service)
        return session, service, runner

    def test_early_failure_does_not_delete_uncreated_interface_and_finishes(self):
        session, service, runner = self.session()
        session.stats.error_message = 'Channel closed'
        session._cleanup_session(Mock())
        runner.run.assert_not_called()
        self.assertEqual(session.stats.state, 'error')
        messages = ''.join(c.args[1] for c in service.radioStreamOutput.emit.call_args_list)
        self.assertIn('\u672a\u8fdb\u5165\u76d1\u542c\u7f51\u5361\u521b\u5efa', messages)
        self.assertIn('\u6536\u5c3e\u5b8c\u6210', messages)

    def test_created_monitor_is_checked_and_flag_cleared(self):
        session, service, runner = self.session()
        session._monitor_creation_attempted = True
        session._cleanup_session(Mock())
        runner.run.assert_called_once()
        self.assertFalse(session._monitor_creation_attempted)
        self.assertEqual(session.stats.state, 'idle')
        command = runner.run.call_args.args[1]
        self.assertIn('wiphy 7', command)
        self.assertIn('type monitor', command)
        self.assertIn('test', command.replace('[', 'test'))

    def test_removal_error_keeps_ownership_and_never_claims_success(self):
        session, service, runner = self.session()
        session._monitor_creation_attempted = True
        runner.run.side_effect = RuntimeError('Channel closed')
        session._cleanup_session(Mock())
        self.assertTrue(session._monitor_creation_attempted)
        self.assertEqual(session.stats.state, 'error')
        messages = ''.join(c.args[1] for c in service.radioStreamOutput.emit.call_args_list)
        self.assertIn('\u6536\u5c3e\u672a\u5b8c\u6210', messages)
        self.assertNotIn('\u6293\u5305\u5df2\u5b89\u5168\u505c\u6b62', messages)

    def test_restore_failure_keeps_recovery_record(self):
        session, service, runner = self.session()
        record = session.takeover_record = object()
        takeover = Mock()
        session._recovery = Mock(record=record)
        session._recovery.finish.return_value = ['restore failed']
        session._recovery.publish.return_value = ''
        session._cleanup_session(takeover)
        self.assertIs(session.takeover_record, record)
        self.assertIn('restore failed', session.stats.error_message)

    def test_stopping_session_cannot_start_second_worker(self):
        session, service, runner = self.session()
        session.stats.state = 'stopping'
        session.start('', False)
        self.assertIsNone(session._worker_thread)

    def test_channel_failure_stays_busy_until_cleanup_finishes(self):
        session, service, runner = self.session()
        runner.run.side_effect = RuntimeError('Channel closed')
        original = session._cleanup_session
        def cleanup(takeover):
            self.assertEqual(session.stats.state, 'stopping')
            original(takeover)
        session._cleanup_session = cleanup
        session._run_capture_pipeline('', False)
        self.assertEqual(session.stats.state, 'error')
        self.assertEqual(runner.run.call_count, 1)
        self.assertFalse(session._monitor_creation_attempted)

    def test_cleanup_verification_rejects_timeout_and_wrong_reply(self):
        for result in (RemoteCommandResult('cleanup', -1, timed_out=True),
                       RemoteCommandResult('cleanup', 42, 'wrong type'),
                       RemoteCommandResult('cleanup', 0, '')):
            runner = Mock()
            runner.run.return_value = result
            with self.assertRaises((RuntimeError, TimeoutError)):
                remove_monitor(runner, 'hcxmon1', 'phy7')

    def test_cleanup_rejects_unowned_name_without_remote_command(self):
        runner = Mock()
        with self.assertRaises(RuntimeError):
            remove_monitor(runner, 'wlan0', 'phy7')
        runner.run.assert_not_called()
