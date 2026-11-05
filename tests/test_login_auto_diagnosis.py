"""Real local ports distinguish TCP refusal, SSH banner and incompatibility."""
from contextlib import contextmanager
import json
import socket
from threading import Event, Thread
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
import paramiko
from tests.test_ssh_empty_password import KEY, router
from modules.capture_services.openwrt.ssh_client import OpenWrtSshClient
from modules.capture_services.openwrt.ssh_login_probe import connection_message


@contextmanager
def endpoint(mode):
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0));listener.listen(1);listener.settimeout(3)
    port = listener.getsockname()[1]
    stop = Event();accepted = [];transports = [];errors = []
    def serve():
        try:
            connection, _ = listener.accept();accepted.append(connection)
            if mode == 'silent':
                stop.wait(3)
            elif mode == 'http':
                connection.sendall(b'HTTP/1.1 200 OK\r\n' * 100)
                stop.wait(3)
            elif mode == 'old_protocol':
                connection.sendall(b'SSH-1.5-legacy_private_banner\r\n')
                stop.wait(3)
            elif mode == 'hostkey_mismatch':
                transport = paramiko.Transport(connection);transports.append(transport)
                transport.add_server_key(KEY)
                # No installed server key can be offered in this configured family.
                transport.get_security_options().key_types = ('ssh-ed25519',)
                transport.start_server(server=paramiko.ServerInterface())
                stop.wait(3)
        except (OSError, EOFError, paramiko.SSHException) as error:
            errors.append(type(error).__name__)
        finally:
            for transport in transports:transport.close()
            for connection in accepted:connection.close()
    thread = Thread(target=serve, daemon=True);thread.start()
    client = OpenWrtSshClient();client.configure('127.0.0.1', port, 'root')
    try:
        yield client, accepted
    finally:
        client.close();stop.set();listener.close();thread.join(4)
        if thread.is_alive():raise AssertionError('Local diagnostic server leaked')


class LoginAutoDiagnosisTests(unittest.TestCase):
    def failed(self, client, timeout=.5):
        with self.assertRaises(Exception) as caught:
            client.connect('private-password', client.fingerprint_for_key(KEY), timeout=timeout)
        info = client.login_diagnostics.snapshot()[-1]
        raw = json.dumps(info)
        for secret in ('127.0.0.1', 'private-password', 'private_banner'):
            self.assertNotIn(secret, raw)
        return info, str(caught.exception)

    def test_closed_port_is_refused_before_ssh(self):
        listener = socket.socket();listener.bind(('127.0.0.1',0))
        port = listener.getsockname()[1];listener.close()
        client = OpenWrtSshClient();client.configure('127.0.0.1',port,'root')
        info, message = self.failed(client, timeout=3)
        self.assertEqual(info['reason'], 'connection_refused')
        self.assertEqual(info['stage'], 'tcp_connect')
        self.assertFalse(info['tcp_connected']);self.assertFalse(info['ssh_banner_received'])
        self.assertIn(str(port), message)

    def test_connected_silent_or_http_port_has_no_confirmed_ssh(self):
        for mode in ('silent', 'http'):
            with self.subTest(mode=mode), endpoint(mode) as (client, accepted):
                info, message = self.failed(client)
                self.assertEqual(info['stage'], 'ssh_banner')
                self.assertEqual(info['reason'], 'ssh_banner_unavailable')
                self.assertTrue(info['tcp_connected']);self.assertFalse(info['ssh_banner_received'])
                self.assertEqual(len(accepted), 1)
                self.assertNotIn('private-password', message)

    def test_unsupported_ssh_version_is_not_an_authentication_error(self):
        with endpoint('old_protocol') as (client, accepted):
            info, message = self.failed(client)
            self.assertEqual(info['reason'], 'protocol_version_mismatch')
            self.assertEqual(info['stage'], 'ssh_handshake')
            self.assertTrue(info['tcp_connected']);self.assertTrue(info['ssh_banner_received'])
            self.assertEqual(len(accepted), 1)

    def test_no_common_hostkey_algorithm_is_classified_separately(self):
        with endpoint('hostkey_mismatch') as (client, accepted):
            info, message = self.failed(client)
            self.assertEqual(info['reason'], 'algorithm_negotiation_failed')
            self.assertEqual(info['stage'], 'ssh_handshake')
            self.assertTrue(info['tcp_connected']);self.assertTrue(info['ssh_banner_received'])
            self.assertEqual(len(accepted), 1)

    def test_actual_authentication_still_records_port_and_ssh_success(self):
        with router('normal') as (client, server):
            client.connect('valid',client.fingerprint_for_key(KEY),timeout=2)
            info = client.login_diagnostics.snapshot()[-1]
            self.assertEqual(info['reason'],'authenticated')
            self.assertTrue(info['tcp_connected']);self.assertTrue(info['ssh_banner_received'])
            self.assertEqual(server.attempts,[('password','root','valid')])

    def test_network_timeout_never_claims_tcp_connected_or_ssh_disabled(self):
        client = OpenWrtSshClient();client.configure('private-host',22,'root')
        fake = Mock();fake.get_transport.return_value = None
        fake.connect.side_effect = TimeoutError(10060, 'private endpoint')
        with patch('paramiko.SSHClient',return_value=fake):
            info, message = self.failed(client)
        self.assertEqual(info['stage'],'tcp_connect')
        self.assertEqual(info['reason'],'connection_timeout')
        self.assertFalse(info['tcp_connected']);self.assertFalse(info['ssh_banner_received'])
        self.assertNotIn('private endpoint',message)
        self.assertNotIn('private-host',message)
        fake.close.assert_called_once()

    def test_socket_timeout_after_auth_started_is_not_a_network_connect_timeout(self):
        from modules.capture_services.openwrt.login_diagnostics import LoginDiagnostics
        journal = LoginDiagnostics()
        journal.begin(NS(_port=22,_user='root'),'secret',15,'password','')
        journal.update(stage='authentication',tcp_connected=True,ssh_banner_received=True)
        error = TimeoutError('private endpoint')
        message = connection_message(error,journal.current(),22)
        journal.finish(.1,error)
        info = journal.snapshot()[-1]
        self.assertEqual(info['stage'],'authentication')
        self.assertEqual(info['reason'],'authentication_timeout')
        self.assertNotIn('private endpoint',message)

    def test_negotiation_error_cannot_erase_already_observed_authentication(self):
        from modules.capture_services.openwrt.login_diagnostics import LoginDiagnostics
        journal = LoginDiagnostics()
        journal.begin(NS(_port=22,_user='root'),'secret',15,'password','')
        journal.update(stage='authentication',tcp_connected=True,ssh_banner_received=True)
        error = paramiko.ssh_exception.IncompatiblePeer('Incompatible ssh peer (no acceptable host key)')
        message = connection_message(error,journal.current(),22)
        journal.finish(.1,error)
        info = journal.snapshot()[-1]
        self.assertEqual(info['stage'],'authentication')
        self.assertEqual(info['reason'],'algorithm_negotiation_failed')
        self.assertNotIn('未进入密码验证',message)

    def test_mixed_address_family_failures_do_not_claim_all_ports_refused(self):
        from modules.capture_services.openwrt.login_diagnostics import LoginDiagnostics
        journal = LoginDiagnostics()
        journal.begin(NS(_port=22,_user='root'),'secret',15,'password','')
        journal.update(stage='tcp_connect',tcp_connected=False,ssh_banner_received=False)
        error = paramiko.ssh_exception.NoValidConnectionsError({
            ('127.0.0.1',22):ConnectionRefusedError(10061,'private endpoint'),
            ('::1',22):OSError(10065,'private endpoint')})
        message = connection_message(error,journal.current(),22)
        journal.finish(.1,error)
        info = journal.snapshot()[-1]
        self.assertEqual(info['reason'],'connection_error')
        self.assertFalse(info['tcp_connected'])
        self.assertNotIn('明确拒绝',message)
        self.assertNotIn('private endpoint',json.dumps(info))

    def test_upstream_auth_wait_preserves_auth_disconnect_category(self):
        from paramiko.auth_handler import AuthHandler
        from modules.capture_services.openwrt.login_diagnostics import LoginDiagnostics
        for native in (ConnectionResetError(10054,'private endpoint'),
                       paramiko.SSHException('private endpoint'), EOFError('private endpoint')):
            with self.subTest(error=type(native).__name__):
                transport = Mock(auth_timeout=1)
                transport.is_active.return_value = False
                transport.get_exception.return_value = native
                handler = AuthHandler(transport)
                try:
                    handler.wait_for_response(Event())
                except Exception as propagated:
                    journal = LoginDiagnostics()
                    journal.begin(NS(_port=22,_user='root'),'secret',15,'password','')
                    journal.update(stage='authentication',tcp_connected=True,ssh_banner_received=True)
                    message = connection_message(propagated,journal.current(),22)
                    journal.finish(.1,propagated)
                else:
                    self.fail('Inactive authentication transport unexpectedly succeeded')
                info = journal.snapshot()[-1]
                self.assertEqual(info['stage'],'authentication')
                self.assertEqual(info['reason'],'authentication_interrupted')
                self.assertNotIn('private endpoint',json.dumps(info))
                self.assertNotIn('private endpoint',message)

    def test_invalid_local_keys_do_not_start_network_or_claim_disconnect(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        cases = ('malformed', 'public_only', 'encrypted', 'oversized', 'missing')
        for case in cases:
            with self.subTest(case=case), TemporaryDirectory() as folder:
                path = Path(folder)/'private-local-key'
                if case == 'malformed':path.write_bytes(b'private invalid key material')
                elif case == 'public_only':path.write_text(KEY.get_name()+' '+KEY.get_base64(),encoding='utf-8')
                elif case == 'encrypted':KEY.write_private_key_file(str(path),password='private-key-passphrase')
                elif case == 'oversized':path.write_bytes(b'x'*(1024*1024+1))
                client = OpenWrtSshClient();client.configure('private-host',22,'root')
                fake = Mock();fake.get_transport.return_value = None
                with patch('paramiko.SSHClient',return_value=fake):
                    with self.assertRaises(RuntimeError) as caught:
                        client.connect('',client.fingerprint_for_key(KEY),timeout=2,
                                       auth_method='private_key',private_key_path=str(path))
                row = client.login_diagnostics.snapshot()[-1]
                self.assertEqual(row['stage'],'configuration')
                expected = 'key_file_unavailable' if case == 'missing' else 'key_passphrase_required' if case == 'encrypted' else 'key_file_invalid'
                self.assertEqual(row['reason'],expected)
                self.assertFalse(row['tcp_connected']);self.assertFalse(row['ssh_banner_received'])
                fake.connect.assert_not_called();fake.close.assert_called_once()
                for private in (str(path),'private-local-key','private-key-passphrase','private invalid key material'):
                    self.assertNotIn(private,json.dumps(row))
                    self.assertNotIn(private,str(caught.exception))
