"""Safe bounded retry and batch isolation for SSH command-channel refusal."""
import unittest
from threading import Event
from types import SimpleNamespace as NS
from unittest.mock import Mock
from paramiko.ssh_exception import SSHException
from modules.capture_services.openwrt.command_runner import OpenWrtCommandRunner, RemoteCommandResult


def closed(stage="exec_request", text="Channel is not open"):
    exc=SSHException(text);exc.ssh_stage=stage;return exc


class CommandChannelRetryTests(unittest.TestCase):
    def runner(self, effects):
        client=NS(connected=True,_client=object(),connection_generation=1,host_fingerprint='device')
        runner=OpenWrtCommandRunner(client)
        runner._run=Mock(side_effect=effects)
        return runner

    def test_both_error_spellings_retry_twice_then_succeed(self):
        for text in ('Channel closed.', 'Channel is not open'):
            with self.subTest(text=text):
                runner=self.runner([closed(text=text),closed(text=text),RemoteCommandResult('snapshot_wireless',0,'OK')])
                self.assertEqual(runner.run('snapshot_wireless','read only',20).stdout,'OK')
                self.assertEqual(runner._run.call_count,3)

    def test_exhausted_retry_raises_original_and_releases_slot(self):
        error=closed();runner=self.runner([closed(),closed(),error])
        with self.assertRaises(SSHException) as caught:runner.run('snapshot_wireless','read only',20)
        self.assertIs(caught.exception,error)
        runner._run.side_effect=None;runner._run.return_value=RemoteCommandResult('next',0)
        self.assertEqual(runner.run('next','next',1).exit_code,0)

    def test_mutations_not_replayed_after_exec_submission(self):
        for name in ('start_airodump-ng','restore_wireless','deauth_client','unknown'):
            runner=self.runner(closed())
            with self.assertRaises(SSHException):runner.run(name,'mutation',20)
            self.assertEqual(runner._run.call_count,1)

    def test_before_submission_can_retry_even_mutation(self):
        runner=self.runner([closed('channel_open'),closed('channel_open'),RemoteCommandResult('start',0)])
        self.assertEqual(runner.run('start','mutation',20).exit_code,0)
        self.assertEqual(runner._run.call_count,3)

    def test_unknown_stage_or_unrelated_error_is_not_retried(self):
        for error in (closed('unknown'),RuntimeError('Channel is not open'),SSHException('Authentication failed')):
            runner=self.runner(error)
            with self.assertRaises(type(error)):runner.run('snapshot_wireless','read only',20)
            self.assertEqual(runner._run.call_count,1)

    def test_cancel_disconnect_or_connection_change_prevents_retry(self):
        for mode in ('cancel','disconnect','generation','fingerprint','client'):
            with self.subTest(mode=mode):
                event=Event();runner=self.runner([])
                def fail(*a,**k):
                    if mode=='cancel':event.set()
                    elif mode=='disconnect':runner.ssh_client.connected=False
                    elif mode=='generation':runner.ssh_client.connection_generation+=1
                    elif mode=='fingerprint':runner.ssh_client.host_fingerprint='other'
                    else:runner.ssh_client._client=object()
                    raise closed()
                runner._run.side_effect=fail
                with self.assertRaises(SSHException):runner.run('snapshot_wireless','read only',20,event)
                self.assertEqual(runner._run.call_count,1)

    def test_retries_share_original_time_budget(self):
        runner=self.runner([closed(),closed(),RemoteCommandResult('snapshot_wireless',0)])
        runner.run('snapshot_wireless','read only',20)
        times=[call.args[2] for call in runner._run.call_args_list]
        self.assertTrue(all(0<t<=20 for t in times))
        self.assertEqual(times,sorted(times,reverse=True))

    def test_legacy_mutation_replay_remains_one_retry_only(self):
        runner=self.runner(RuntimeError("Channel closed"))
        with self.assertRaises(RuntimeError):runner.run('takeover_disable','mutation',20)
        self.assertEqual(runner._run.call_count,2)
