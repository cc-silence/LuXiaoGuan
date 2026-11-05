"""Three-round review: public key parser errors and login resource ownership."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from modules.capture_services.openwrt.ssh_login_probe import load_selected_private_key, PrivateKeyLoadError
from modules.capture_services.openwrt.ssh_client import OpenWrtSshClient
from tests.test_ssh_empty_password import KEY, router


class LoginReviewBoundaryTests(unittest.TestCase):
    def test_unsupported_crypto_backend_is_a_local_key_error(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'key';path.write_bytes(b'synthetic key data')
            with patch('paramiko.PKey.from_path',side_effect=UnsupportedAlgorithm('private backend detail')):
                with self.assertRaises(PrivateKeyLoadError) as caught:
                    load_selected_private_key(path)
        self.assertEqual(caught.exception.reason,'key_file_invalid')
        self.assertIsInstance(caught.exception.__cause__,UnsupportedAlgorithm)
        self.assertNotIn('private backend detail',str(caught.exception))

    def test_native_openssh_and_pem_parsers_remain_available(self):
        cases = ((ed25519.Ed25519PrivateKey.generate(),serialization.PrivateFormat.OpenSSH),
                 (ec.generate_private_key(ec.SECP256R1()),serialization.PrivateFormat.OpenSSH),
                 (ec.generate_private_key(ec.SECP256R1()),serialization.PrivateFormat.TraditionalOpenSSL))
        for key,fmt in cases:
            with self.subTest(key=type(key).__name__,fmt=fmt),TemporaryDirectory() as folder:
                path=Path(folder)/'key'
                path.write_bytes(key.private_bytes(serialization.Encoding.PEM,fmt,serialization.NoEncryption()))
                loaded=load_selected_private_key(path)
                self.assertTrue(loaded.can_sign())
                self.assertIn(loaded.get_name(),('ssh-ed25519','ecdsa-sha2-nistp256'))

    def test_native_associated_public_blob_and_cert_path_resolution_are_preserved(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'key';KEY.write_private_key_file(str(path))
            cert=Path(str(path)+'-cert.pub')
            cert.write_text(KEY.get_name()+' '+KEY.get_base64()+' fixture',encoding='utf-8')
            for selected in (path,cert):
                loaded=load_selected_private_key(selected)
                self.assertEqual(loaded.asbytes(),KEY.asbytes())
                self.assertIsNotNone(loaded.public_blob)

    def test_empty_key_selection_allocates_no_client_and_password_retry_works(self):
        with router('normal') as (client,server):
            with patch('paramiko.SSHClient') as factory:
                with self.assertRaises(ValueError):
                    client.connect(auth_method='private_key',private_key_path='  ')
                factory.assert_not_called()
            self.assertIsNone(client._connecting_client)
            info=client.login_diagnostics.snapshot()[-1]
            self.assertEqual(info['stage'],'configuration')
            self.assertEqual(info['reason'],'configuration_error')
            self.assertFalse(info.get('tcp_connected',False))
            client.connect('valid',client.fingerprint_for_key(KEY),timeout=3)
            self.assertTrue(client.connected)
            self.assertEqual(server.attempts,[('password','root','valid')])

    def test_live_connection_reuse_does_not_load_a_new_key_or_reauthenticate(self):
        with router('normal') as (client,server):
            first=client.connect('valid',client.fingerprint_for_key(KEY),timeout=3)
            with patch('paramiko.PKey.from_path') as loader,patch('paramiko.SSHClient') as factory:
                reused=client.connect(auth_method='private_key',private_key_path='')
                loader.assert_not_called()
                factory.assert_not_called()
            self.assertEqual(first,reused)
            self.assertTrue(client.connected)
            self.assertEqual(server.attempts,[('password','root','valid')])

    def test_native_rsa_signing_algorithm_mismatch_is_not_a_disconnect(self):
        import paramiko
        from types import SimpleNamespace
        from paramiko.auth_handler import AuthHandler
        from modules.capture_services.openwrt.login_diagnostics import LoginDiagnostics
        from modules.capture_services.openwrt.ssh_login_probe import connection_message
        transport=Mock(preferred_pubkeys=['rsa-sha2-512'],server_extensions={'server-sig-algs':b'ssh-ed25519'})
        handler=AuthHandler(transport)
        with self.assertRaises(paramiko.AuthenticationException) as caught:
            handler._finalize_pubkey_algorithm('ssh-rsa')
        journal=LoginDiagnostics()
        journal.begin(SimpleNamespace(_port=22,_user='root'),'',3,'private_key','private-path')
        journal.update(stage='authentication',tcp_connected=True,ssh_banner_received=True,transport_active=True)
        message=connection_message(caught.exception,journal.current(),22)
        journal.finish(.1,caught.exception)
        info=journal.snapshot()[-1]
        self.assertEqual(info['reason'],'public_key_algorithm_mismatch')
        self.assertEqual(info['stage'],'authentication')
        self.assertTrue(info['transport_active'])
        self.assertTrue(message)
        self.assertNotIn('private-path',str(info))

    def test_password_rejection_does_not_become_an_algorithm_error(self):
        import paramiko
        from modules.capture_services.openwrt.ssh_login_probe import negotiation_reason
        self.assertIsNone(negotiation_reason(paramiko.AuthenticationException('Authentication failed.')))
        self.assertIsNone(negotiation_reason(paramiko.AuthenticationException('Authentication timeout.')))

    def test_real_ssh_signing_mismatch_keeps_reason_and_closes_connection(self):
        import paramiko
        original=paramiko.Transport
        class NarrowServerSignatures(original):
            @property
            def preferred_pubkeys(self):
                return ('ssh-ed25519',) if self.server_mode else super().preferred_pubkeys
        with TemporaryDirectory() as folder,patch('paramiko.Transport',NarrowServerSignatures),router('key') as (client,server):
            path=Path(folder)/'key';KEY.write_private_key_file(str(path))
            with self.assertRaises(RuntimeError) as caught:
                client.connect('',client.fingerprint_for_key(KEY),timeout=3,
                               auth_method='private_key',private_key_path=str(path))
            info=client.login_diagnostics.snapshot()[-1]
            self.assertEqual(info['reason'],'public_key_algorithm_mismatch')
            self.assertEqual(info['stage'],'authentication')
            self.assertTrue(info['tcp_connected'])
            self.assertTrue(info['host_key_verified'])
            self.assertFalse(client.connected)
            self.assertIsNone(client._connecting_client)
            self.assertFalse(server.attempts)
            self.assertNotIn(str(path),str(caught.exception))
