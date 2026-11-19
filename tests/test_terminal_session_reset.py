"""Fresh-console admission, lifecycle, initial geometry and actual native input."""
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from PySide6.QtCore import Qt, QEvent
from PySide6.QtGui import QKeyEvent
from tests.test_terminal_runtime import Owner, APP
from tests import test_terminal_interaction as interaction
from modules.capture_services.router_terminal_service import RouterTerminalService, TerminalWorker
from modules.capture_services.openwrt.command_runner import RemoteCommandResult
from modules.capture_services.openwrt.terminal_session import session_command
from modules.main_pages.packet_capture.openwrt.router_terminal_page import RouterTerminalPage, ConfirmDialog


class TerminalSessionResetTests(unittest.TestCase):
    def make_service(self):
        owner=Owner()
        service=RouterTerminalService(owner)
        self.addCleanup(owner.deleteLater)
        return owner,service

    def old_worker(self,service):
        worker=TerminalWorker(service.ssh,service.ssh._client)
        service.worker=worker
        service.state='connected'
        service.session_mode='persistent'
        return worker

    def test_reset_command_cannot_attach_old_session_or_destroy_other_sessions(self):
        command=session_command(fresh=True)
        self.assertIn("kill-session -t '=winbox-terminal' || exit $?",command)
        self.assertNotIn('kill-server',command)
        self.assertNotIn('new-session -A',command)
        self.assertLess(command.index('kill-session'),command.index('exec tmux'))
        self.assertIn('new-session -A',session_command())

    def test_restart_waits_for_old_thread_then_creates_fresh_worker(self):
        owner,service=self.make_service();old=self.old_worker(service)
        service.resize(123,55)
        with patch.object(TerminalWorker,'start') as start:
            self.assertTrue(service.restart())
            self.assertTrue(old.cancel.is_set());start.assert_not_called()
            self.assertFalse(service.restart())
            service._finished()
            self.assertIsNot(service.worker,old)
            self.assertTrue(service.worker.fresh)
            self.assertTrue(service.worker.require_persistent)
            self.assertEqual(service.worker.dimensions,(123,55))
            start.assert_called_once()
        service.worker.deleteLater();service.worker=None

    def test_late_connected_signal_cannot_send_to_stopping_worker(self):
        owner,service=self.make_service();old=self.old_worker(service)
        service.restart()
        service._status('connected')
        self.assertEqual(service.state,'stopping')
        self.assertFalse(service.send('new-command\r'))
        self.assertEqual(list(old.commands),[])
        service.stop();service._finished()

    def test_explicit_disconnect_cancels_pending_restart(self):
        owner,service=self.make_service();self.old_worker(service)
        service.restart();service.stop()
        with patch.object(TerminalWorker,'start') as start:
            service._finished();start.assert_not_called()
        self.assertIsNone(service.worker)

    def test_device_change_or_shutdown_does_not_restart(self):
        for change in ('device','shutdown'):
            owner,service=self.make_service();self.old_worker(service);service.restart()
            if change=='device':service.ssh._client=object()
            else:owner._shutting_down=True
            with patch.object(TerminalWorker,'start') as start:
                service._finished();start.assert_not_called()
            self.assertIsNone(service.worker)

    def test_dimensions_received_before_start_are_used_for_initial_pty(self):
        owner,service=self.make_service();service.resize(119,61)
        channel=Mock();channel.recv.return_value=b''
        service.ssh.open_terminal=Mock(return_value=channel)
        with patch.object(TerminalWorker,'start'):
            service.start()
        worker=service.worker
        worker.run()
        self.assertEqual(service.ssh.open_terminal.call_args.kwargs,{'width':119,'height':61})
        worker.deleteLater();service.worker=None

    def test_resize_during_probe_is_applied_before_opening_pty(self):
        owner,service=self.make_service()
        channel=Mock();channel.recv.return_value=b''
        service.ssh.open_terminal=Mock(return_value=channel)
        runner=NS(run=Mock())
        worker=TerminalWorker(service.ssh,service.ssh._client,runner=runner)
        self.addCleanup(worker.deleteLater)
        def probe(*args,**kwargs):
            worker.resize(140,72)
            return RemoteCommandResult('terminal_tmux',0,'tmux 2.9a')
        runner.run.side_effect=probe
        worker.run()
        self.assertEqual(service.ssh.open_terminal.call_args.kwargs['width'],140)
        self.assertEqual(service.ssh.open_terminal.call_args.kwargs['height'],72)

    def test_required_reset_never_falls_back_to_plain_terminal_on_probe_failure(self):
        owner,service=self.make_service()
        service.ssh.open_terminal=Mock()
        runner=NS(run=Mock(return_value=RemoteCommandResult('terminal_tmux',1)))
        worker=TerminalWorker(service.ssh,service.ssh._client,runner=runner,fresh=True,require_persistent=True)
        self.addCleanup(worker.deleteLater)
        states=[];worker.status.connect(states.append)
        worker.run()
        self.assertIn('failed',states)
        service.ssh.open_terminal.assert_not_called()

    def test_file_run_never_sends_to_old_script_and_cancel_does_nothing(self):
        owner,service=self.make_service();old=self.old_worker(service)
        page=RouterTerminalPage(service)
        self.addCleanup(page.deleteLater)
        try:
            with patch.object(ConfirmDialog,'exec',return_value=ConfirmDialog.Rejected):
                self.assertFalse(page.open_path('/root/example.sh',True))
            self.assertFalse(old.cancel.is_set())
            with patch.object(ConfirmDialog,'exec',return_value=ConfirmDialog.Accepted):
                self.assertTrue(page.open_path('/root/example.sh',True))
            self.assertTrue(old.cancel.is_set())
            self.assertEqual(list(old.commands),[])
            self.assertIsNotNone(service._restart_command)
            with patch.object(TerminalWorker,'start'):
                service._finished()
            fresh=service.worker
            self.assertTrue(fresh.fresh)
            self.assertIn('example.sh', fresh.initial_command)
            self.assertEqual(list(fresh.commands),[])
            service._set_state('connected')
            self.assertEqual(list(fresh.commands),[])
            self.assertIsNone(service._restart_command)
        finally:
            page.dispose()
            if service.worker:service.worker.deleteLater();service.worker=None

    def test_startup_command_executes_once_and_rejects_newline(self):
        import subprocess
        from tests.posix_shell import local_shell
        from modules.capture_services.openwrt.terminal_session import startup_command
        shell=local_shell()
        self.assertIsNotNone(shell)
        command=startup_command("printf %s RM_STARTUP_ONCE")
        result=subprocess.run([str(shell),"-c",command],input=b"",capture_output=True,timeout=10)
        self.assertEqual(result.returncode,0)
        self.assertEqual(result.stdout.count(b"RM_STARTUP_ONCE"),2)
        with self.assertRaises(ValueError):
            startup_command("unsafe\nsecond-command")
        with self.assertRaises(ValueError):
            session_command(initial_command="echo bad",fresh=False)

    def test_actual_native_keys_forward_zero_enter_and_interrupt(self):
        case=interaction.TerminalInteractionTests();case.setUp()
        try:
            for key,mods,text in ((Qt.Key_0,Qt.NoModifier,'0'),
                                  (Qt.Key_Return,Qt.NoModifier,'\r'),
                                  (Qt.Key_C,Qt.ControlModifier,'\x03')):
                APP.sendEvent(case.view.terminal,QKeyEvent(QEvent.KeyPress,key,mods,text))
            self.assertEqual(''.join(case.bridge.sent),'0\r\x03')
        finally:case.tearDown()
