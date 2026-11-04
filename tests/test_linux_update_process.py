"""Process ticket policy plus native Linux pidfd/child probe integration."""
from contextlib import contextmanager
import copy
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock,patch
from modules.platform_services import linux_update_process as process,linux_update_child as child

BOOT='11111111-2222-3333-4444-555555555555'
TICKET=dict(pid=42,start_ticks=100,device=1,inode=20,boot_id=BOOT)


@contextmanager
def kernel_model(events=None,identity=None):
    poll=Mock();poll.poll.side_effect=events or [[],[(9,1)]]
    select=NS(poll=Mock(return_value=poll),POLLIN=1,POLLHUP=16,POLLERR=8,POLLNVAL=32)
    with patch.object(process,'_native'),patch.object(process,'select',select),patch.object(process,'boot_id',return_value=BOOT),patch.object(process,'_identity',return_value=identity or {k:TICKET[k] for k in ('pid','start_ticks','device','inode')}),patch.object(process.os,'pidfd_open',return_value=9,create=True) as opened,patch.object(process.os,'close') as closed:
        yield poll,opened,closed


class LinuxProcessPolicyTests(unittest.TestCase):
    def test_stat_parser_handles_spaces_and_parentheses_in_process_name(self):
        raw='42 (a (b) c) '+' '.join(['S']+['0']*18+['12345']+['0']*5)
        self.assertEqual(process.start_ticks(raw,42),12345)
        with self.assertRaises(ValueError):process.start_ticks(raw,99)

    def test_invalid_tickets_fail_closed(self):
        for key,value in [('pid',True),('pid',-1),('boot_id','invalid'),('start_ticks','100')]:
            ticket=copy.deepcopy(TICKET);ticket[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):process.validate_ticket(ticket)

    def test_valid_wait_uses_one_kernel_wait_and_closes_handle(self):
        with kernel_model() as (poll,opened,closed):process.wait_for_parent(TICKET,2)
        self.assertEqual(poll.poll.call_args_list[0].args,(0,))
        self.assertEqual(poll.poll.call_args_list[1].args,(2000,));closed.assert_called_once_with(9)

    def test_reused_pid_does_not_wait_for_an_unrelated_process(self):
        identity={k:TICKET[k] for k in ('pid','start_ticks','device','inode')};identity['start_ticks']=999
        with kernel_model(events=[[]],identity=identity) as (poll,opened,closed):process.wait_for_parent(TICKET)
        self.assertEqual(poll.poll.call_count,1);closed.assert_called_once_with(9)

    def test_timeout_and_invalid_handle_never_report_parent_exit(self):
        for events,error in [([[],[]],TimeoutError),([[(9,32)]],OSError)]:
            with self.subTest(error=error),kernel_model(events) as (poll,opened,closed),self.assertRaises(error):process.wait_for_parent(TICKET,1)
            closed.assert_called_once_with(9)

    def test_previous_boot_parent_is_gone_without_opening_a_pid(self):
        with patch.object(process,'_native'),patch.object(process,'boot_id',return_value='aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'),patch.object(process.os,'pidfd_open',create=True) as opened:
            process.wait_for_parent(TICKET);opened.assert_not_called()

    def test_child_environment_resets_bundle_paths_and_smoke_user_data(self):
        with patch.dict(os.environ,{'LD_LIBRARY_PATH':'old','_PYI_APPLICATION_HOME_DIR':'old','PUBLISH_SECRET':'secret','DBUS_SESSION_BUS_ADDRESS':'session'}):
            env=child.environment(Path('/tmp/private-smoke'))
        self.assertNotIn('LD_LIBRARY_PATH',env);self.assertNotIn('PUBLISH_SECRET',env)
        self.assertNotIn('_PYI_APPLICATION_HOME_DIR',env);self.assertNotIn('DBUS_SESSION_BUS_ADDRESS',env)
        self.assertEqual(env['PYINSTALLER_RESET_ENVIRONMENT'],'1');self.assertEqual(env['HOME'],str(Path('/tmp/private-smoke')))


@unittest.skipUnless(sys.platform.startswith('linux'),'Native Linux pidfd and process groups required')
class NativeLinuxProcessTests(unittest.TestCase):
    def test_current_parent_ticket_times_out_without_killing_it(self):
        ticket=process.capture_parent(sys.executable)
        self.assertEqual(ticket['pid'],os.getpid())
        with self.assertRaises(TimeoutError):process.wait_for_parent(ticket,0.01)

    def test_actual_child_exit_signals_pidfd(self):
        runner=subprocess.Popen([sys.executable,'-c','import sys; sys.stdin.buffer.read(1)'],stdin=subprocess.PIPE)
        try:
            ticket=process.capture_parent(sys.executable,runner.pid);runner.stdin.close()
            process.wait_for_parent(ticket,5);self.assertEqual(runner.wait(timeout=2),0)
        finally:
            if runner.poll() is None:runner.kill();runner.wait(timeout=2)

    def test_startup_probe_bounds_output_and_reaps_timed_out_children(self):
        with tempfile.TemporaryDirectory() as directory:
            env=child.environment(Path(directory))
            stop=child._stop
            def checked_stop(process):
                self.assertIsNone(process.returncode)
                stop(process)
            with patch.object(child,'_stop',side_effect=checked_stop):
                value=child.run_check([sys.executable,'-c','print("ok")'],directory,env,5)
            self.assertEqual(value['returncode'],0)
            with self.assertRaises(ValueError):child.run_check([sys.executable,'-c','print("x"*10000)'],directory,env,5,100)
            with self.assertRaises(TimeoutError):child.run_check([sys.executable,'-c','import time;time.sleep(30)'],directory,env,0.1)
