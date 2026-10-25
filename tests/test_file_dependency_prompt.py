"""Confirmed SFTP diagnosis, cancellation and connection-bound retries."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QWidget
from modules.main_pages.packet_capture.openwrt.file_dependency_prompt import FileDependencyPrompt

APP = QApplication.instance() or QApplication([])
MODULE = "modules.main_pages.packet_capture.openwrt.file_dependency_prompt."


class Dependency(QObject):
    taskChanged = Signal(object)
    taskFinished = Signal(str, bool, str)

    def __init__(self):
        super().__init__()
        self.check_common_async = Mock(side_effect=lambda: self.start("check"))
        self.install_async = Mock(side_effect=lambda *a, **k: self.start("install"))
        self.cancel = Mock()

    def start(self, action):
        self.taskChanged.emit(NS(group="common", session_id=action, message=action))
        return True


class FileDependencyTests(unittest.TestCase):
    def setUp(self):
        self.page = QWidget()
        self.dependency = Dependency()
        self.owner = NS(dependency_service=self.dependency, openwrt_snapshot=NS(dependencies=()))
        self.ssh = NS(connected=True, _client=object())
        self.page.service = NS(owner=self.owner, manager=NS(ssh=self.ssh))
        self.page._refresh = Mock()
        self.flow = FileDependencyPrompt(self.page)
        self.confirm = patch(MODULE+"ConfirmDialog.exec", return_value=1).start()
        self.message = patch(MODULE+"MessageDialog.exec", return_value=1).start()
        self.addCleanup(patch.stopall)

    def tearDown(self):
        self.flow.dispose()
        self.page.deleteLater()
        APP.processEvents()

    def result(self, session, state, success=True, message="result"):
        self.owner.openwrt_snapshot.dependencies = (NS(key="sftp", state=NS(value=state), detail=state),)
        self.dependency.taskFinished.emit(session, success, message)
        APP.processEvents()

    def begin(self):
        self.assertTrue(self.flow.handle_failure("EOF during negotiation"))
        self.dependency.check_common_async.assert_not_called()
        APP.processEvents()
        self.dependency.check_common_async.assert_called_once_with()

    def test_confirmed_missing_offers_install_and_verified_ready_retries(self):
        self.begin()
        # Public dependency service reports missing as a failed availability check.
        self.result("check", "missing", False)
        self.confirm.assert_called_once()
        self.dependency.install_async.assert_called_once_with({}, sftp_only=True)
        self.page._refresh.assert_not_called()
        self.result("install", "ready")
        self.page._refresh.assert_called_once_with(True)

    def test_handshake_error_does_not_imply_missing(self):
        for state in ("ready", "unknown"):
            self.begin()
            self.result("check", state, state=="ready")
            self.dependency.install_async.assert_not_called()
            self.confirm.assert_not_called()
            self.dependency.check_common_async.reset_mock()

    def test_decline_does_not_install_or_retry(self):
        self.confirm.return_value = 0
        self.begin()
        self.result("check", "missing", False)
        self.dependency.install_async.assert_not_called()
        self.page._refresh.assert_not_called()

    def test_cancelled_check_never_prompts_even_if_reprobe_finds_missing(self):
        self.begin()
        self.flow.dialog.request_cancel()
        self.result("check", "missing", False, "cancelled")
        self.confirm.assert_not_called()
        self.dependency.install_async.assert_not_called()

    def test_changed_connection_and_unrelated_results_are_ignored(self):
        self.begin()
        self.result("other", "missing", False)
        self.assertEqual(self.flow.session, "check")
        self.ssh._client = object()
        self.result("check", "missing", False)
        self.confirm.assert_not_called()
        self.page._refresh.assert_not_called()

    def test_install_failure_stops_without_retry(self):
        self.begin()
        self.result("check", "missing", False)
        self.result("install", "missing", False)
        self.page._refresh.assert_not_called()

    def test_completion_waits_until_old_task_publication_returns(self):
        self.begin()
        self.owner.openwrt_snapshot.dependencies = (NS(key="sftp", state=NS(value="missing"), detail="missing"),)
        self.dependency.taskFinished.emit("check", False, "not available")
        # The old completion still owns its worker during direct signal delivery.
        self.dependency.install_async.assert_not_called()
        APP.processEvents()
        self.dependency.install_async.assert_called_once_with({}, sftp_only=True)

    def test_dispose_before_queued_diagnosis_prevents_task(self):
        self.flow.handle_failure("EOF during negotiation")
        self.flow.dispose()
        APP.processEvents()
        self.dependency.check_common_async.assert_not_called()


class FilePageIntegrationTests(unittest.TestCase):
    def test_real_directory_failure_routes_to_dependency_check(self):
        from tests.test_file_selection import Service
        from modules.main_pages.packet_capture.openwrt.router_files_page import RouterFilesPage
        service=Service()
        page=RouterFilesPage(service)
        dependency=Dependency()
        owner=NS(dependency_service=dependency,openwrt_snapshot=NS(dependencies=()))
        service.owner=owner
        page._dependency_prompt.dispose()
        page._dependency_prompt=FileDependencyPrompt(page)
        page._navigate(True,"/root")
        key=service.calls[-1][0]
        with patch(MODULE+"MessageDialog.exec",return_value=1) as message:
            service.failed.emit(key,"EOF during negotiation")
            APP.processEvents()
            dependency.check_common_async.assert_called_once_with()
            message.assert_not_called()
        del service.owner
        page.dispose()
        page.deleteLater()
        APP.processEvents()


if __name__ == "__main__":
    unittest.main()
