"""Secure-storage contracts using synthetic credentials and an in-memory Secret Service."""
from contextlib import contextmanager
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace as NS, ModuleType
import unittest
from unittest.mock import Mock, patch
from modules.platform_services import credentials, linux_credentials as linux, credential_worker as worker
from scripts import linux_runtime_check

SECRET = "synthetic-not-a-real-password"
TARGET = "RouterManager:OpenWrt:192.0.2.5:22:root"


def request(operation="read", target=TARGET, password=""):
    return dict(operation=operation, target=target, username="root", password=password)


@contextmanager
def secret_service(locked=False, encrypted=True):
    items = []
    collection = NS(collection_path="/org/freedesktop/secrets/collection/login",
                    is_locked=Mock(return_value=locked), unlock=Mock())
    def item(target, secret, application="RouterManager"):
        result = NS()
        result.attributes = dict(application=application, target=target)
        result.get_attributes = Mock(side_effect=lambda: dict(result.attributes))
        result.is_locked = Mock(return_value=False)
        result.get_secret = Mock(return_value=secret)
        result.delete = Mock(side_effect=lambda: items.remove(result))
        items.append(result)
        return result
    def search(attributes):
        return iter([value for value in items if all(value.attributes.get(k) == v for k, v in attributes.items())])
    def create(label, attributes, secret, replace, content_type):
        if replace:
            items[:] = [value for value in items if value.attributes != attributes]
        return item(attributes["target"], secret)
    collection.search_items = Mock(side_effect=search)
    collection.create_item = Mock(side_effect=create)
    connection = NS(close=Mock())
    storage, collections, util = ModuleType("secretstorage"), ModuleType("secretstorage.collection"), ModuleType("secretstorage.util")
    storage.dbus_init = Mock(return_value=connection)
    collections.get_collection_by_alias = Mock(return_value=collection)
    collections.SESSION_COLLECTION = "/org/freedesktop/secrets/collection/session"
    util.open_session = Mock(return_value=NS(encrypted=encrypted))
    with patch.dict(sys.modules, {"secretstorage": storage, "secretstorage.collection": collections,
                                 "secretstorage.util": util}):
        yield NS(items=items, item=item, collection=collection, connection=connection,
                 storage=storage, collections=collections, util=util)


class LinuxCredentialBackendTests(unittest.TestCase):
    def test_read_write_replace_and_exact_target_delete(self):
        with secret_service() as store:
            other = store.item(TARGET + "-other", b"other")
            self.assertEqual(linux.perform(request()), "")
            self.assertTrue(linux.perform(request("write", password=SECRET)))
            self.assertEqual(linux.perform(request()), SECRET)
            self.assertTrue(linux.perform(request("write", password="replacement")))
            self.assertEqual(linux.perform(request()), "replacement")
            self.assertEqual(len(store.items), 2)
            self.assertTrue(linux.perform(request("delete")))
            self.assertEqual(linux.perform(request()), "")
            self.assertTrue(linux.perform(request("delete")))
            self.assertIn(other, store.items)
            store.collection.unlock.assert_not_called()
            self.assertEqual(store.connection.close.call_count, 8)

    def test_missing_dependency_has_no_file_storage_fallback(self):
        with patch.dict(sys.modules, {"secretstorage": None}):
            with self.assertRaises(credentials.CredentialError) as caught:
                linux.perform(request("write", password=SECRET))
        self.assertEqual(caught.exception.code, "missing_dependency")

    def test_locked_and_unencrypted_keyrings_never_receive_secret(self):
        for locked, encrypted, code in ((True, True, "locked"), (False, False, "unencrypted")):
            with self.subTest(code=code), secret_service(locked, encrypted) as store:
                with self.assertRaises(credentials.CredentialError) as caught:
                    linux.perform(request("write", password=SECRET))
                self.assertEqual(caught.exception.code, code)
                store.collection.create_item.assert_not_called()
                store.collection.unlock.assert_not_called()
                store.connection.close.assert_called_once()

    def test_no_session_collection_or_raw_exception_fallback(self):
        with secret_service() as store:
            store.collection.collection_path = store.collections.SESSION_COLLECTION
            with self.assertRaises(credentials.CredentialError):
                linux.perform(request("write", password=SECRET))
            store.collection.create_item.assert_not_called()
        with secret_service() as store:
            store.collections.get_collection_by_alias.side_effect = RuntimeError(SECRET)
            with self.assertRaises(credentials.CredentialError) as caught:
                linux.perform(request())
            self.assertNotIn(SECRET, str(caught.exception))
            store.connection.close.assert_called_once()

    def test_duplicate_or_foreign_search_result_is_not_read_or_overwritten(self):
        with secret_service() as store:
            first = store.item(TARGET, b"one")
            store.item(TARGET, b"two")
            for operation in ("read", "write"):
                with self.assertRaises(credentials.CredentialError) as caught:
                    linux.perform(request(operation, password=SECRET if operation == "write" else ""))
                self.assertEqual(caught.exception.code, "ambiguous")
            first.get_secret.assert_not_called()
            store.collection.create_item.assert_not_called()
        with secret_service() as store:
            foreign = store.item(TARGET, b"foreign", application="another-app")
            store.collection.search_items.side_effect = lambda attributes: iter([foreign])
            with self.assertRaises(credentials.CredentialError):
                linux.perform(request("delete"))
            foreign.delete.assert_not_called()

    def test_write_and_delete_must_verify_persistent_state(self):
        with secret_service() as store:
            store.collection.create_item.side_effect = lambda *args, **kwargs: None
            with self.assertRaises(credentials.CredentialError) as caught:
                linux.perform(request("write", password=SECRET))
            self.assertEqual(caught.exception.code, "verification")
        with secret_service() as store:
            value = store.item(TARGET, b"one")
            value.delete.side_effect = None
            with self.assertRaises(credentials.CredentialError):
                linux.perform(request("delete"))


class CredentialTransportTests(unittest.TestCase):
    def test_windows_adapter_retains_original_backend_calls(self):
        with patch.object(credentials, "is_windows", return_value=True), \
                patch("modules.platform_services.windows_credentials.read_generic_password", return_value=SECRET) as read, \
                patch("modules.platform_services.windows_credentials.write_generic_password", return_value=True) as write, \
                patch("modules.platform_services.windows_credentials.delete_generic_password", return_value=True) as delete:
            self.assertEqual(credentials.read_generic_password(TARGET), SECRET)
            self.assertTrue(credentials.write_generic_password(TARGET, "root", SECRET))
            self.assertTrue(credentials.delete_generic_password(TARGET))
            read.assert_called_once_with(TARGET)
            write.assert_called_once_with(TARGET, "root", SECRET)
            delete.assert_called_once_with(TARGET)

    def test_secrets_only_travel_on_stdin_and_worker_has_timeout(self):
        reply = subprocess.CompletedProcess([], 0, b'{"ok":true,"value":true}')
        with patch.dict(sys.modules, {"PySide6.QtCore": None}), \
                patch.object(credentials.subprocess, "run", return_value=reply) as run:
            self.assertTrue(credentials._linux_request("write", TARGET, "root", SECRET))
        self.assertNotIn(SECRET, repr(run.call_args.args))
        self.assertIn(SECRET.encode(), run.call_args.kwargs["input"])
        self.assertEqual(run.call_args.kwargs["timeout"], 10)
        self.assertEqual(run.call_args.kwargs["stderr"], subprocess.DEVNULL)

    def test_timeout_and_invalid_reply_never_expose_secret(self):
        with patch.dict(sys.modules, {"PySide6.QtCore": None}):
            for outcome in (
                    subprocess.TimeoutExpired("worker", 10, output=SECRET.encode()),
                    subprocess.CompletedProcess([], 1, SECRET.encode()),
                    subprocess.CompletedProcess([], 1, b'{"ok":false,"code":"unknown"}')):
                with self.subTest(kind=type(outcome).__name__):
                    with patch.object(credentials.subprocess, "run") as run:
                        if isinstance(outcome, Exception):
                            run.side_effect = outcome
                        else:
                            run.return_value = outcome
                        with self.assertRaises(credentials.CredentialError) as caught:
                            credentials._linux_request("read", TARGET)
                    self.assertNotIn(SECRET, str(caught.exception))

    def test_gui_thread_is_rejected_before_starting_a_blocking_call(self):
        thread = object()
        qt = NS(QCoreApplication=NS(instance=lambda: NS(thread=lambda: thread)),
                QThread=NS(currentThread=lambda: thread))
        with patch.dict(sys.modules, {"PySide6.QtCore": qt}), patch.object(credentials.subprocess, "run") as run:
            with self.assertRaises(credentials.CredentialError) as caught:
                credentials._linux_request("read", TARGET)
            self.assertEqual(caught.exception.code, "background_required")
            run.assert_not_called()

    def test_worker_protocol_and_error_redaction(self):
        for payload in (request("write", password=SECRET), request()):
            output = io.StringIO()
            with patch.object(worker, "is_linux", return_value=True), \
                    patch.object(linux, "perform", return_value=True if payload["operation"] == "write" else SECRET):
                result = worker.run_worker(["main.py", "--credential-worker"],
                                           io.BytesIO(json.dumps(payload).encode()), output)
            self.assertEqual(result, 0)
            self.assertTrue(json.loads(output.getvalue())["ok"])
        output = io.StringIO()
        with patch.object(worker, "is_linux", return_value=True), \
                patch.object(linux, "perform", side_effect=RuntimeError(SECRET)):
            result = worker.run_worker(["main.py", "--credential-worker"],
                                       io.BytesIO(json.dumps(request()).encode()), output)
        self.assertEqual(result, 1)
        self.assertNotIn(SECRET, output.getvalue())
        self.assertEqual(json.loads(output.getvalue())["code"], "unavailable")

    def test_main_worker_dispatch_stays_gui_free_with_private_stdin(self):
        code = """
import sys
from modules.platform_services import credential_worker, linux_credentials
credential_worker.is_linux = lambda: True
linux_credentials.perform = lambda payload: True
import main
sys.argv = ['main.py', '--credential-worker']
result = main.main()
assert 'router_app.bootstrap' not in sys.modules
assert 'PySide6.QtWidgets' not in sys.modules
raise SystemExit(result)
"""
        result = subprocess.run([sys.executable, "-c", code],
                                input=json.dumps(request("write", password=SECRET)).encode(),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10,
                                cwd=Path(__file__).resolve().parents[1])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), {"ok": True, "value": True})
        self.assertNotIn(SECRET.encode(), result.stderr)

    def test_oversized_worker_request_is_rejected_before_backend(self):
        output = io.StringIO()
        with patch.object(worker, "is_linux", return_value=True), patch.object(linux, "perform") as perform:
            self.assertEqual(worker.run_worker(["main.py", "--credential-worker"],
                             io.BytesIO(b"x" * (credentials.MAX_BYTES + 1)), output), 1)
            perform.assert_not_called()

    def test_linux_dependency_profile_and_recursive_include_rejection(self):
        root = Path(__file__).resolve().parents[1]
        pins = linux_runtime_check.pinned_requirements(root / "requirements-linux.txt")
        self.assertEqual(pins["SecretStorage"], "3.3.3")
        self.assertEqual(pins["jeepney"], "0.9.0")
        self.assertEqual(pins["PySide6"], "6.3.2")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "loop.txt"
            path.write_text("-r loop.txt\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                linux_runtime_check.pinned_requirements(path)


if __name__ == "__main__":
    unittest.main()
