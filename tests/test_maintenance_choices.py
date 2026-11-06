"""Both consent paths, Chinese default names and Unicode archive round trips."""
from datetime import datetime
from pathlib import Path
from threading import Event
from unittest.mock import Mock, patch
import tempfile
import unittest
from tests.common_settings_fixture import create_page, APP
from tests import test_router_backup as fixture
from modules.capture_services.openwrt.router_backup import RouterBackupManager
from modules.main_pages.packet_capture.openwrt.maintenance_review_dialog import MaintenanceReviewDialog
from modules.main_pages.packet_capture.openwrt.backup_filenames import backup_filename


class MaintenanceChoicesTests(unittest.TestCase):
    create_page = create_page

    def dialog_factory(self, backup):
        def make(review, path, parent):
            dialog = MaintenanceReviewDialog(review, path, parent)
            def choose():
                if hasattr(dialog, "confirm_reset"):
                    self.assertFalse(dialog.go.isEnabled())
                    self.assertFalse(dialog.direct_button.isEnabled())
                    dialog.confirm_reset.click()
                (dialog.go if backup else dialog.direct_button).click()
                return dialog.result()
            dialog.exec = choose
            return dialog
        return make

    def test_each_operation_submits_explicit_backup_choice(self):
        for operation in ("reset", "restore"):
            for backup in (False, True):
                page, _ = self.create_page()
                actions = page.maintenance_actions
                actions.pending["preview"] = ("preview", actions._identity())
                review = dict(operation=operation, model="Router", version="23.05.4", entries=1, size=100, files=[])
                with patch.object(actions, "_destination", return_value=("C:/before.tar.gz", None)) as picker, patch(
                    "modules.main_pages.packet_capture.openwrt.maintenance_actions.MaintenanceReviewDialog",
                    side_effect=self.dialog_factory(backup)):
                    self.assertTrue(actions.completed("preview", review))
                self.assertEqual(picker.call_count, int(backup))
                call = page.manager.submit.call_args
                self.assertEqual(call.args, ("maintenance_apply",))
                self.assertEqual(call.kwargs["backup_path"], "C:/before.tar.gz" if backup else None)

    def test_cancelling_backup_picker_never_falls_through_to_direct_execution(self):
        page, _ = self.create_page()
        actions = page.maintenance_actions
        actions.pending["preview"] = ("preview", actions._identity())
        review = dict(operation="reset", model="Router", version="23.05.4")
        with patch.object(actions, "_destination", return_value=None), patch(
            "modules.main_pages.packet_capture.openwrt.maintenance_actions.MaintenanceReviewDialog",
            side_effect=self.dialog_factory(True)):
            actions.completed("preview", review)
        page.manager.submit.assert_not_called()

    def test_chinese_default_names(self):
        now = datetime(2026, 11, 5, 16, 30, 0)
        self.assertEqual(backup_filename(now=now), "\u914d\u7f6e\u5907\u4efd_20261105_163000.tar.gz")
        self.assertEqual(backup_filename("\u6062\u590d\u51fa\u5382\u8bbe\u7f6e", now=now),
                         "\u6267\u884c\u6062\u590d\u51fa\u5382\u8bbe\u7f6e\u524d\u5907\u4efd_20261105_163000.tar.gz")

    def test_chinese_filename_backup_can_restore_using_ascii_remote_name(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            device = fixture.Device(root, "23.05.4")
            manager = RouterBackupManager(device.manager)
            destination = root / "\u914d\u7f6e\u5907\u4efd_\u6d4b\u8bd5.tar.gz"
            manager.export(destination, None, Event(), Mock())
            review = manager.preview("restore", destination, Event())
            result = manager.execute(review, None, None, Event(), Mock())
            self.assertEqual(result["outcome"], "reconnect")
            apply = next(command for name, command in device.commands if name == "maintenance_apply")
            self.assertIn("/restore.tar.gz", apply)
            self.assertNotIn(destination.name, apply)
            self.assertFalse(device.remote)

    def test_connection_changed_during_backup_picker_blocks_both_operations(self):
        for operation in ("reset", "restore"):
            page, _ = self.create_page()
            actions = page.maintenance_actions
            actions.pending["preview"] = ("preview", actions._identity())
            review = dict(operation=operation, model="Router", version="23.05.4", entries=1, size=100, files=[])
            def switched(*args, **kwargs):
                page.manager.manager.ssh._client = object()
                return "C:/before.tar.gz", None
            with patch.object(actions, "_destination", side_effect=switched), patch(
                "modules.main_pages.packet_capture.openwrt.maintenance_actions.MaintenanceReviewDialog",
                side_effect=self.dialog_factory(True)):
                actions.completed("preview", review)
            page.manager.submit.assert_not_called()

    def test_reset_backup_failure_never_starts_destructive_command(self):
        with tempfile.TemporaryDirectory() as folder:
            device = fixture.Device(folder, "23.05.4")
            manager = RouterBackupManager(device.manager)
            review = manager.preview("reset", "", Event())
            device.backup_error = True
            with self.assertRaises(RuntimeError):
                manager.execute(review, Path(folder)/"before.tar.gz", None, Event(), Mock())
            self.assertNotIn("maintenance_apply", [name for name, _ in device.commands])
