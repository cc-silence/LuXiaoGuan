"""Second review: asynchronous Winsock completion and concurrent startup audit."""
from threading import Event
import unittest
from unittest.mock import Mock, patch

import paramiko
from modules.capture_services.openwrt import ssh_connect_control as control
from modules.desktop_shell.startup_firewall_controller import StartupFirewallController
from tests.test_login_policy_repair import denied
from tests.test_ssh_empty_password import KEY, router


class AsynchronousConnectionTests(unittest.TestCase):
    def addresses(self):
        return [(control.socket.AF_INET, control.socket.SOCK_STREAM, 0, '', ('192.0.2.1', 22))]

    def test_delayed_windows_denial_repairs_only_after_so_error(self):
        first, second = Mock(), Mock()
        first.connect_ex.return_value = 10035
        first.getsockopt.return_value = 10013
        second.connect_ex.return_value = 0
        def repair():
            first.getsockopt.assert_called_once_with(control.socket.SOL_SOCKET, control.socket.SO_ERROR)
            first.close.assert_called_once()
        callback = Mock(side_effect=repair)
        with patch.object(control.socket, 'getaddrinfo', return_value=self.addresses()), patch.object(control.socket, 'socket', side_effect=[first, second]), patch.object(control.select, 'select', return_value=([], [], [first])):
            self.assertIs(control._open_login_socket('192.0.2.1', 22, 3, Event(), callback), second)
        callback.assert_called_once()
        second.close()

    def test_delayed_success_uses_original_connection_without_repair(self):
        sock, repair = Mock(), Mock()
        sock.connect_ex.return_value = 10035
        sock.getsockopt.return_value = 0
        with patch.object(control.socket, 'getaddrinfo', return_value=self.addresses()), patch.object(control.socket, 'socket', return_value=sock), patch.object(control.select, 'select', return_value=([], [sock], [])):
            self.assertIs(control._open_login_socket('192.0.2.1', 22, 3, Event(), repair), sock)
        repair.assert_not_called()
        sock.close.assert_not_called()
        sock.close()

    def test_delayed_router_refusal_does_not_modify_policy(self):
        sock, repair = Mock(), Mock()
        sock.connect_ex.return_value = 10035
        sock.getsockopt.return_value = 10061
        with patch.object(control.socket, 'getaddrinfo', return_value=self.addresses()), patch.object(control.socket, 'socket', return_value=sock), patch.object(control.select, 'select', return_value=([], [], [sock])):
            with self.assertRaises(paramiko.ssh_exception.NoValidConnectionsError):
                control._open_login_socket('192.0.2.1', 22, 3, Event(), repair)
        repair.assert_not_called()
        sock.close.assert_called_once()

    def test_cancellation_after_denial_prevents_policy_request(self):
        cancel, repair = Event(), Mock()
        def failed(*args):
            cancel.set()
            raise denied()
        with patch.object(control, '_connect_socket', side_effect=failed):
            with self.assertRaises(InterruptedError):
                control._open_login_socket('192.0.2.1', 22, 3, cancel, repair)
        repair.assert_not_called()


class ConcurrentStartupTests(unittest.TestCase):
    def test_running_then_failed_audit_does_not_delay_or_disconnect_real_login(self):
        entered, release = Event(), Event()
        controller = StartupFirewallController()
        controller.applicable = True
        def audit(**kwargs):
            entered.set()
            if not release.wait(3):
                raise TimeoutError('Test audit was not released')
            raise OSError('Fixture audit failure')
        with patch('modules.desktop_shell.startup_firewall_controller.ensure_startup_firewall_rules', side_effect=audit):
            controller.start()
            try:
                self.assertTrue(entered.wait(2))
                self.assertTrue(controller.active)
                with router('normal') as (client, server):
                    # Use the runtime callback with the actual listener target.
                    repair = controller.prepare_connection(client._host, client._port, Event())
                    client.connect('valid', client.fingerprint_for_key(KEY), timeout=3,
                                   cancel_event=Event(), network_repair=repair)
                    self.assertTrue(client.connected)
                    self.assertTrue(controller.active)
                    release.set()
                    self.assertTrue(controller._ready.wait(2))
                    self.assertFalse(controller.active)
                    self.assertTrue(controller._error)
                    self.assertTrue(client.connected)
                    self.assertEqual(server.attempts, [('password', 'root', 'valid')])
            finally:
                release.set()
                self.assertTrue(controller._ready.wait(2))


if __name__ == '__main__':
    unittest.main()
