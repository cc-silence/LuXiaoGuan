"""Actual Paramiko SFTP v3 packets over local sockets; files stay in memory."""
import errno
import posixpath
import socket
import stat
import threading
import unittest
from contextlib import nullcontext
from types import SimpleNamespace as NS
from unittest.mock import Mock
import paramiko
from tests.test_router_file_delete import MemorySFTP, attrs
from modules.capture_services.openwrt.router_management import RouterManagement


class FixtureAuth(paramiko.ServerInterface):
    def check_auth_none(self, username):
        return paramiko.AUTH_SUCCESSFUL

    def check_channel_request(self, kind, channel_id):
        return paramiko.OPEN_SUCCEEDED if kind == 'session' else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED


class MemoryProtocol(paramiko.SFTPServerInterface):
    def __init__(self, server, *, fs):
        super().__init__(server)
        self.fs = fs

    def lstat(self, path):
        try:
            value = self.fs.lstat(path)
            result = paramiko.SFTPAttributes()
            for name, field in vars(value).items():
                setattr(result, name, field)
            result.st_atime = result.st_mtime
            return result
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno or errno.EIO)

    def list_folder(self, path):
        try:
            values = []
            for entry in self.fs.listdir_iter(path):
                value = self.lstat(posixpath.join(path, entry.filename))
                if isinstance(value, int):
                    return value
                value.filename = entry.filename
                values.append(value)
            return values
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno or errno.EIO)

    def remove(self, path):
        try:
            self.fs.remove(path)
            return paramiko.SFTP_OK
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno or errno.EIO)

    def rmdir(self, path):
        try:
            self.fs.rmdir(path)
            return paramiko.SFTP_OK
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno or errno.EIO)


class RemoteDeletionProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = paramiko.RSAKey.generate(2048)

    def setUp(self):
        self.fs = MemorySFTP()
        self.cancel = threading.Event()
        client_socket, server_socket = socket.socketpair()
        client_socket.settimeout(5)
        server_socket.settimeout(5)
        self.server = paramiko.Transport(server_socket)
        self.client = paramiko.Transport(client_socket)
        self.addCleanup(self.close)
        self.server.add_server_key(self.key)
        self.server.set_subsystem_handler('sftp', paramiko.SFTPServer, MemoryProtocol, fs=self.fs)
        self.errors = []
        def start():
            try:
                self.server.start_server(server=FixtureAuth())
            except Exception as exc:
                self.errors.append(exc)
        self.handshake = threading.Thread(target=start, daemon=True)
        self.handshake.start()
        self.client.start_client(timeout=5)
        self.assertEqual(self.client.get_remote_server_key(), self.key)
        self.client.auth_none('in-memory-fixture')
        channel = self.client.open_session(timeout=5)
        channel.settimeout(5)
        channel.invoke_subsystem('sftp')
        self.sftp = paramiko.SFTPClient(channel)
        self.ssh = NS(connected=True, _client=self.client, host_fingerprint='pinned-fixture',
                      transfer=lambda *a, **k: nullcontext(), open_sftp=lambda **k: self.sftp,
                      drop_sftp=Mock(side_effect=self.sftp.close))
        self.manager = RouterManagement(self.ssh, Mock())

    def close(self):
        if hasattr(self, 'sftp'):
            self.sftp.close()
        self.client.close()
        self.server.close()
        if hasattr(self, 'handshake'):
            self.handshake.join(timeout=3)
            self.assertFalse(self.handshake.is_alive())
        self.client.join(timeout=3)
        self.server.join(timeout=3)
        self.assertFalse(self.client.is_alive())
        self.assertFalse(self.server.is_alive())

    def test_multiple_readdir_pages_and_unicode_nested_entries_delete_correctly(self):
        for index in range(75):
            self.fs.nodes['/root/target/sub/file %02d' % index] = attrs(stat.S_IFREG | 0o644)
        self.fs.nodes['/root/target/sub/\u6d4b\u8bd5.txt'] = attrs(stat.S_IFREG | 0o644)
        expected = self.manager.identity(self.sftp.lstat('/root/target'))
        result = self.manager.mutate('delete', '/root/target', self.cancel, expected=expected)
        self.assertEqual(result, '/root/target')
        self.assertEqual(len(self.fs.deleted), 80)
        self.assertIn('/outside/keep', self.fs.nodes)
        self.assertNotIn('/root/target/link', self.fs.listed)
        self.assertFalse(self.errors)

    def test_cancellation_releases_actual_sftp_channel_after_one_confirmed_unlink(self):
        self.fs.on_delete = lambda path: self.cancel.set()
        expected = self.manager.identity(self.sftp.lstat('/root/target'))
        with self.assertRaises(InterruptedError):
            self.manager.mutate('delete', '/root/target', self.cancel, expected=expected)
        self.assertEqual(len(self.fs.deleted), 1)
        self.assertIn('/root/target', self.fs.nodes)
        self.ssh.drop_sftp.assert_called_once()
        self.assertTrue(self.sftp.get_channel().closed)
        self.assertIn('/outside/keep', self.fs.nodes)


if __name__ == '__main__':
    unittest.main()
