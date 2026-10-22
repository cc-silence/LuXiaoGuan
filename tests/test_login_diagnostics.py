"""Real loopback login failures and privacy checks through the feedback ZIP."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
import zipfile
import paramiko

from tests.test_ssh_empty_password import router, KEY
from modules.capture_services.openwrt.ssh_client import OpenWrtSshClient, HostKeyConfirmationRequired, HostKeyChangedError
from modules.capture_services.openwrt.command_runner import OpenWrtCommandRunner
from modules.capture_services.openwrt.operation_diagnostics import sanitized_operation_evidence
from modules.capture_services.openwrt.login_diagnostics import LoginDiagnostics, sanitized_login_attempts, MAX_ATTEMPTS
from modules.capture_services.openwrt.diagnostic_context import router_context_json
from modules.capture_services.openwrt.models import OpenWrtSessionSnapshot
from modules.display_privacy import masker_for
from modules.log_services import export_logs


class LoginDiagnosticTests(unittest.TestCase):
    def connect(self, client, password='valid', **kwargs):
        return client.connect(password, client.fingerprint_for_key(KEY), timeout=3, **kwargs)

    def test_rejected_password_survives_failed_state_export_without_credentials(self):
        secret = '  synthetic-private-password  '
        with router('normal') as (client, server), TemporaryDirectory() as folder:
            runner = OpenWrtCommandRunner(client)
            with self.assertRaises(RuntimeError):
                self.connect(client, secret)
            self.assertEqual(server.attempts, [('password', 'root', secret)])
            context = router_context_json(OpenWrtSessionSnapshot.initial(), masker_for([]),
                                          operation_evidence=runner.operation_diagnostics.snapshot())
            path = Path(folder)/'feedback.zip'
            export_logs(path, 'login', [], router_context=context)
            with zipfile.ZipFile(path) as archive:
                self.assertIsNone(archive.testzip())
                raw = archive.read('operation-diagnostics.json').decode('utf-8')
                info = json.loads(raw)['login_attempts'][-1]
                self.assertEqual(info['stage'], 'authentication')
                self.assertEqual(info['reason'], 'authentication_rejected')
                self.assertEqual(info['exception_type'], 'AuthenticationException')
                self.assertEqual(info['auth_method'], 'password')
                self.assertTrue(info['password_present'])
                self.assertTrue(info['password_boundary_whitespace'])
                self.assertTrue(info['host_key_verified'])
                self.assertEqual(info['server_family'], 'paramiko')
                self.assertGreaterEqual(info['elapsed_seconds'], 0)
                self.assertFalse(info['allowed_auth_known'])
                self.assertNotIn(secret.strip(), raw)
                self.assertNotIn('127.0.0.1', raw)

    def test_method_restriction_retains_server_allowed_methods(self):
        with router('key') as (client, server):
            with self.assertRaises(RuntimeError):
                self.connect(client)
            info = client.login_diagnostics.snapshot()[-1]
            self.assertEqual(info['reason'], 'auth_method_not_allowed')
            self.assertEqual(info['allowed_auth_methods'], ['publickey'])
            self.assertTrue(info['allowed_auth_known'])

    def test_success_does_not_clear_same_device_failure(self):
        with router('normal') as (client, server):
            client.login_diagnostics.begin(client,'test',3,'password','')
            client.login_diagnostics.finish(0.1,paramiko.AuthenticationException('Authentication failed.'))
            self.connect(client)
            runner = OpenWrtCommandRunner(client)
            runner.operation_diagnostics.bind_connection('verified-host-key')
            self.assertEqual(runner.operation_diagnostics.snapshot()['login_attempts'][-1]['reason'], 'authenticated')
            self.assertEqual(server.attempts, [('password', 'root', 'valid')])
            self.assertEqual(runner.operation_diagnostics.snapshot()['login_attempts'][0]['reason'], 'authentication_rejected')

    def test_host_key_decisions_remain_before_authentication(self):
        for expected, exception, reason in (('', HostKeyConfirmationRequired, 'host_key_confirmation_required'),
                ('SHA256:other', HostKeyChangedError, 'host_key_changed')):
            with self.subTest(reason=reason), router('normal') as (client, server):
                with self.assertRaises(exception):
                    client.connect('valid', expected, timeout=3)
                info=client.login_diagnostics.snapshot()[-1]
                self.assertEqual(info['stage'], 'host_key')
                self.assertEqual(info['reason'], reason)
                self.assertFalse(info['host_key_verified'])
                self.assertFalse(server.attempts)

    def test_timeout_transport_and_key_file_errors_keep_original_exception(self):
        cases = ((paramiko.AuthenticationException('Authentication timeout.'), 'authentication_timeout', 'authentication'),
                 (ConnectionRefusedError(10061, 'secret endpoint'), 'connection_refused', 'connect_or_handshake'),
                 (TimeoutError('secret endpoint'), 'connection_timeout', 'connect_or_handshake'),
                 (paramiko.ssh_exception.NoValidConnectionsError({('private-host',22): ConnectionRefusedError(10061,'secret')}), 'connection_refused', 'connect_or_handshake'),
                 (paramiko.SSHException('secret banner'), 'handshake_error', 'connect_or_handshake'))
        for error, reason, stage in cases:
            with self.subTest(reason=reason):
                client=OpenWrtSshClient(); client.configure('private-host.example', 2222, 'private-user')
                transport=Mock(remote_version='')
                fake=Mock(); fake.get_transport.return_value=transport; fake.connect.side_effect=error
                with patch('paramiko.SSHClient', return_value=fake):
                    with self.assertRaises(Exception):self.connect(client)
                info=client.login_diagnostics.snapshot()[-1]
                self.assertEqual(info['reason'],reason)
                self.assertEqual(info['stage'],stage)
                raw=json.dumps(info)
                self.assertNotIn('secret',raw)
                self.assertNotIn('private-host',raw)
                self.assertNotIn('private-user',raw)
                self.assertEqual(info['port'],2222)

    def test_private_key_error_exports_no_key_path_or_passphrase(self):
        client=OpenWrtSshClient();client.configure('private-host',22,'root')
        fake=Mock();fake.get_transport.return_value=None
        fake.connect.side_effect=FileNotFoundError(2,'private file missing','C:/private-user/private-key.pem')
        with patch('paramiko.SSHClient',return_value=fake):
            with self.assertRaises(FileNotFoundError):
                self.connect(client,'ignored-passphrase',auth_method='private_key',private_key_path='C:/private-user/private-key.pem')
        info=client.login_diagnostics.snapshot()[-1]
        self.assertEqual(info['stage'],'configuration')
        self.assertEqual(info['reason'],'key_file_unavailable')
        self.assertEqual(info['auth_method'],'private_key')
        self.assertTrue(info['private_key_selected'])
        self.assertFalse(info['password_present'])
        self.assertNotIn('private-key.pem',json.dumps(info))
        self.assertNotIn('ignored-passphrase',json.dumps(info))
        fake.close.assert_called_once()

    def test_history_is_bounded_and_identity_changes_clear_it(self):
        client=OpenWrtSshClient();client.configure('host-a',22,'root')
        journal=client.login_diagnostics
        for _ in range(30):
            journal.begin(client,'test',3,'password','')
            journal.finish(0.1,ValueError('not retained'))
        self.assertEqual(len(journal.snapshot()),MAX_ATTEMPTS)
        client.configure('host-a',22,'root')
        self.assertEqual(len(journal.snapshot()),MAX_ATTEMPTS)
        for host,port,user in (('host-a',2222,'root'),('host-a',2222,'other'),('host-b',2222,'other')):
            client.configure(host,port,user)
            self.assertFalse(journal.snapshot())
            journal.begin(client,'test',3,'password','');journal.finish(0.1)

    def test_export_allowlist_rejects_unbounded_or_injected_details(self):
        with self.assertRaises(ValueError):sanitized_login_attempts([{}]*(MAX_ATTEMPTS+1))
        row=dict(status='failed',stage='authentication',reason='authentication_rejected',password='secret',
                 exception_type='secret',server_family='secret',server_version='2022.82 secret',
                 allowed_auth_methods=['password','secret'],errno=float('inf'))
        evidence=dict(schema_version=1,login_attempts=[row])
        clean=sanitized_operation_evidence(evidence,masker_for([]))
        self.assertNotIn('secret',json.dumps(clean))
        self.assertEqual(clean['login_attempts'][0]['allowed_auth_methods'],['password'])

    def test_old_and_new_server_identification_is_bounded(self):
        client=OpenWrtSshClient();client.configure('private-host',22,'root')
        journal=client.login_diagnostics
        for banner,family,version in (('SSH-2.0-dropbear_2019.78','dropbear','2019.78'),
                ('SSH-2.0-dropbear_2022.82','dropbear','2022.82'),
                ('SSH-2.0-dropbear_2025.88','dropbear','2025.88'),
                ('SSH-2.0-OpenSSH_9.9p1 private-details','openssh','9.9'),
                ('SSH-2.0-private-banner','other',None)):
            with self.subTest(banner=banner):
                journal.begin(client,'test',3,'password','')
                journal.transport(Mock(remote_version=banner,is_active=Mock(return_value=True)))
                journal.finish(0.1)
                info=journal.snapshot()[-1]
                self.assertEqual(info['server_family'],family)
                self.assertEqual(info.get('server_version'),version)
                self.assertNotIn('private-details',json.dumps(info))
                self.assertNotIn('private-banner',json.dumps(info))
                self.assertNotIn('private-host',json.dumps(info))
