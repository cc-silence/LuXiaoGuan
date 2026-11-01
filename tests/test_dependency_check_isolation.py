"""Read-only dependency checks must not overwrite unrelated state."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import unittest
from unittest.mock import Mock, patch
from PySide6.QtWidgets import QWidget
from tests.test_dependency_group_controls import APP, GroupTaskTests, info
from modules.main_pages.packet_capture.openwrt.capture_dependency_progress import CaptureDependencyProgress
from modules.capture_services.dependency_service import DependencyTask
from tests.test_hcx_upgrade import Service


class DependencyCheckIsolationTests(unittest.TestCase):
    def test_single_tool_failure_keeps_other_installed_states(self):
        owner, service = GroupTaskTests().service((info("iw"), info("curl")))
        with patch("modules.capture_services.dependency_service.check_tools",
                   return_value=(info("iw", "unknown"), info("curl", "unknown"))) as check:
            self.assertTrue(service.check_tool_async("iw"))
            operation, event = owner.pending
            result = operation(event, Mock())
            self.assertEqual(check.call_args.args[1], ("iw",))
        service._complete(service.task.session_id, result)
        rows = {row.key: row.state.value for row in owner.openwrt_backend.snapshot.dependencies}
        self.assertEqual(rows, {"iw": "unknown", "curl": "ready"})
        self.assertFalse(owner.task_controller.busy)
        owner.deleteLater()

    def test_check_failure_closes_progress_but_install_failure_stays_reviewable(self):
        for action in ("check", "install"):
            with self.subTest(action=action):
                page = QWidget()
                service = Service()
                progress = CaptureDependencyProgress(page, service, lambda task: True)
                task = DependencyTask("fixture", "capture", ("password",), action, "Checking")
                service.taskChanged.emit(task)
                dialog = progress.dialog
                APP.processEvents()
                self.assertTrue(dialog.isVisible())
                service.taskFinished.emit("fixture", False, "Channel closed.")
                APP.processEvents()
                self.assertFalse(dialog.running)
                self.assertEqual(dialog.isVisible(), action == "install")
                progress.dispose()
                dialog.close()
                page.deleteLater()


if __name__ == "__main__":
    unittest.main()
