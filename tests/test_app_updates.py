"""Offline regression tests for signed manifests, real asynchronous HTTP and cancellation."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import base64
import json
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QWidget
from modules.app_update_services.manifest import canonical, verify_manifest, version_key, https_url
from modules.app_update_services.checker import UpdateChecker
from modules.network_policy import NetworkChannel, CHANNEL_DIRECT

APP = QApplication.instance() or QApplication([])
KEY = Ed25519PrivateKey.generate()
PUBLIC = base64.b64encode(KEY.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
PAYLOAD = dict(schema=1, product="RouterManager", platform="win-x64", version="2.0.9_alpha",
               notes="Release notes <script>must stay plain text</script>",
               url="https://updates.example.com/files/package.zip", size=123,
               sha256="a" * 64, published_at="2026-10-19T12:00:00+08:00")


def signed(payload=None):
    payload = dict(PAYLOAD if payload is None else payload)
    return canonical(dict(payload=payload, signature=base64.b64encode(KEY.sign(canonical(payload))).decode()))


class ManifestTests(unittest.TestCase):
    def test_signed_and_tampered_manifest(self):
        self.assertEqual(verify_manifest(signed(), PUBLIC).version, "2.0.9_alpha")
        envelope = json.loads(signed())
        envelope["payload"]["url"] = "https://attacker.example/package.zip"
        with self.assertRaises(Exception):
            verify_manifest(canonical(envelope), PUBLIC)

    def test_rejects_untrusted_key_and_unsigned_envelope(self):
        with self.assertRaises(Exception):
            verify_manifest(signed(), base64.b64encode(bytes(32)).decode())
        with self.assertRaises(ValueError):
            verify_manifest(canonical(PAYLOAD), PUBLIC)

    def test_version_order_numeric_and_prerelease(self):
        versions = ["2.0.7_alpha", "2.0.7_beta", "2.0.7_rc1", "2.0.7", "2.0.8_alpha", "2.0.10_alpha", "3.0"]
        self.assertEqual(sorted(versions, key=version_key), versions)
        self.assertLess(version_key("2.0.7_beta2"), version_key("2.0.7_beta10"))
        for invalid in ("", "abc", "2.0.7_bad", None, "2.0.7\n"):
            with self.assertRaises(ValueError):
                version_key(invalid)

    def test_rejects_unsafe_urls_and_oversize_metadata(self):
        for url in ("http://example.com/file", "file:///test", "https://u:p@example.com/a",
                    "https://example.com:444/a", "https://example.com/a\n", "https://example.com/a#fragment"):
            with self.assertRaises(ValueError):
                https_url(url)
        for field, value in (("size", True), ("size", -1), ("sha256", "bad"),
                             ("notes", "a" * 16001), ("platform", "linux")):
            payload = dict(PAYLOAD, **{field: value})
            with self.assertRaises(ValueError):
                verify_manifest(signed(payload), PUBLIC)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    def log_message(self, *_args):
        pass

    def do_GET(self):
        if self.path == "/stall":
            time.sleep(0.4)
        body = b"x" * 70000 if self.path == "/oversize" else signed()
        if self.path == "/bad":
            body = b'{"payload":{},"signature":"invalid"}'
        self.send_response(503 if self.path == "/denied" else 200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


class NetworkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = Path(self.temp.name) / "channel.json"
        self.checker = UpdateChecker("2.0.8_alpha", self.config, public_key=PUBLIC)
        self.checker.test_code="fixture-code"
        self.routes = patch("modules.network_policy.allowed_channels",
                            side_effect=lambda *_args: [NetworkChannel(CHANNEL_DIRECT, "fixture")])
        self.urls = patch("modules.app_update_services.checker.https_url", side_effect=lambda value: value)
        self.routes.start()
        self.urls.start()

    def tearDown(self):
        self.checker.cancel()
        self.checker.deleteLater()
        APP.processEvents()
        self.routes.stop()
        self.urls.stop()
        self.temp.cleanup()

    def run_check(self, path="/manifest", cancel=False, timeout_ms=2500):
        self.config.write_text(json.dumps(dict(schema=1, enabled=True,
            manifest_url=f"http://127.0.0.1:{self.server.server_port}" + path)), encoding="utf-8")
        loop = QEventLoop()
        result = []
        self.checker.completed.connect(lambda release, manual: (result.append(("ok", release, manual)), loop.quit()))
        self.checker.failed.connect(lambda message, manual: (result.append(("failed", message, manual)), loop.quit()))
        deadline = QTimer()
        deadline.setSingleShot(True)
        deadline.timeout.connect(loop.quit)
        deadline.start(timeout_ms)
        self.assertTrue(self.checker.check(manual=True))
        self.assertFalse(self.checker.check(manual=True))
        if cancel:
            self.checker.cancel()
            deadline.start(80)
        loop.exec()
        deadline.stop()
        self.assertFalse(self.checker.active)
        self.assertEqual(len(self.checker._data), 0)
        return result

    def test_real_http_new_version_and_repeat_check(self):
        result = self.run_check()
        self.assertEqual(result[0][0], "ok")
        self.assertEqual(result[0][1].version, PAYLOAD["version"])
        self.assertTrue(result[0][2])

    def test_same_or_older_version_no_prompt(self):
        self.checker.current_version = "2.0.9_alpha"
        self.assertIsNone(self.run_check()[0][1])

    def test_invalid_signature_and_http_denial(self):
        for path in ("/bad", "/denied"):
            with self.subTest(path=path):
                self.assertEqual(self.run_check(path)[-1][0], "failed")

    def test_size_limit_and_timeout(self):
        self.assertEqual(self.run_check("/oversize")[-1][0], "failed")
        self.checker.TIMEOUT_MS = 40
        self.assertEqual(self.run_check("/stall")[-1][0], "failed")

    def test_cancel_releases_network_and_suppresses_result(self):
        self.assertEqual(self.run_check("/stall", cancel=True), [])

    def test_disabled_configuration_is_explicit_for_manual_check(self):
        self.config.write_text('{"schema":1,"enabled":false}', encoding="utf-8")
        results = []
        self.checker.failed.connect(lambda text, manual: results.append((text, manual)))
        self.assertFalse(self.checker.check(manual=True))
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0][1])


class PresenterTests(unittest.TestCase):
    def test_dialog_plain_text_theme_and_release(self):
        from PySide6.QtCore import QObject
        from modules.desktop_shell.app_update_presenter import AppUpdatePresenter
        from configparser import ConfigParser
        runtime = QObject()
        runtime.main_window = QWidget()
        runtime.main_window.show()
        runtime._exiting = False
        runtime.config = ConfigParser()
        presenter = AppUpdatePresenter("2.0.8_alpha", runtime)
        for theme in ("dark.qss", "light.qss"):
            with self.subTest(theme=theme):
                from modules.dialog_theme import build_base_stylesheet
                from modules.config_manager import ConfigManager
                presenter._release = verify_manifest(signed(), PUBLIC)
                stylesheet = build_base_stylesheet(ConfigManager(), theme_name=theme)
                with patch("modules.common_widgets.common_dialogs.build_current_base_stylesheet", return_value=stylesheet):
                    presenter._present_if_visible(force=True)
                self.assertIsNotNone(presenter.dialog)
                dialog = presenter.dialog
                self.assertEqual(dialog.styleSheet(), stylesheet)
                from PySide6.QtWidgets import QTextEdit
                self.assertEqual(dialog.findChild(QTextEdit).toPlainText(), PAYLOAD["notes"])
                dialog.reject()
                APP.processEvents()
                self.assertIsNone(presenter.dialog)
        presenter.stop()
        runtime.main_window.close()
        runtime.deleteLater()


if __name__ == "__main__":
    unittest.main()
