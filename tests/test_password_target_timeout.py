"""Single-target timeout isolation, cleanup, history and batch-wide failures."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import Mock,patch
from modules.capture_services.openwrt.models import AccessPointInfo
from modules.capture_services.openwrt.password_verification import OpenWrtPasswordVerifier,PasswordVerifyCancelled
from modules.capture_services.openwrt.password_control import ControlRequestTimeout
from modules.capture_services.openwrt.password_history import PasswordAttemptHistory
from modules.capture_services.openwrt.password_reporting import PasswordVerificationReporter
from modules.capture_services.openwrt.password_rounds import CandidateRound

TARGETS=tuple(AccessPointInfo('02:11:22:33:44:'+f'{n:02x}',ssid=chr(65+n),band='2.4G',encryption='WPA2-PSK') for n in range(3))

class PasswordTargetTimeoutTests(unittest.TestCase):
    def fixture(self):
        runner=Mock();runner.ssh_client.connected=True
        verifier=OpenWrtPasswordVerifier(runner)
        verifier._backup_configs=Mock();verifier._restore=Mock(return_value='')
        verifier._pick_radio=Mock(return_value='radio7');verifier._setup_environment=Mock()
        controls=[Mock(),Mock(),Mock()]
        controls[0].attempt.return_value=True
        controls[1].attempt.side_effect=ControlRequestTimeout('status')
        controls[2].attempt.return_value=True
        return verifier,controls

    def test_second_timeout_continues_third_rebuilds_and_never_marks_unknown_candidate(self):
        verifier,controls=self.fixture();messages=[]
        reporter=PasswordVerificationReporter(TARGETS,{},lambda text,level:messages.append((text,level)),None)
        with TemporaryDirectory() as folder,PasswordAttemptHistory(Path(folder)/'history',key=b'k'*32) as history,patch('modules.capture_services.openwrt.password_control.open_control',side_effect=controls):
            results=verifier.verify_targets(TARGETS,['candidate'],'owned',folder,target_callback=reporter.target_event,attempt_history=history)
            self.assertEqual([result.found for result in results],[True,False,True])
            self.assertTrue(results[1].error);self.assertEqual(results[1].tried,1)
            self.assertFalse(history.contains(TARGETS[1],'candidate'))
            self.assertFalse(history.contains(TARGETS[0],'candidate'));self.assertFalse(history.contains(TARGETS[2],'candidate'))
        self.assertEqual(verifier._setup_environment.call_count,2)
        verifier._backup_configs.assert_called_once();verifier._restore.assert_called_once()
        for control in controls:control.close.assert_called_once()
        self.assertEqual(reporter.counts['error'],1);self.assertEqual(reporter.counts['not_found'],0)
        self.assertTrue(any('\u8d85\u65f6' in text for text,_ in messages))

    def test_failed_target_is_not_retried_in_later_dictionary_rounds(self):
        verifier,controls=self.fixture()
        rounds=(CandidateRound('one',('candidate',)),CandidateRound('two',('another-candidate',)))
        with TemporaryDirectory() as folder,patch('modules.capture_services.openwrt.password_control.open_control',side_effect=controls):
            results=verifier.verify_targets(TARGETS,['candidate','another-candidate'],'owned',folder,rounds=rounds,dictionary_first=True)
        self.assertEqual(len(results),3)
        self.assertTrue(results[1].error);self.assertTrue(results[1].final)
        for control in controls:self.assertEqual(control.attempt.call_count,1)

    def test_ssh_loss_still_aborts_and_runs_restore(self):
        verifier,controls=self.fixture()
        def lost(*args):
            verifier.command_runner.ssh_client.connected=False
            raise ControlRequestTimeout('status')
        controls[1].attempt.side_effect=lost
        with TemporaryDirectory() as folder,patch('modules.capture_services.openwrt.password_control.open_control',side_effect=controls):
            with self.assertRaises(TimeoutError):verifier.verify_targets(TARGETS,['candidate'],'owned',folder)
        controls[2].attempt.assert_not_called();verifier._restore.assert_called_once()

    def test_cancel_during_timeout_still_aborts_and_runs_restore(self):
        verifier,controls=self.fixture();event=Event()
        def cancelled(*args):
            event.set();raise ControlRequestTimeout('status')
        controls[1].attempt.side_effect=cancelled
        with TemporaryDirectory() as folder,patch('modules.capture_services.openwrt.password_control.open_control',side_effect=controls):
            with self.assertRaises(PasswordVerifyCancelled):verifier.verify_targets(TARGETS,['candidate'],'owned',folder,event)
        controls[2].attempt.assert_not_called();verifier._restore.assert_called_once()

    def test_next_environment_preparation_failure_is_not_swallowed(self):
        verifier,controls=self.fixture()
        verifier._setup_environment.side_effect=[None,RuntimeError('controlled preparation failure')]
        with TemporaryDirectory() as folder,patch('modules.capture_services.openwrt.password_control.open_control',side_effect=controls):
            with self.assertRaisesRegex(RuntimeError,'controlled preparation failure'):
                verifier.verify_targets(TARGETS,['candidate'],'owned',folder)
        controls[2].attempt.assert_not_called();verifier._restore.assert_called_once()

    def test_control_cleanup_failure_still_aborts_batch(self):
        verifier,controls=self.fixture()
        controls[1].close.side_effect=RuntimeError('controlled cleanup failure')
        with TemporaryDirectory() as folder,patch('modules.capture_services.openwrt.password_control.open_control',side_effect=controls):
            with self.assertRaisesRegex(RuntimeError,'controlled cleanup failure'):
                verifier.verify_targets(TARGETS,['candidate'],'owned',folder)
        controls[2].attempt.assert_not_called();verifier._restore.assert_called_once()

    def test_summary_counts_timeouts_separately(self):
        from modules.capture_services.capture_service import CaptureService
        from modules.capture_services.openwrt.models import OpenWrtSessionSnapshot
        from modules.capture_services.openwrt.password_verification import PasswordTargetResult
        service=Mock();service.openwrt_backend.snapshot=OpenWrtSessionSnapshot(task_state='capturing')
        result=PasswordTargetResult(TARGETS[1].bssid,'B',False,1,2,error=True)
        CaptureService._on_openwrt_password_succeeded(service,((result,),''))
        summary=service.messageGenerated.emit.call_args.args[0]
        self.assertIn('\u672a\u627e\u5230 0',summary)
        self.assertIn('\u8d85\u65f6\u672a\u5b8c\u6210 1',summary)
