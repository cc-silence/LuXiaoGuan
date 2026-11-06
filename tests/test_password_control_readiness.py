"""Initialization uses the actual PONG handshake, with no extra readiness wait."""
import unittest
from threading import Event
from unittest.mock import Mock
from modules.capture_services.openwrt.password_control import PasswordControl, ControlRequestTimeout
from modules.capture_services.openwrt.operation_diagnostics import OperationDiagnostics
from tests.test_password_control import Protocol


class Clock:
    def __init__(self): self.now = 0
    def __call__(self):
        self.now += .25
        return self.now


class StartupTests(unittest.TestCase):
    def fixture(self, channel):
        ssh = Mock()
        streams = [Mock(), Mock(), Mock()]
        ssh.exec_command.return_value = (*streams, channel)
        return ssh, streams

    def test_ready_daemon_needs_pong_even_without_editor_prompt(self):
        channel = Protocol()
        channel.output.clear()
        ssh, streams = self.fixture(channel)
        control = PasswordControl(ssh, 'owned', Event())
        try:
            self.assertEqual(channel.commands[0], 'ping')
            self.assertNotIn('add_network', channel.commands)
        finally:
            control.close()

    def test_unresponsive_daemon_has_one_five_second_handshake(self):
        clock = Clock()
        channel = Protocol()
        channel.output.clear()
        channel.sendall = Mock()
        ssh, streams = self.fixture(channel)
        journal = OperationDiagnostics()
        with self.assertRaises(ControlRequestTimeout):
            PasswordControl(ssh, 'owned', Event(), clock=clock, journal=journal)
        self.assertLess(clock.now, 8)
        self.assertTrue(channel.closed)
        for stream in streams:
            stream.close.assert_called_once()
        self.assertNotIn('control_ready', str(journal.snapshot()))

    def test_cancelled_startup_closes_owned_resources(self):
        channel = Protocol()
        cancel = Event()
        cancel.set()
        ssh, streams = self.fixture(channel)
        with self.assertRaises(InterruptedError):
            PasswordControl(ssh, 'owned', cancel)
        self.assertTrue(channel.closed)
        self.assertNotIn('add_network', channel.commands)

    def test_banner_prompt_does_not_replace_pong(self):
        clock = Clock()
        channel = Protocol()
        channel.output = bytearray(b'> ')
        channel.sendall = Mock()
        ssh, streams = self.fixture(channel)
        with self.assertRaises(ControlRequestTimeout):
            PasswordControl(ssh, 'owned', Event(), clock=clock)
        self.assertTrue(channel.closed)

if __name__ == '__main__':
    unittest.main()
