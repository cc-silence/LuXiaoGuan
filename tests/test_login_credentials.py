"""Current standalone login credentials; workers are intercepted before page creation."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dataclasses import replace
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication
from modules.capture_services.capture_service import CaptureService
from modules.capture_services.openwrt.models import OpenWrtConnectionResult, OpenWrtConnectionState
from modules.main_pages.packet_capture.openwrt.router_login_page import RouterLoginPage


class LoginCredentialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.service = CaptureService()
        self.settings = {"host": "router.example", "port": 22, "user": "root",
                         "auth_method": "password", "remember_password": False}
        self.operations = []
        # Page construction now starts offline-bundle inspection; never launch a real
        # QThread in a fixture that later deletes its service directly.
        self.service._start_openwrt_worker = lambda sid, operation, *args, **kwargs: self.operations.append(operation)
        self.page = RouterLoginPage(self.service, self.settings)
        self.service.openwrt_backend.connect = Mock()
        self.states = []
        self.service.openwrtSnapshotChanged.connect(lambda snapshot: self.states.append(snapshot.connection_state))
        self.secret = "  Synthetic-Password-Only!  "

    def tearDown(self):
        self.service.task_controller.finish()
        self.page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.service.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def start_login(self, fingerprint=""):
        self.assertTrue(self.service.connect_openwrt_async(self.settings, fingerprint))
        self.operations[-1](None)
        return self.service.openwrt_backend.connect.call_args

    def test_password_without_remember_survives_start_notifications(self):
        self.page.connection.password.setText(self.secret)
        sent = self.start_login()
        self.assertEqual(sent.args[1], self.secret)
        self.assertEqual(self.page.connection.password.text(), self.secret)
        self.assertTrue(self.states)
        self.assertTrue(all(state == OpenWrtConnectionState.CONNECTING for state in self.states))
        self.assertFalse(self.page.connection.remember_password.isChecked())
        self.assertNotIn("password", self.page.collect_settings())

    def test_failed_login_clears_secret_and_retry_uses_new_input(self):
        self.page.connection.password.setText("wrong-synthetic-password")
        self.start_login()
        self.service._on_openwrt_worker_failed("rejected", "")
        self.assertEqual(self.page.connection.password.text(), "")
        self.assertEqual(self.service.openwrt_password(), "")
        self.page.connection.password.setText(self.secret)
        self.states.clear()
        sent = self.start_login()
        self.assertEqual(sent.args[1], self.secret)
        self.assertTrue(all(state == OpenWrtConnectionState.CONNECTING for state in self.states))

    def test_password_survives_fingerprint_confirmation_reconnect(self):
        for status in ("fingerprint_required", "fingerprint_changed"):
            with self.subTest(status=status):
                self.page.connection.password.setText(self.secret)
                self.start_login()
                result = OpenWrtConnectionResult(status=status, fingerprint="SHA256:synthetic")
                self.service._on_openwrt_connect_succeeded(result)
                self.assertEqual(self.service.openwrt_password(), self.secret)
                sent = self.start_login(result.fingerprint)
                self.assertEqual(sent.args[1], self.secret)
                self.assertEqual(sent.kwargs["expected_fingerprint"], result.fingerprint)
                self.service.task_controller.finish()

    def test_rejecting_fingerprint_still_clears_secret(self):
        self.page.connection.password.setText(self.secret)
        self.start_login()
        self.service._on_openwrt_connect_succeeded(OpenWrtConnectionResult(status="fingerprint_required"))
        self.service.reject_openwrt_fingerprint()
        self.assertEqual(self.service.openwrt_password(), "")
        self.assertEqual(self.page.connection.password.text(), "")

    def test_remembered_password_does_not_reload_at_task_start(self):
        with patch.object(self.service, "save_openwrt_password_preference"), patch.object(
            self.service, "load_saved_openwrt_password", return_value=self.secret
        ) as reader:
            self.page.connection.password.setText(self.secret)
            self.page.connection.remember_password.setChecked(True)
            self.service.openwrt_backend.snapshot = replace(
                self.service.openwrt_backend.snapshot, connection_state=OpenWrtConnectionState.FAILED)
            reader.reset_mock()
            sent = self.start_login()
            self.assertEqual(sent.args[1], self.secret)
            reader.assert_not_called()

    def test_private_key_login_keeps_key_settings_and_empty_password(self):
        self.page.connection.auth_method.setCurrentIndex(
            self.page.connection.auth_method.findData("private_key"))
        self.settings.update(auth_method="private_key", private_key_path="synthetic-key")
        sent = self.start_login()
        self.assertEqual(sent.args[1], "")
        self.assertEqual(sent.args[0]["private_key_path"], "synthetic-key")


if __name__ == "__main__":
    unittest.main()
