"""Local recovery review keeps originals until explicitly confirmed."""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from PySide6.QtWidgets import QApplication, QWidget
from modules.capture_services.openwrt.password_recovery_store import RecoveryStore, RecoveryRecordCorrupt, RecoveryDirectoryError
from modules.capture_services.openwrt.password_recovery_admission import RecoveryRecordsUnavailable
from modules.main_pages.packet_capture.openwrt.recovery_access_prompt import RecoveryAccessPrompt
from PySide6.QtCore import QObject, Signal

FP = 'SHA256:' + 'a' * 43


class Signals(QObject):
    openwrtSnapshotChanged = Signal(object)


class RecoveryReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_confirmed_corrupt_file_is_backed_up_with_exact_bytes(self):
        with TemporaryDirectory() as folder:
            store = RecoveryStore(Path(folder) / 'records'); store.root.mkdir()
            path = store._path(FP, 'a' * 32); path.write_bytes(b'{invalid')
            with self.assertRaises(RecoveryRecordCorrupt) as caught:
                store.records(FP)
            issue = caught.exception
            self.assertTrue(path.exists())
            backup = store.backup_corrupt(issue)
            self.assertFalse(path.exists())
            self.assertEqual(backup.read_bytes(), b'{invalid')
            self.assertEqual(store.records(FP), [])

    def test_changed_file_is_never_moved(self):
        with TemporaryDirectory() as folder:
            store = RecoveryStore(Path(folder) / 'records'); store.root.mkdir()
            path = store._path(FP, 'a' * 32); path.write_bytes(b'{invalid')
            with self.assertRaises(RecoveryRecordCorrupt) as caught:
                store.records(FP)
            path.write_bytes(b'{changed')
            with self.assertRaises(ValueError): store.backup_corrupt(caught.exception)
            self.assertEqual(path.read_bytes(), b'{changed')

    def test_newer_schema_is_not_offered_as_corrupt(self):
        with TemporaryDirectory() as folder:
            store=RecoveryStore(Path(folder)/'records');store.root.mkdir()
            path=store._path(FP,'a'*32)
            content=json.dumps({'schema':2,'fingerprint':FP,'session':'a'*32}).encode()
            path.write_bytes(content)
            with self.assertRaises(ValueError) as caught:store.records(FP)
            self.assertNotIsInstance(caught.exception,RecoveryRecordCorrupt)
            self.assertEqual(path.read_bytes(),content)

    def test_unexpected_backup_failure_reports_finished(self):
        from modules.main_pages.packet_capture.openwrt.recovery_access_prompt import BackupWorker
        from PySide6.QtTest import QSignalSpy
        worker=BackupWorker(NS(backup_corrupt=Mock(side_effect=RuntimeError('private'))),object())
        spy=QSignalSpy(worker.signals.finished)
        with self.assertLogs('modules.main_pages.packet_capture.openwrt.recovery_access_prompt',level='WARNING'):
            worker.run()
        self.assertEqual(spy.count(),1)
        self.assertEqual(spy.at(0),[False])

    def test_directory_error_classification_does_not_expose_path(self):
        error = RecoveryRecordsUnavailable(RecoveryDirectoryError('private-path'))
        self.assertEqual(error.evidence['reason'], 'recovery_directory')
        self.assertNotIn('private-path', json.dumps(error.evidence))

    def test_user_confirmation_runs_real_backup_asynchronously(self):
        import time
        with TemporaryDirectory() as folder:
            store=RecoveryStore(Path(folder)/'records');store.root.mkdir()
            path=store._path(FP,'a'*32);path.write_bytes(b'{invalid')
            with self.assertRaises(RecoveryRecordCorrupt) as caught:store.records(FP)
            page=QWidget();self.addCleanup(page.close)
            service=Signals();page.service=service
            ssh=NS(connected=True,host_fingerprint=FP,_client=object())
            service.openwrt_backend=NS(ssh_client=ssh,password_recovery_store=store,
                reconnect_cleanup=NS(_password_check_issue=(FP,{'reason':'recovery_record_validation'}),
                                     _corrupt_password_record=caught.exception))
            prompt=RecoveryAccessPrompt(page);prompt._show()
            self.assertTrue(path.exists())
            prompt._dialog.yes_button.click()
            deadline=time.monotonic()+5
            while time.monotonic()<deadline and (path.exists() or prompt._worker is not None):
                self.app.processEvents();time.sleep(.01)
            self.app.processEvents()
            self.assertFalse(path.exists())
            backups=list((store.root/'recovery-backups').iterdir())
            self.assertEqual(len(backups),1);self.assertEqual(backups[0].read_bytes(),b'{invalid')
            if prompt._dialog is not None:prompt._dialog.close()

    def test_user_declining_review_leaves_record_unchanged(self):
        with TemporaryDirectory() as folder:
            store=RecoveryStore(Path(folder)/'records');store.root.mkdir()
            path=store._path(FP,'a'*32);path.write_bytes(b'{invalid')
            with self.assertRaises(RecoveryRecordCorrupt) as caught:store.records(FP)
            page=QWidget();self.addCleanup(page.close)
            service=Signals();page.service=service
            ssh=NS(connected=True,host_fingerprint=FP,_client=object())
            service.openwrt_backend=NS(ssh_client=ssh,password_recovery_store=store,
                reconnect_cleanup=NS(_password_check_issue=(FP,{'reason':'recovery_record_validation'}),
                                     _corrupt_password_record=caught.exception))
            prompt=RecoveryAccessPrompt(page);prompt._show();prompt._dialog.no_button.click()
            self.assertEqual(path.read_bytes(),b'{invalid')
            self.assertFalse((store.root/'recovery-backups').exists())

    def test_directory_issue_displays_move_recommendation_once(self):
        page = QWidget(); self.addCleanup(page.close)
        service = Signals(); page.service = service
        ssh = NS(connected=True, host_fingerprint=FP, _client=object())
        backend = NS(ssh_client=ssh, password_recovery_store=NS(root=Path('test-records')),
                     reconnect_cleanup=NS(_password_check_issue=(FP, {'reason':'recovery_directory'})))
        service.openwrt_backend = backend
        prompt = RecoveryAccessPrompt(page)
        prompt._show()
        self.assertIsNotNone(prompt._dialog)
        first = prompt._dialog
        self.assertTrue(first.isVisible())
        prompt._show()
        self.assertIs(prompt._dialog, first)
        first.close()


if __name__ == '__main__': unittest.main()
