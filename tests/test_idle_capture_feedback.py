"""Idle dual-radio feedback: placeholders, closed SFTP and failed result transfer."""
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import unittest
from modules.capture_services.openwrt.capture_placeholders import release_owned_placeholder, prepare_placeholders
from modules.capture_services.openwrt.command_runner import RemoteCommandResult
from modules.capture_services.openwrt.ssh_client import OpenWrtSshClient


class IdleCaptureFeedbackTests(unittest.TestCase):
    def test_replaces_only_registered_same_connection_placeholder(self):
        connection=object()
        owner=NS(_created_stubs=['hcxstub1'],_bound_client=connection,_bound_fingerprint='same')
        runner=Mock();runner.run.return_value=RemoteCommandResult('replace',0,'')
        lane=NS(phy='phy1',runner=runner,_stop_event=None,_bound_client=connection,
                _bound_fingerprint='same',service=NS(sessions={'radio0':owner}))
        release_owned_placeholder(lane)
        self.assertEqual(owner._created_stubs,[])
        command=runner.run.call_args.args[1]
        self.assertIn('routermanager:autocapture-stub',command)
        self.assertIn('readlink',command)
        self.assertIn('iw dev hcxstub1 del',command)
        self.assertNotIn('hcxmon',command)

    def test_does_not_delete_foreign_placeholder_or_forget_failed_cleanup(self):
        connection=object();runner=Mock()
        owner=NS(_created_stubs=['hcxstub1'],_bound_client=object(),_bound_fingerprint='same')
        lane=NS(phy='phy1',runner=runner,_stop_event=None,_bound_client=connection,
                _bound_fingerprint='same',service=NS(sessions={'radio0':owner}))
        release_owned_placeholder(lane);runner.run.assert_not_called()
        owner._bound_client=connection
        runner.run.return_value=RemoteCommandResult('replace',74,'','ownership mismatch')
        with self.assertRaises(RuntimeError):release_owned_placeholder(lane)
        self.assertEqual(owner._created_stubs,['hcxstub1'])

    def test_placeholder_failure_keeps_created_resource_for_cleanup(self):
        runner=Mock()
        runner.run.return_value=RemoteCommandResult('stubs',0,'STUB_CREATED:hcxstub2\nSTUB_FAILED\n')
        session=NS(runner=runner,_stop_event=None,_created_stubs=[])
        with self.assertRaises(RuntimeError):prepare_placeholders(session)
        self.assertEqual(session._created_stubs,['hcxstub2'])

    def test_placeholder_addresses_need_no_router_hexdump(self):
        runner=Mock();runner.run.return_value=RemoteCommandResult('stubs',0,'STUB_CREATED:hcxstub0\nSTUB_CREATED:hcxstub260\n')
        lane=NS(runner=runner,_stop_event=None,_created_stubs=[])
        with patch('modules.capture_services.openwrt.capture_placeholders.secrets.token_bytes',return_value=bytes([17,34,51])):
            prepare_placeholders(lane)
        command=runner.run.call_args.args[1]
        self.assertNotIn('hexdump',command)
        self.assertIn("02:11:22:33:%02x:%02x",command)
        self.assertIn('_num / 256',command)
        self.assertIn('_num % 256',command)
        self.assertEqual(lane._created_stubs,['hcxstub0','hcxstub260'])

    def test_placeholder_inventory_failure_is_not_treated_as_empty_hardware(self):
        runner=Mock();runner.run.return_value=RemoteCommandResult('stubs',76,'')
        session=NS(runner=runner,_stop_event=None,_created_stubs=[])
        with self.assertRaises(RuntimeError):prepare_placeholders(session)
        command=runner.run.call_args.args[1]
        self.assertEqual(command.count('iw dev'),1)
        self.assertIn('|| exit 76',command)
        self.assertIn('^phy#${_num}$',command)

    def test_capture_retry_closes_failed_channel_and_never_reuses_it(self):
        for message in ("Channel closed.", "Channel is not open"):
            with self.subTest(message=message):
                import threading
                from paramiko import SSHException
                ssh=OpenWrtSshClient();ssh._client=Mock()
                transport=ssh._client.get_transport.return_value
                transport.is_active.return_value=True
                first,second=Mock(),Mock()
                first.get_pty.side_effect=SSHException(message)
                transport.open_session.side_effect=[first,second]
                result=ssh.open_capture_stream(ssh._client,'owned-command',threading.Event())
                self.assertIs(result,second)
                first.close.assert_called_once()
                first.exec_command.assert_not_called()
                second.exec_command.assert_called_once_with('owned-command')
                second.close.assert_not_called()

    def test_closed_sftp_cache_is_rebuilt_without_reconnecting_ssh(self):
        from paramiko import Channel
        ssh=OpenWrtSshClient();ssh._client=Mock()
        ssh._client.get_transport.return_value.is_active.return_value=True
        old_channel=Channel(4);old_channel.close()
        old=Mock();old.get_channel.return_value=old_channel;ssh._sftp=old
        new=Mock();new.get_channel.return_value=NS(closed=False,active=True)
        ssh._open_owned=Mock(return_value=new)
        self.assertIs(ssh.open_sftp(),new)
        ssh._open_owned.assert_called_once();old.close.assert_called_once()
        ssh._open_owned.reset_mock()
        self.assertIs(ssh.open_sftp(),new);ssh._open_owned.assert_not_called()

    def test_download_failure_is_retained_in_session_error(self):
        from modules.capture_services.openwrt.auto_capture_service import AutoCaptureRadioSession
        with TemporaryDirectory() as temp:
            ssh=Mock();ssh.transfer.return_value=nullcontext()
            ssh.open_sftp.return_value.stat.side_effect=OSError('Socket is closed')
            session=NS(remote_cap_file='/root/cap/owned.pcapng',output_dir=Path(temp),ssh_client=ssh,
                       radio_name='radio0',stats=NS(error_message='',file_size_bytes=0),service=Mock())
            AutoCaptureRadioSession._pull_capture_result(session)
            self.assertIn('Socket is closed',session.stats.error_message)
            self.assertFalse(list(Path(temp).glob('*.part')))

if __name__=='__main__':unittest.main()
