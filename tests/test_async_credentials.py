"""Synthetic subprocess and UI regressions for Linux credential integration."""
import json
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
import time
import unittest
from unittest.mock import Mock, patch
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QSignalBlocker
from PySide6.QtWidgets import QApplication
from modules.platform_services.credential_controller import CredentialController
from modules.platform_services.credentials import CredentialError
from modules.capture_services.capture_service import CaptureService
from modules.main_pages.packet_capture.openwrt.router_login_page import RouterLoginPage

TARGET = "RouterManager:OpenWrt:192.0.2.1:22:root"


class ControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def wait_idle(self, controller):
        end = time.monotonic() + 4
        while controller.active and time.monotonic() < end:
            self.app.processEvents()
            time.sleep(.002)
        self.assertFalse(controller.active)

    def controller(self, script, timeout_ms=2000):
        controller = CredentialController(command=lambda: [sys.executable, "-c", script],
                                          timeout_ms=timeout_ms)
        self.addCleanup(controller.deleteLater)
        return controller

    def test_real_pipe_preserves_password_and_argv_is_secret_free(self):
        import hashlib
        from uuid import uuid4
        secret = "synthetic-" + uuid4().hex
        digest = hashlib.sha256(secret.encode()).hexdigest()
        script = ("import json,hashlib,sys;p=json.load(sys.stdin);"
                  "print(json.dumps(dict(ok=True,value=hashlib.sha256(p['password'].encode()).hexdigest()==" + repr(digest) + ")))")
        controller = self.controller(script)
        values = []
        controller.submit("write", TARGET, "root", secret, controller,
                          lambda value, error: values.append((value, error)))
        self.assertNotIn(secret, str(controller.process.arguments()))
        self.wait_idle(controller)
        self.assertEqual(values, [(True, None)])
        self.assertEqual(controller._output, bytearray())

    def test_failed_start_and_storage_failure_are_structured(self):
        for command in (
            lambda: ["nonexistent-routermanager-credential-helper"],
            lambda: [sys.executable, "-c", "print('{\"ok\":false,\"code\":\"locked\"}')"],
        ):
            controller = CredentialController(command=command)
            values = []
            controller.submit("read", TARGET, "", "", controller,
                              lambda value, error: values.append(error.code))
            self.wait_idle(controller)
            self.assertEqual(len(values), 1)
            self.assertIn(values[0], {"unavailable", "locked"})
            controller.deleteLater()

    def test_timeout_reaps_process_and_drops_secret(self):
        controller = self.controller("import time;time.sleep(60)", timeout_ms=30)
        errors = []
        controller.submit("write", TARGET, "root", "synthetic", controller,
                          lambda value, error: errors.append(error.code))
        self.wait_idle(controller)
        self.assertEqual(errors, ["timeout"])
        self.assertIsNone(controller._current)

    def test_shutdown_clears_queue_waits_for_process_and_suppresses_callbacks(self):
        controller = self.controller("import time;time.sleep(60)")
        callback = Mock()
        for _ in range(9):
            controller.submit("write", TARGET, "root", "synthetic", controller, callback)
        with self.assertRaises(CredentialError):
            controller.submit("write", TARGET, "root", "synthetic", controller, callback)
        controller.shutdown()
        self.wait_idle(controller)
        callback.assert_not_called()
        self.assertFalse(controller._queue)
        with self.assertRaises(CredentialError):
            controller.submit("read", TARGET, "", "", controller, callback)

    def test_destroyed_context_never_receives_result(self):
        controller = self.controller("print('{\"ok\":true,\"value\":\"synthetic\"}')")
        context, callback = QObject(), Mock()
        controller.submit("read", TARGET, "", "", context, callback)
        context.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.wait_idle(controller)
        callback.assert_not_called()

    def test_oversized_output_is_rejected(self):
        controller = self.controller("print('x'*70000)")
        errors = []
        controller.submit("read", TARGET, "", "", controller,
                          lambda value, error: errors.append(error.code))
        self.wait_idle(controller)
        self.assertEqual(errors, ["verification"])


class LoginIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.platform = patch("modules.main_pages.packet_capture.openwrt.login_credentials.is_linux", return_value=True)
        self.platform.start()
        self.service = CaptureService()
        self.service._start_openwrt_worker = Mock()
        self.jobs = []
        self.service.credentials.submit = lambda *args: self.jobs.append(args)
        self.page = RouterLoginPage(self.service, dict(host="192.0.2.1", port=22,
                                   user="root", remember_password=False, auth_method="password"))
        self.ui = self.page._credential_ui
        self.page.show_storage_feedback = Mock()
        self.page.info_panel = Mock()

    def tearDown(self):
        self.platform.stop()
        self.page.deleteLater()
        self.service.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def finish(self, index=-1, value=True, error=None):
        self.jobs[index][-1](value, error)

    def test_delayed_read_does_not_replace_new_input(self):
        self.page.connection.remember_password.setChecked(True)
        self.assertEqual(self.jobs[-1][0], "read")
        self.page.connection.password.setText("new synthetic input")
        self.finish(value="old synthetic")
        self.assertEqual(self.page.connection.password.text(), "new synthetic input")

    def test_delayed_read_does_not_cross_router_identity(self):
        self.page.connection.remember_password.setChecked(True)
        self.page.connection.host.setText("192.0.2.2")
        self.page._reload_saved_password_for_identity()
        self.finish(0, value="other device secret")
        self.assertEqual(self.page.connection.password.text(), "")
        self.finish(1, value="correct synthetic")
        self.assertEqual(self.page.connection.password.text(), "correct synthetic")

    def test_failed_delete_restores_preference_preserves_input_without_toast(self):
        self.page.connection.password.setText("synthetic")
        self.page.connection.remember_password.setChecked(True)
        self.finish()
        self.page.connection.remember_password.setChecked(False)
        self.page.connection.password.setText("new synthetic")
        self.finish(error=CredentialError("locked"))
        self.assertTrue(self.page.connection.remember_password.isChecked())
        self.assertEqual(self.page.connection.password.text(), "new synthetic")
        self.page.show_storage_feedback.assert_not_called()

    def test_successful_delete_commits_only_after_verified_reply(self):
        self.page.connection.password.setText("synthetic")
        self.page.connection.remember_password.setChecked(True)
        self.finish()
        self.page.connection.remember_password.setChecked(False)
        self.assertTrue(self.page._remember_password_preference)
        self.finish()
        self.assertFalse(self.page._remember_password_preference)
        self.page.show_storage_feedback.assert_called_once()

    def test_login_waits_for_verified_save_and_rejects_edited_request(self):
        self.page.connection.password.setText("synthetic")
        with QSignalBlocker(self.page.connection.remember_password):
            self.page.connection.remember_password.setChecked(True)
        continuation = Mock()
        self.ui.before_login(self.page.collect_settings(), continuation)
        continuation.assert_not_called()
        self.page.connection.password.setText("edited")
        self.finish()
        continuation.assert_not_called()
        self.ui.before_login(self.page.collect_settings(), continuation)
        self.finish()
        continuation.assert_called_once()

    def test_login_click_during_read_does_not_invalidate_the_pending_read(self):
        self.page.connection.remember_password.setChecked(True)
        continuation = Mock()
        self.ui.before_login(self.page.collect_settings(), continuation)
        continuation.assert_not_called()
        self.finish(value="stored synthetic")
        self.assertEqual(self.page.connection.password.text(), "stored synthetic")
        self.assertTrue(self.page._remember_password_preference)

    def test_failed_save_never_connects(self):
        self.page.connection.password.setText("synthetic")
        with QSignalBlocker(self.page.connection.remember_password):
            self.page.connection.remember_password.setChecked(True)
        continuation = Mock()
        self.ui.before_login(self.page.collect_settings(), continuation)
        self.finish(error=CredentialError("timeout"))
        continuation.assert_not_called()
        self.page.info_panel.append_info.assert_called()

    def test_memory_only_login_does_not_require_a_keyring(self):
        continuation = Mock()
        self.ui.before_login(self.page.collect_settings(), continuation)
        continuation.assert_called_once()
        self.assertEqual(self.jobs, [])

    def test_empty_remembered_login_never_deletes_stored_password(self):
        with QSignalBlocker(self.page.connection.remember_password):
            self.page.connection.remember_password.setChecked(True)
        continuation = Mock()
        self.ui.before_login(self.page.collect_settings(), continuation)
        continuation.assert_not_called()
        self.assertEqual(self.jobs, [])


class PasswordHandoffTests(unittest.TestCase):
    def test_changed_password_is_copied_before_request_clear_and_scoped_to_original_target(self):
        from types import SimpleNamespace
        from modules.capture_services.openwrt.router_password import PasswordChange
        from modules.capture_services.router_password_actions import accept_password_result
        for current in (True, False):
            owner = Mock()
            owner._openwrt_credential_target.return_value = TARGET
            client = object()
            request = PasswordChange.create("synthetic-new-password",
                dict(host="192.0.2.1", port=22, user="root", remember_password=True))
            service = SimpleNamespace(owner=owner,
                manager=SimpleNamespace(ssh=SimpleNamespace(_client=client)),
                current=("job", "password_change", {"request": request}, "PIN",
                         client if current else object()), passwordChanged=Mock())
            with patch("modules.platform_services.runtime.is_linux", return_value=True):
                accept_password_result(service, dict(changed=True, note="verified remote"))
            request.clear()
            arguments = owner.credentials.submit.call_args.args
            self.assertEqual(arguments[:4], ("write", TARGET, "root", "synthetic-new-password"))
            self.assertEqual(request.secret, bytearray())
            if current:
                owner.set_openwrt_password.assert_called_once_with("synthetic-new-password")
            else:
                owner.set_openwrt_password.assert_not_called()
                service.passwordChanged.emit.assert_not_called()
            arguments[-1](None, CredentialError("locked"))
            self.assertEqual(owner.settingsMessageGenerated.emit.call_args.args[1], "error")
