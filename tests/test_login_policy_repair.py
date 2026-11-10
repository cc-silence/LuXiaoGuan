"""Policy preparation must follow a real TCP denial, before SSH authentication."""
import errno
from threading import Event
import unittest
from unittest.mock import Mock, patch

import paramiko
from modules.capture_services.openwrt import ssh_connect_control as control
from modules.capture_services.login_operation import run_login
from modules.platform_services import network_policy
from tests.test_ssh_empty_password import KEY, router


def denied(code=10013):
    return paramiko.ssh_exception.NoValidConnectionsError({('192.0.2.1', 22): OSError(code, 'fixture')})


class PolicySocketTests(unittest.TestCase):
    def test_connected_socket_never_requests_policy_changes(self):
        sock, repair = Mock(), Mock()
        with patch.object(control, '_connect_socket', return_value=sock) as connect:
            self.assertIs(control._open_login_socket('192.0.2.1', 22, 3, Event(), repair), sock)
        repair.assert_not_called()
        connect.assert_called_once()

    def test_access_denied_repairs_once_and_reuses_second_socket(self):
        sock, repair = Mock(), Mock()
        cancel = Event()
        with patch.object(control, '_connect_socket', side_effect=[denied(), sock]) as connect:
            self.assertIs(control._open_login_socket('192.0.2.1', 22, 3, cancel, repair), sock)
        repair.assert_called_once_with()
        self.assertEqual(connect.call_args_list[0], connect.call_args_list[1])

    def test_persistent_denial_is_not_a_retry_loop(self):
        repair = Mock()
        with patch.object(control, '_connect_socket', side_effect=denied()) as connect:
            with self.assertRaises(paramiko.ssh_exception.NoValidConnectionsError):
                control._open_login_socket('192.0.2.1', 22, 3, Event(), repair)
        self.assertEqual(connect.call_count, 2)
        repair.assert_called_once()

    def test_refusal_timeout_dns_and_network_errors_never_change_policy(self):
        import socket
        for error in (denied(10061), denied(10065), TimeoutError(), socket.gaierror(-2, 'fixture')):
            with self.subTest(error=repr(error)):
                repair = Mock()
                with patch.object(control, '_connect_socket', side_effect=error) as connect:
                    with self.assertRaises(OSError):
                        control._open_login_socket('192.0.2.1', 22, 3, Event(), repair)
                repair.assert_not_called()
                connect.assert_called_once()

    def test_mixed_address_errors_do_not_imply_policy_denial(self):
        error = paramiko.ssh_exception.NoValidConnectionsError({
            ('192.0.2.1', 22): OSError(10013, 'fixture'),
            ('192.0.2.2', 22): ConnectionRefusedError(errno.ECONNREFUSED, 'fixture')})
        repair = Mock()
        with patch.object(control, '_connect_socket', side_effect=error):
            with self.assertRaises(OSError):
                control._open_login_socket('fixture', 22, 3, Event(), repair)
        repair.assert_not_called()

    def test_repair_failure_and_cancellation_prevent_second_connection(self):
        for cancel_during in (False, True):
            cancel = Event()
            repair = Mock(side_effect=cancel.set if cancel_during else PermissionError('authorization failed'))
            with patch.object(control, '_connect_socket', side_effect=denied()) as connect:
                with self.assertRaises(InterruptedError if cancel_during else PermissionError):
                    control._open_login_socket('192.0.2.1', 22, 3, cancel, repair)
            connect.assert_called_once()

    def test_no_cancellation_event_still_uses_repair_and_closes_failed_socket(self):
        sock, repair = Mock(), Mock()
        with patch.object(control, '_connect_socket', return_value=sock):
            with self.assertRaisesRegex(ValueError, 'handshake'):
                with control.login_socket('192.0.2.1', 22, 3, None, repair) as connected:
                    self.assertIs(connected, sock)
                    raise ValueError('handshake')
        sock.close.assert_called_once()
        repair.assert_not_called()


class PolicyLoginIntegrationTests(unittest.TestCase):
    def test_actual_ssh_connection_authenticates_once_without_policy(self):
        repair = Mock(side_effect=AssertionError('Unnecessary policy mutation'))
        with router('normal') as (client, server):
            client.connect('valid', client.fingerprint_for_key(KEY), timeout=3,
                           cancel_event=Event(), network_repair=repair)
            self.assertTrue(client.connected)
            self.assertEqual(server.attempts, [('password', 'root', 'valid')])
        repair.assert_not_called()

    def test_real_ssh_authentication_after_one_local_denial(self):
        repair = Mock()
        original = control._connect_socket
        attempts = []
        def connect(*args):
            attempts.append(args)
            if len(attempts) == 1:
                raise denied()
            return original(*args)
        with router('normal') as (client, server), patch.object(control, '_connect_socket', side_effect=connect):
            client.connect('valid', client.fingerprint_for_key(KEY), timeout=3,
                           cancel_event=Event(), network_repair=repair)
            self.assertTrue(client.connected)
            self.assertEqual(server.attempts, [('password', 'root', 'valid')])
        repair.assert_called_once()
        self.assertEqual(len(attempts), 2)

    def test_authentication_failure_does_not_trigger_policy_or_auth_retry(self):
        repair = Mock()
        with router('normal') as (client, server):
            with self.assertRaises(RuntimeError):
                client.connect('wrong', client.fingerprint_for_key(KEY), timeout=3,
                               cancel_event=Event(), network_repair=repair)
            self.assertEqual(server.attempts, [('password', 'root', 'wrong')])
        repair.assert_not_called()

    def test_host_identity_mismatch_never_changes_policy_or_authenticates(self):
        from modules.capture_services.openwrt.ssh_client import HostKeyChangedError
        repair = Mock()
        with router('normal') as (client, server):
            with self.assertRaises(HostKeyChangedError):
                client.connect('valid', 'SHA256:unexpected', timeout=3,
                               cancel_event=Event(), network_repair=repair)
            self.assertEqual(server.attempts, [])
        repair.assert_not_called()

    def test_preparation_passes_deferred_repair_without_executing_it(self):
        backend = Mock()
        backend.validate_settings.return_value = dict(host='192.0.2.1', port=22)
        repair = Mock()
        prepare = Mock(return_value=repair)
        cancel = Event()
        run_login(backend, {}, 'secret', 'fingerprint', cancel, None, network_prepare=prepare)
        repair.assert_not_called()
        self.assertIs(backend.connect.call_args.kwargs['network_repair'], repair)

    def test_failed_or_pending_firewall_audit_does_not_block_connection(self):
        from modules.desktop_shell.startup_firewall_controller import StartupFirewallController
        for route in ('firewall', 'ipsec'):
            with self.subTest(route=route):
                controller = StartupFirewallController()
                controller.applicable = True
                controller._error = 'Earlier audit failed'
                controller._audit_route = route
                with patch.object(controller, 'wait_ready', side_effect=AssertionError('Audit blocked login')):
                    repair = controller.prepare_connection('192.0.2.1', 22, Event())
                    with router('normal') as (client, server):
                        client.connect('valid', client.fingerprint_for_key(KEY), timeout=3,
                                       cancel_event=Event(), network_repair=repair)
                        self.assertTrue(client.connected)
                        self.assertEqual(server.attempts, [('password', 'root', 'valid')])

    def test_actual_backend_preserves_deferred_repair_and_fingerprint_confirmation(self):
        from modules.capture_services.backends.openwrt_backend import OpenWrtCaptureBackend
        from modules.capture_services.openwrt.ssh_client import HostKeyConfirmationRequired
        backend = OpenWrtCaptureBackend.__new__(OpenWrtCaptureBackend)
        backend.ssh_client = Mock()
        backend.ssh_client.connect.side_effect = HostKeyConfirmationRequired('192.0.2.1', 22, 'SHA256:fixture', 'ssh-rsa')
        repair = Mock()
        result = run_login(backend, dict(host='192.0.2.1', port=22, user='root'),
                           'secret', '', Event(), None, network_prepare=Mock(return_value=repair))
        self.assertEqual(result.status, 'fingerprint_required')
        self.assertIs(backend.ssh_client.connect.call_args.kwargs['network_repair'], repair)
        repair.assert_not_called()

    def test_disabled_firewall_controller_defers_mutation(self):
        from modules.desktop_shell.startup_firewall_controller import StartupFirewallController
        controller = StartupFirewallController()
        controller.applicable = True
        controller._completed = True
        controller._ready.set()
        controller._audit_route = 'ipsec'
        cancel = Event()
        with patch('modules.platform_services.windows_network_services.firewall_route', return_value='ipsec'), patch('modules.platform_services.network_policy.prepare_target') as prepare:
            repair = controller.prepare_connection('192.0.2.1', 22, cancel)
            prepare.assert_not_called()
            repair()
            prepare.assert_called_once_with('192.0.2.1', 22, cancel)


if __name__ == '__main__':
    unittest.main()
