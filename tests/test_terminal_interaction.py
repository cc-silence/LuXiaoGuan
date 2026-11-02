"""Real native component regressions; no router or shell is started."""
import base64
import json
import os
import unittest
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QObject, Signal, Qt, QPoint, QPointF, QEvent, QCoreApplication
from PySide6.QtGui import QWheelEvent, QMouseEvent
from PySide6.QtWidgets import QApplication, QScrollBar
from modules.terminal_widgets.native_view import NativeTerminalView

APP=QApplication.instance() or QApplication([])

class Bridge(QObject):
    output=Signal(str)
    control=Signal(str)
    def __init__(self):
        super().__init__();self.sent=[];self.copied=[];self.zoomed=[]
    def input(self,value): self.sent.append(value)
    def resize(self,*value): pass
    def ack(self,value): pass
    def selectionChanged(self,value): pass
    def copy(self,value): self.copied.append(value)
    def zoom(self,value): self.zoomed.append(value)

class TerminalInteractionTests(unittest.TestCase):
    def setUp(self):
        self.bridge=Bridge();self.view=NativeTerminalView(self.bridge)
        self.view.setAttribute(Qt.WA_DontShowOnScreen)
        self.view.resize(900,420);self.view.show();APP.processEvents()
        self.control(action='state',connected=True)
        self.view._dll.wb_colors(self.view._pointer,0xff00ff00,0xff202020)
        self.feed(b'\x1b[?25l')
    def tearDown(self):
        self.view.dispose();self.view.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
    def control(self,**value):
        self.bridge.control.emit(json.dumps(value));APP.processEvents()
    def feed(self,data):
        self.bridge.output.emit(base64.b64encode(data).decode('ascii'));APP.processEvents()
    def wheel(self,delta=120,modifiers=Qt.NoModifier):
        point=QPoint(10,10)
        event=QWheelEvent(QPointF(point),QPointF(self.view.terminal.mapToGlobal(point)),
            QPoint(),QPoint(0,delta),Qt.NoButton,modifiers,Qt.NoScrollPhase,False)
        APP.sendEvent(self.view.terminal,event);APP.processEvents()
    def test_wide_history_restores_background_after_font_resize(self):
        line='\u4f1a\u8bdd\u65e5\u5fd7 /root/cap/ \u7ed3\u675f '*4
        self.feed(('\x1b[32m'+(line+'\r\n')*100+'\x1b[0m').encode('utf-8'))
        for size in (26,12,20,10,16,8,28,14):
            self.control(action='theme',fontSize=size)
        image=self.view.terminal.grab().toImage()
        # A green glyph on the configured grey background can never paint black.
        # Old libvterm lost the pen of every wide continuation pulled from history.
        black=sum(image.pixelColor(x,y).rgb()==0xff000000
                  for y in range(image.height()) for x in range(image.width()-25))
        self.assertEqual(black,0,'Wide continuation cells painted an uninitialised background')
    def test_plain_wheel_scrolls_history_without_shell_input(self):
        self.feed(b'\r\n'.join(('line%03d'%i).encode() for i in range(100)))
        bar=self.view.terminal.findChild(QScrollBar);before=bar.value()
        self.wheel()
        self.assertLess(bar.value(),before);self.assertEqual(self.bridge.sent,[])
    def test_tmux_wheel_reports_mouse_but_ctrl_wheel_only_zooms(self):
        self.feed(b'\x1b[?1049h\x1b[?1000h\x1b[?1006h')
        self.wheel()
        self.assertTrue(any(value.startswith('\x1b[<64;') for value in self.bridge.sent))
        self.bridge.sent.clear();self.wheel(modifiers=Qt.ControlModifier)
        self.assertEqual(self.bridge.sent,[]);self.assertEqual(self.bridge.zoomed,[1])
    def test_tmux_mouse_mode_keeps_local_selection(self):
        self.feed(b'\x1b[?1049h\x1b[?1000h\x1b[?1006hselect this text')
        for kind,point,button,buttons in (
            (QEvent.MouseButtonPress,QPoint(2,5),Qt.LeftButton,Qt.LeftButton),
            (QEvent.MouseMove,QPoint(85,5),Qt.NoButton,Qt.LeftButton),
            (QEvent.MouseButtonRelease,QPoint(85,5),Qt.LeftButton,Qt.NoButton)):
            APP.sendEvent(self.view.terminal,QMouseEvent(kind,QPointF(point),button,buttons,Qt.NoModifier))
        APP.processEvents()
        self.assertIn('select',self.view.terminal_text(True))
        self.assertEqual(self.bridge.sent,[])
    def test_bell_does_not_flash_display(self):
        self.feed(b'unchanged')
        before=self.view.terminal.grab().toImage()
        self.feed(b'\a')
        self.assertEqual(self.view.terminal.grab().toImage(),before)
    def test_plain_clear_does_not_reappear_on_scroll_or_resize(self):
        self.feed(b'\r\n'.join(b'old-content' for _ in range(100)))
        self.control(action='clear');self.wheel();self.wheel(-120)
        self.control(action='theme',fontSize=18)
        self.assertNotIn('old-content',self.view.terminal_text())
        self.assertEqual(self.bridge.sent,[])


class TerminalClearServiceTests(unittest.TestCase):
    def make_worker(self,result=None):
        from types import SimpleNamespace as NS
        from unittest.mock import Mock
        from modules.capture_services.router_terminal_service import TerminalWorker
        from modules.capture_services.openwrt.command_runner import RemoteCommandResult
        client=object();ssh=NS(connected=True,_client=client)
        runner=NS(run=Mock(return_value=result or RemoteCommandResult('terminal_clear',0)))
        worker=TerminalWorker(ssh,client,runner=runner)
        self.addCleanup(worker.deleteLater)
        return worker,runner
    def test_remote_clear_uses_control_channel_without_shell_input(self):
        worker,runner=self.make_worker();done=[]
        worker.clearFinished.connect(lambda *value:done.append(value))
        worker._clear_persistent_screen()
        self.assertEqual(done,[(True,'')]);self.assertEqual(list(worker.commands),[])
        name,command=runner.run.call_args.args
        self.assertEqual(name,'terminal_clear')
        self.assertIn('clear-history -t "$wb_pane"',command)
        self.assertIn("display-message -p -t '=winbox-terminal:'",command)
        self.assertIn('send-keys -R -t "$wb_pane"',command)
        self.assertNotIn('C-l',command)
        self.assertEqual(runner.run.call_args.kwargs['timeout'],10)
        self.assertIs(runner.run.call_args.kwargs['cancel_event'],worker.cancel)
    def test_failure_timeout_cancellation_and_device_change_are_not_success(self):
        from modules.capture_services.openwrt.command_runner import RemoteCommandResult
        for result in (RemoteCommandResult('terminal_clear',1),
                       RemoteCommandResult('terminal_clear',0,timed_out=True),
                       RemoteCommandResult('terminal_clear',0,cancelled=True)):
            with self.subTest(result=result):
                worker,_=self.make_worker(result);done=[]
                worker.clearFinished.connect(lambda *value:done.append(value))
                worker._clear_persistent_screen();self.assertFalse(done[0][0])
        worker,runner=self.make_worker();done=[]
        worker.ssh._client=object();worker.clearFinished.connect(lambda *value:done.append(value))
        worker._clear_persistent_screen();runner.run.assert_not_called()
        self.assertEqual(done,[(False,'ConnectionError')])
    def test_pending_clear_is_bounded_and_page_clears_only_after_success(self):
        from unittest.mock import patch
        from tests.test_terminal_runtime import Owner
        from modules.capture_services.router_terminal_service import RouterTerminalService
        from modules.main_pages.packet_capture.openwrt.router_terminal_page import RouterTerminalPage
        owner=Owner();service=RouterTerminalService(owner)
        worker,_=self.make_worker();service.worker=worker
        service.state='connected';service.session_mode='persistent'
        worker.clearFinished.connect(service._clear_finished)
        page=RouterTerminalPage(service)
        try:
            with patch.object(page,'control') as control:
                page.clear_terminal();self.assertTrue(service.clear_pending)
                self.assertTrue(worker.clear_requested.is_set())
                self.assertFalse(page.clear_button.isEnabled())
                self.assertFalse(service.clear_screen());control.assert_not_called()
                worker.clearFinished.emit(False,'Timeout')
                self.assertFalse(service.clear_pending);control.assert_not_called()
                page.clear_terminal();worker.clearFinished.emit(True,'')
                control.assert_called_once_with(action='clear')
                self.assertTrue(page.clear_button.isEnabled())
        finally:
            page.dispose();page.deleteLater();service.worker=None;owner.deleteLater()

    def test_user_disconnect_clears_local_view_only_after_worker_finishes(self):
        from unittest.mock import patch
        from tests.test_terminal_runtime import Owner
        from modules.capture_services.router_terminal_service import RouterTerminalService
        from modules.main_pages.packet_capture.openwrt.router_terminal_page import RouterTerminalPage,ConfirmDialog
        owner=Owner();service=RouterTerminalService(owner)
        worker,runner=self.make_worker();service.worker=worker
        service.state='connected';service.session_mode='persistent'
        worker.output.connect(service.output)
        page=RouterTerminalPage(service);page._load_view()
        try:
            worker.output.emit(base64.b64encode(b'old-display').decode('ascii'))
            with patch.object(ConfirmDialog,'exec',return_value=ConfirmDialog.Rejected):
                page.close_terminal()
            self.assertFalse(worker.cancel.is_set())
            self.assertIn('old-display',page.view.terminal_text())
            with patch.object(ConfirmDialog,'exec',return_value=ConfirmDialog.Accepted):
                page.close_terminal()
            self.assertEqual(service.state,'stopping')
            self.assertIn('old-display',page.view.terminal_text())
            worker.output.emit(base64.b64encode(b'last-output-before-close').decode('ascii'))
            self.assertIn('last-output-before-close',page.view.terminal_text())
            service._finished()
            self.assertEqual(service.state,'closed')
            self.assertEqual(page.view.terminal_text().strip(),'')
            self.assertTrue(service.ssh.connected)
            self.assertFalse(worker.clear_requested.is_set())
            runner.run.assert_not_called()
        finally:
            page.dispose();page.deleteLater();service.worker=None;owner.deleteLater()

    def test_new_channel_resets_old_buffer_but_status_refresh_does_not(self):
        from tests.test_terminal_runtime import Owner
        from modules.capture_services.router_terminal_service import RouterTerminalService
        from modules.main_pages.packet_capture.openwrt.router_terminal_page import RouterTerminalPage
        owner=Owner();service=RouterTerminalService(owner)
        first,_=self.make_worker();service.worker=first;service.state='connected'
        page=RouterTerminalPage(service);page._load_view()
        try:
            service.output.emit(base64.b64encode(b'old-PIN-record').decode('ascii'))
            page._status('connected');self.assertIn('old-PIN-record',page.view.terminal_text())
            page._status('input_full');page._status('connected')
            self.assertIn('old-PIN-record',page.view.terminal_text())
            second,_=self.make_worker();service.worker=second
            page._status('connected')
            self.assertEqual(page.view.terminal_text().strip(),'')
            service.output.emit(base64.b64encode(b'current-capture').decode('ascii'))
            self.assertIn('current-capture',page.view.terminal_text())
            self.assertNotIn('old-PIN-record',page.view.terminal_text())
        finally:
            page.dispose();page.deleteLater();service.worker=None;owner.deleteLater()
    def test_reconnect_uses_live_tmux_stream_without_another_panes_snapshot(self):
        from unittest.mock import Mock
        from modules.capture_services.openwrt.command_runner import RemoteCommandResult
        worker,runner=self.make_worker(RemoteCommandResult('terminal_tmux',0,'tmux 3.3a'))
        channel=Mock();channel.recv.side_effect=[b'current-capture',b'']
        worker.ssh.open_terminal=Mock(return_value=channel)
        outputs=[];worker.output.connect(lambda value:outputs.append(base64.b64decode(value)))
        worker.run()
        self.assertEqual(outputs,[b'current-capture']);self.assertEqual(runner.run.call_count,1)
        self.assertEqual(runner.run.call_args.args[0],'terminal_tmux')
        self.assertIn('new-session -A -s winbox-terminal',worker.ssh.open_terminal.call_args.kwargs['command'])
        channel.close.assert_called_once_with()
    def test_clear_completion_checks_cancellation_and_identity_again(self):
        from modules.capture_services.openwrt.command_runner import RemoteCommandResult
        for change in ('cancel','device'):
            worker,runner=self.make_worker();done=[]
            def complete(*args,**kwargs):
                if change=='cancel':worker.cancel.set()
                else:worker.ssh._client=object()
                return RemoteCommandResult('terminal_clear',0)
            runner.run.side_effect=complete
            worker.clearFinished.connect(lambda *value:done.append(value))
            worker._clear_persistent_screen();self.assertFalse(done[0][0])

    def test_clear_returns_keyboard_focus_to_connected_terminal(self):
        from tests.test_terminal_runtime import Owner
        from modules.capture_services.router_terminal_service import RouterTerminalService
        from modules.main_pages.packet_capture.openwrt.router_terminal_page import RouterTerminalPage
        owner=Owner();service=RouterTerminalService(owner)
        worker,_=self.make_worker();service.worker=worker;service.state='connected';service.session_mode='persistent'
        worker.clearFinished.connect(service._clear_finished)
        page=RouterTerminalPage(service);page._load_view()
        try:
            page.clear_button.setFocus()
            self.assertIs(page.focusWidget(),page.clear_button)
            page.clear_terminal();worker.clearFinished.emit(True,'')
            self.assertIs(page.focusWidget(),page.view.terminal)
            service._set_state('closed');page.clear_button.setFocus()
            page._reset_display()
            self.assertIs(page.focusWidget(),page.clear_button)
        finally:
            page.dispose();page.deleteLater();service.worker=None;owner.deleteLater()
