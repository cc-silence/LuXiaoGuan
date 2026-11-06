"""Real loopback cancellation during SSH banner and password waits."""
import errno
import socket
from threading import Event, Thread, enumerate as threads
import time
import unittest
from unittest.mock import Mock, patch
import paramiko
from tests.test_ssh_empty_password import KEY, router
from modules.capture_services.openwrt.ssh_client import OpenWrtSshClient
from modules.capture_services.openwrt.ssh_connect_control import _connect_socket


class LoginNetworkCancelTests(unittest.TestCase):
    def stalled_login(self, stage):
        entered, stop, cancel = Event(), Event(), Event()
        errors, elapsed, transports = [], [], []
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0)); listener.listen(1); listener.settimeout(3)
        client = OpenWrtSshClient()
        client.configure("127.0.0.1", listener.getsockname()[1], "root")

        class Server(paramiko.ServerInterface):
            def check_auth_password(self, username, password):
                entered.set(); stop.wait(5)
                return paramiko.AUTH_SUCCESSFUL

        def serve():
            connection = None
            try:
                connection, _ = listener.accept()
                if stage == "banner":
                    entered.set(); stop.wait(5)
                else:
                    transport = paramiko.Transport(connection)
                    transports.append(transport)
                    transport.add_server_key(KEY)
                    transport.start_server(server=Server())
                    stop.wait(5)
            except (OSError, EOFError, paramiko.SSHException):
                pass
            finally:
                for transport in transports:
                    transport.close()
                if connection is not None:
                    connection.close()

        def login():
            try:
                client.connect("valid", client.fingerprint_for_key(KEY), timeout=5, cancel_event=cancel)
            except Exception as exc:
                errors.append(exc)

        server = Thread(target=serve, daemon=True)
        worker = Thread(target=login, daemon=True)
        server.start(); worker.start()
        try:
            self.assertTrue(entered.wait(3), stage)
            started = time.monotonic(); cancel.set()
            worker.join(1.5)
            elapsed.append(time.monotonic() - started)
            self.assertFalse(worker.is_alive(), stage)
            self.assertEqual(len(errors), 1)
            self.assertIsInstance(errors[0], InterruptedError)
            self.assertFalse(client.connected)
            self.assertIsNone(client._connecting_client)
            self.assertEqual(client.login_diagnostics.snapshot()[-1]["status"], "cancelled")
            self.assertFalse(any(t.name == "RouterLoginCancel" for t in threads()))
            print(stage + " cancel_seconds=" + str(round(elapsed[0], 3)))
        finally:
            cancel.set(); stop.set(); client.close(); listener.close()
            worker.join(3); server.join(3)

    def test_stalled_banner_cancels_without_full_timeout(self):
        self.stalled_login("banner")

    def test_stalled_authentication_cancels_without_full_timeout(self):
        self.stalled_login("authentication")

    def test_successful_login_with_cancellation_support(self):
        with router("normal") as (client, server):
            client.connect("valid", client.fingerprint_for_key(KEY), timeout=3, cancel_event=Event())
            self.assertTrue(client.connected)
            self.assertEqual(server.attempts, [("password", "root", "valid")])

    def test_tcp_wait_is_cancelled_and_socket_closed(self):
        cancel, entered = Event(), Event()
        sock = Mock(); sock.connect_ex.return_value = errno.EINPROGRESS
        def wait(*args):
            entered.set(); cancel.set()
            return [], [], []
        with patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 22))]), patch("socket.socket", return_value=sock), patch("select.select", side_effect=wait):
            with self.assertRaises(InterruptedError):
                _connect_socket("127.0.0.1", 22, 15, cancel)
        self.assertTrue(entered.is_set())
        sock.close.assert_called()

    def test_cancelled_before_start_does_not_resolve_or_connect(self):
        cancel = Event(); cancel.set()
        with patch("socket.getaddrinfo") as resolve:
            with self.assertRaises(InterruptedError):
                _connect_socket("example.invalid", 22, 15, cancel)
            resolve.assert_not_called()


class LoginOperationCancelTests(unittest.TestCase):
    def test_cancel_before_connect_disconnects_in_worker_and_returns_cancelled(self):
        from modules.capture_services.login_operation import run_login
        backend = Mock(); cancel = Event(); cancel.set()
        result = run_login(backend, {}, "", "", cancel, None)
        self.assertEqual(result.status, "cancelled")
        backend.connect.assert_not_called()
        backend.disconnect.assert_called_once()

    def test_cancel_after_connect_does_not_publish_success(self):
        from modules.capture_services.login_operation import run_login
        backend = Mock(); cancel = Event()
        def connect(*args, **kwargs):
            cancel.set()
            return Mock(status="connected")
        backend.connect.side_effect = connect
        result = run_login(backend, {}, "", "", cancel, None)
        self.assertEqual(result.status, "cancelled")
        backend.disconnect.assert_called_once()

    def test_recovery_failure_is_not_hidden_as_cancellation(self):
        from modules.capture_services.login_operation import run_login
        backend = Mock(); cancel = Event()
        def connect(*args, **kwargs):
            cancel.set()
            raise RuntimeError("Recovery not verified")
        backend.connect.side_effect = connect
        with self.assertRaisesRegex(RuntimeError, "Recovery not verified"):
            run_login(backend, {}, "", "", cancel, None)
