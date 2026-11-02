"""Real Windows cancel events and child processes; never real network writes."""
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import patch
from uuid import uuid4
from modules.platform_services.cancellation import CancellationEvent
from modules.platform_services import static_network_windows as windows
from tests.test_lan_client_recovery import adapter


class EventTests(unittest.TestCase):
    def test_subscriber_is_immediate_and_can_be_removed(self):
        token=CancellationEvent();calls=[]
        remove=token.subscribe(lambda:calls.append('cancel'))
        token.set();remove();token.set();self.assertEqual(calls,['cancel'])
        token.subscribe(lambda:calls.append('late'));self.assertEqual(calls,['cancel','late'])

    def test_management_task_controller_retains_immediate_cancel_event(self):
        from modules.capture_services.task_controller import CaptureTaskController
        token=CancellationEvent();calls=[];token.subscribe(lambda:calls.append(True))
        controller=CaptureTaskController()
        try:
            self.assertIs(controller.begin('router_management','test',cancel_event=token),token)
            self.assertTrue(controller.cancel());self.assertEqual(calls,[True])
        finally:controller.finish();controller.deleteLater()


@unittest.skipUnless(os.name=='nt','Windows wait handles required')
class WindowsCancellationTests(unittest.TestCase):
    def test_static_command_cancel_terminates_its_own_process_without_waiting_for_timeout(self):
        token=CancellationEvent();driver=windows.Driver(adapter());driver.cancel=token
        children=[];original=subprocess.Popen
        def launch(*args,**kwargs):
            child=original([sys.executable,'-c','import time; time.sleep(30)'],**kwargs)
            children.append(child);token.set();return child
        started=time.monotonic()
        with patch.object(windows.subprocess,'Popen',side_effect=launch):
            with self.assertRaises(InterruptedError):driver._execute(sys.executable,[],10)
        self.assertLess(time.monotonic()-started,3)
        self.assertIsNotNone(children[0].poll())

    def test_static_read_uses_no_powershell_process(self):
        driver=windows.Driver(adapter())
        with patch('modules.platform_services.static_network_windows_read.read',return_value={'addresses':[]}) as read,patch.object(windows.subprocess,'Popen',side_effect=AssertionError('Slow shell read')):
            self.assertEqual(driver.read(),{'addresses':[]})
        read.assert_called_once_with(driver.adapter)

    def request(self,event):
        from modules.platform_services.firewall_session_transport import PIPE_PREFIX
        return dict(operation='execute',file=sys.executable,args='-c "import time;time.sleep(30)"',directory=None,
                    timeout_ms=10000,show_window=False,reply_address=PIPE_PREFIX+uuid4().hex,cancel_event=event)

    def test_admin_command_cancel_terminates_only_the_submitted_child(self):
        from modules.platform_services.windows_cancel_wait import CancelSignal
        from modules.platform_services.admin_command_worker import AdminCommandJobs
        token=CancellationEvent();children=[];original=subprocess.Popen
        def launch(*args,**kwargs):
            child=original([sys.executable,'-c','import time;time.sleep(30)'],**kwargs)
            children.append(child);token.set();return child
        with CancelSignal(token) as signal:
            jobs=AdminCommandJobs(os.getpid())
            try:
                with patch('modules.platform_services.admin_command_worker.subprocess.Popen',side_effect=launch):
                    result=jobs.execute(self.request(signal.name))
                self.assertEqual(result['code'],1223);self.assertIsNotNone(children[0].poll())
            finally:jobs.close()

    def test_already_cancelled_admin_request_never_launches_a_process(self):
        from modules.platform_services.windows_cancel_wait import CancelSignal
        from modules.platform_services.admin_command_worker import AdminCommandJobs
        token=CancellationEvent();token.set()
        with CancelSignal(token) as signal:
            jobs=AdminCommandJobs(os.getpid())
            try:
                with patch('modules.platform_services.admin_command_worker.subprocess.Popen') as start:
                    result=jobs.execute(self.request(signal.name))
                self.assertEqual(result['code'],1223);start.assert_not_called()
            finally:jobs.close()

    def test_cancellation_event_schema_rejects_unknown_names(self):
        from modules.platform_services.update_elevation import validate_command_request
        request=self.request('Global'+chr(92)+'unknown')
        with self.assertRaises(ValueError):validate_command_request(request)
        request.pop('cancel_event');self.assertIs(validate_command_request(request),request)


if __name__=='__main__':unittest.main()
