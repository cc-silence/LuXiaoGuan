"""Offline cleanup regressions: preserve the primary failure and return restore warnings."""
import unittest
import threading
from types import SimpleNamespace
from unittest.mock import Mock, patch
from modules.capture_services.openwrt.operation_diagnostics import OperationDiagnostics
from modules.capture_services.openwrt.password_verification import OpenWrtPasswordVerifier, PasswordTargetResult


class VerificationCleanupTests(unittest.TestCase):
    def make_verifier(self):
        verifier=OpenWrtPasswordVerifier(SimpleNamespace(operation_diagnostics=OperationDiagnostics(),run=Mock()))
        verifier._backup_configs=Mock()
        verifier._restore=Mock(return_value='')
        self.entry=PasswordTargetResult('02:11:22:33:44:55','fixture',False,0,1)
        verifier._verify_single=Mock(return_value={'setup_done':True,'entry':self.entry})
        return verifier

    def test_restore_warning_is_in_returned_results(self):
        verifier=self.make_verifier()
        verifier._restore.return_value='restore unverified'
        result=verifier.verify_targets([object()],['fixture-candidate'],'test','.')
        self.assertIn('restore unverified',result[0].note)
        self.assertEqual(verifier.last_restore_errors,'restore unverified')

    def test_restore_exception_cannot_replace_primary_prepare_exception(self):
        verifier=self.make_verifier()
        primary=ValueError('preparation failed')
        verifier._verify_single.side_effect=primary
        verifier._restore.side_effect=RuntimeError('restore failed')
        with self.assertRaises(ValueError) as caught:
            verifier.verify_targets([object()],['fixture-candidate'],'test','.')
        self.assertIs(caught.exception,primary)
        self.assertTrue(verifier.last_restore_errors)

    def test_pause_finalization_exception_cannot_skip_router_restore(self):
        verifier=self.make_verifier()
        pause=SimpleNamespace(checkpoint=Mock(),finish=Mock(side_effect=RuntimeError('finish failed')))
        with self.assertRaises(RuntimeError):
            verifier.verify_targets([object()],['fixture-candidate'],'test','.',pause_control=pause)
        verifier._restore.assert_called_once()

    def test_pause_error_does_not_mask_primary_or_skip_restore(self):
        verifier=self.make_verifier()
        primary=ValueError('preparation failed')
        verifier._verify_single.side_effect=primary
        pause=SimpleNamespace(checkpoint=Mock(),finish=Mock(side_effect=RuntimeError('private-finish-secret')))
        with self.assertRaises(ValueError) as caught:
            verifier.verify_targets([object()],['fixture-candidate'],'test','.',pause_control=pause)
        self.assertIs(caught.exception,primary)
        verifier._restore.assert_called_once()
        journal=verifier.command_runner.operation_diagnostics.snapshot()
        self.assertTrue(any(r.get('stage')=='finish_control' for r in journal['failures']))
        self.assertNotIn('private-finish-secret',str(journal))

    def test_restore_exception_after_success_is_not_reported_as_success(self):
        verifier=self.make_verifier()
        verifier._restore.side_effect=TimeoutError('restore timed out')
        with self.assertRaises(TimeoutError):verifier.verify_targets([object()],['fixture-candidate'],'test','.')
        self.assertTrue(verifier.last_restore_errors)
        journal=verifier.command_runner.operation_diagnostics.snapshot()
        self.assertEqual(journal['failures'][-1]['status'],'timeout')

    def test_cancel_after_selection_before_first_write_does_not_restart_network(self):
        runner=SimpleNamespace(operation_diagnostics=OperationDiagnostics(),run=Mock())
        verifier=OpenWrtPasswordVerifier(runner)
        event=threading.Event()
        target=SimpleNamespace(bssid='02:11:22:33:44:55',ssid='fixture',encryption='WPA2-PSK',band='2.4G')
        def select(*args):
            event.set()
            return 'radio7'
        with patch.object(verifier,'_backup_configs'), patch.object(verifier,'_pick_radio',side_effect=select):
            with self.assertRaises(RuntimeError):verifier.verify_targets([target],['fixture-candidate'],'test','.',cancel_event=event)
        runner.run.assert_not_called()
        self.assertFalse(verifier._configuration_touched)

if __name__=='__main__':unittest.main()
