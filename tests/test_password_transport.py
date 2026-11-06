"""Native transport selection and complete pre-candidate readiness checks."""
import json,unittest
from threading import Event
from unittest.mock import Mock,patch
from modules.capture_services.openwrt.command_runner import RemoteCommandResult
from modules.capture_services.openwrt.password_control import PasswordControl,ControlUnavailable,open_control
from modules.capture_services.openwrt.operation_diagnostics import OperationDiagnostics
from tests.test_password_control import Protocol

class PasswordTransportTests(unittest.TestCase):
    def runner(self):
        runner=Mock();runner.ssh_client.connected=True;runner.operation_diagnostics=OperationDiagnostics()
        payload={'radio1':{'interfaces':[{'section':'owned','ifname':'phy1-sta0'}]}}
        runner.run.return_value=RemoteCommandResult('probe',0,json.dumps(payload))
        return runner

    def test_pipe_first_then_terminal_only_before_any_candidate(self):
        runner=self.runner();control=Mock()
        with patch('modules.capture_services.openwrt.password_control.PasswordControl',side_effect=[ControlUnavailable('fixture'),control]) as factory:
            self.assertIs(open_control(runner,'owned',None),control)
        self.assertEqual([call.kwargs['transport'] for call in factory.call_args_list],['pipe','pty'])
        self.assertIn('native_pty_ready',str(runner.operation_diagnostics.snapshot()))

    def test_no_fallback_after_ssh_disconnect(self):
        runner=self.runner();runner.ssh_client.connected=False
        with patch('modules.capture_services.openwrt.password_control.PasswordControl',side_effect=ControlUnavailable('fixture')) as factory:
            with self.assertRaises(ControlUnavailable):open_control(runner,'owned',None)
        self.assertEqual(factory.call_count,1)

    def test_terminal_owned_channel_probes_disconnect_before_candidates(self):
        channel=Protocol();ssh=Mock();ssh.open_control_stream.return_value=channel
        control=PasswordControl(ssh,'phy1-sta0',Event(),transport='pty')
        self.assertIn('disconnect',channel.commands);self.assertNotIn('add_network',channel.commands)
        self.assertNotIn('stty',ssh.open_control_stream.call_args.args[0])
        ssh.exec_command.assert_not_called();self.assertEqual(control.streams,())
        control.close();self.assertTrue(channel.closed)

    def test_terminal_input_echo_does_not_become_a_reply_or_leak_to_diagnostics(self):
        channel=Protocol();ssh=Mock();ssh.open_control_stream.return_value=channel
        original=channel.sendall
        def echo(data):
            channel.output.extend(data.replace(b'\n',b'\r\n'))
            original(data)
        channel.sendall=echo
        journal=OperationDiagnostics()
        control=PasswordControl(ssh,'phy1-sta0',Event(),transport='pty',journal=journal)
        try:
            self.assertFalse(control.attempt('fixture','02:11:22:33:44:55','first-candidate'))
            self.assertTrue(control.attempt('fixture','02:11:22:33:44:55','second-candidate'))
            self.assertNotIn('first-candidate',str(journal.snapshot()))
            self.assertNotIn('second-candidate',str(journal.snapshot()))
        finally:
            control.close()
