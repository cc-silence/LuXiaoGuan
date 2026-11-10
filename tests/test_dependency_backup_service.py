import unittest
from unittest.mock import Mock, patch
from PySide6.QtWidgets import QWidget, QVBoxLayout
from tests.test_standalone import APP
from tests.test_dependency_diagnostic_paths import Owner as BaseOwner
from modules.capture_services.dependency_backup_service import DependencyBackupService
from modules.main_pages.packet_capture.openwrt.dependency_backup_card import DependencyBackupCard


class Owner(BaseOwner):
    @property
    def openwrt_snapshot(self):
        return self.openwrt_backend.snapshot

    def _start_openwrt_worker(self, session, operation, event, success, failure, progress=None, message=None):
        self.pending = operation, event, success, failure, progress


class ServiceTests(unittest.TestCase):
    def owner(self):
        owner = Owner()
        self.addCleanup(owner.deleteLater)
        owner.dependency_service = Mock()
        return owner

    def test_worker_owns_task_and_double_click_rejected(self):
        owner = self.owner()
        service = DependencyBackupService(owner)
        completed = Mock(); service.completed.connect(completed)
        failed = Mock(); service.failed.connect(failed)
        with patch("modules.capture_services.openwrt.installed_dependency_backup.InstalledDependencyBackup") as task:
            task.return_value.backup.return_value = {"action":"backup","count":1,"stored":1,"path":"test.zip"}
            self.assertTrue(service.backup("test.zip"))
            self.assertTrue(service.active)
            self.assertFalse(service.backup("second.zip"))
            operation,event,success,_,_ = owner.pending
            result = operation(event,Mock())
            success(result)
        self.assertFalse(service.active)
        completed.assert_called_once_with(result)
        task.return_value.backup.assert_called_once_with("test.zip")

    def test_worker_start_failure_releases_busy_state(self):
        owner = self.owner()
        service = DependencyBackupService(owner)
        owner._start_openwrt_worker = Mock(side_effect=RuntimeError("fixture"))
        failed = Mock(); service.failed.connect(failed)
        self.assertFalse(service.backup("test.zip"))
        self.assertFalse(service.active)
        self.assertIsNone(service.cancel_event)
        failed.assert_called_once()

    def test_connection_switched_before_worker_starts(self):
        owner=self.owner();service=DependencyBackupService(owner)
        with patch("modules.capture_services.openwrt.installed_dependency_backup.InstalledDependencyBackup") as task:
            self.assertTrue(service.preview("test.zip"))
            operation,event,_,failure,_=owner.pending
            owner.openwrt_backend.ssh_client._client=object()
            with self.assertRaises(RuntimeError):
                operation(event,Mock())
            task.assert_not_called()
            failure("changed","changed")
        self.assertFalse(service.active)

    def test_cancel_and_page_disposal_do_not_own_remote_work(self):
        owner=self.owner();parent=QWidget();layout=QVBoxLayout(parent)
        card=DependencyBackupCard(parent,owner);layout.addWidget(card.card)
        self.assertTrue(card.backup_button.isEnabled())
        card.service.active=True;card.service.cancel_event=Mock()
        card.service.changed.emit()
        self.assertFalse(card.backup_button.isEnabled())
        card.dispose()
        card.service.cancel_event.set.assert_not_called()
        parent.deleteLater()

    def test_restoration_updates_shared_dependency_state(self):
        owner=self.owner();service=DependencyBackupService(owner)
        with patch("modules.capture_services.openwrt.installed_dependency_backup.InstalledDependencyBackup") as task, patch(
                "modules.capture_services.dependency_state_sync.refresh_related",return_value=("verified",)):
            task.return_value.restore.return_value={"action":"restore","installed":("curl",),"skipped":("libc",)}
            self.assertTrue(service.restore({"source":"test.zip"}))
            operation,event,success,_,_=owner.pending
            result=operation(event,Mock());success(result)
        owner.dependency_service.publish_verified.assert_called_once_with(
            ("verified",),(owner.openwrt_backend.ssh_client._client,"fixture"),notify=True)

