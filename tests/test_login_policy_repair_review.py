"""Review regressions: wrapped Winsock errors and connection ownership."""
import errno
from threading import Event
import unittest
from unittest.mock import Mock, patch

import paramiko
from modules.capture_services.openwrt import ssh_connect_control as control
from modules.capture_services.openwrt.ssh_client import OpenWrtSshClient
from modules.capture_services.openwrt.ssh_login_probe import connection_message
from tests.test_login_policy_repair import denied
from tests.test_ssh_empty_password import KEY, router


class PolicyReviewTests(unittest.TestCase):
    def test_watcher_start_failure_closes_connected_socket_without_join(self):
        sock, watcher = Mock(), Mock()
        watcher.start.side_effect = RuntimeError('cannot start new thread')
        with patch.object(control, '_connect_socket', return_value=sock), patch.object(control, 'Thread', return_value=watcher):
            with self.assertRaisesRegex(RuntimeError, 'cannot start'):
                with control.login_socket('192.0.2.1', 22, 3, Event()):
                    self.fail('Failed watcher must not yield socket')
        sock.close.assert_called_once()
        watcher.join.assert_not_called()

    def test_failed_connect_socket_is_closed_before_repair(self):
        first, second = Mock(), Mock()
        first.connect_ex.return_value = 10013
        second.connect_ex.return_value = 0
        repair = Mock(side_effect=lambda: first.close.assert_called_once())
        address = (control.socket.AF_INET, control.socket.SOCK_STREAM, 0, '', ('192.0.2.1', 22))
        with patch.object(control.socket, 'getaddrinfo', return_value=[address]), patch.object(control.socket, 'socket', side_effect=[first, second]):
            connected = control._open_login_socket('192.0.2.1', 22, 3, Event(), repair)
        self.assertIs(connected, second)
        repair.assert_called_once()
        second.close.assert_not_called()
        second.close()

    def test_retry_failure_closes_both_native_sockets(self):
        first, second = Mock(), Mock()
        first.connect_ex.return_value = second.connect_ex.return_value = 10013
        address = (control.socket.AF_INET, control.socket.SOCK_STREAM, 0, '', ('192.0.2.1', 22))
        with patch.object(control.socket, 'getaddrinfo', return_value=[address]), patch.object(control.socket, 'socket', side_effect=[first, second]):
            with self.assertRaises(paramiko.ssh_exception.NoValidConnectionsError):
                control._open_login_socket('192.0.2.1', 22, 3, Event(), Mock())
        first.close.assert_called_once()
        second.close.assert_called_once()

    def test_wrapped_access_denial_retains_native_code_without_private_text(self):
        client = OpenWrtSshClient()
        client.configure('192.0.2.1', 22, 'root')
        repair = Mock()
        error = paramiko.ssh_exception.NoValidConnectionsError({('192.0.2.1', 22): OSError(10013, 'private-error-secret')})
        with patch.object(control, '_connect_socket', side_effect=error) as connect:
            with self.assertRaises(RuntimeError) as caught:
                client.connect('private-password-secret', 'SHA256:fixture', timeout=3,
                               cancel_event=Event(), network_repair=repair)
        row = client.login_diagnostics.snapshot()[-1]
        self.assertEqual(row['reason'], 'windows_access_denied')
        self.assertEqual(row['errno'], 10013)
        self.assertFalse(row['tcp_connected'])
        self.assertEqual(connect.call_count, 2)
        repair.assert_called_once()
        self.assertNotIn('secret', str(row))
        self.assertNotIn('192.0.2.1', str(row))
        self.assertNotIn('secret', str(caught.exception))

    def test_mixed_errors_do_not_misreport_access_denied(self):
        error = paramiko.ssh_exception.NoValidConnectionsError({
            ('192.0.2.1', 22): OSError(10013, 'fixture'),
            ('192.0.2.2', 22): ConnectionRefusedError(errno.ECONNREFUSED, 'fixture')})
        self.assertFalse(control.access_denied(error))
        self.assertEqual(connection_message(error, {'tcp_connected': False}, 22), '')

    def test_unverified_host_still_requires_authentication(self):
        repair = Mock()
        with router('normal') as (client, server):
            client.connect('valid', timeout=3, cancel_event=Event(), network_repair=repair)
            self.assertTrue(client.connected)
            self.assertTrue(client.host_fingerprint)
            self.assertEqual(server.attempts, [('password', 'root', 'valid')])
        repair.assert_not_called()

    def test_unverified_host_rejects_wrong_password(self):
        with router('normal') as (client, server):
            with self.assertRaises(RuntimeError):
                client.connect('wrong', timeout=3)
            self.assertFalse(client.connected)
            self.assertEqual(server.attempts, [('password', 'root', 'wrong')])

    def test_empty_password_with_deferred_repair_keeps_existing_auth_sequence(self):
        repair = Mock()
        with router('empty') as (client, server):
            client.connect('', client.fingerprint_for_key(KEY), timeout=3,
                           cancel_event=Event(), network_repair=repair)
            self.assertTrue(client.connected)
            self.assertEqual(server.attempts, [('none', 'root'), ('password', 'root', '')])
        repair.assert_not_called()


if __name__ == '__main__':
    unittest.main()
