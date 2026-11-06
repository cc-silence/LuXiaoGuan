"""Capture-ended directory refresh preserves file operations and device identity."""
from types import SimpleNamespace as NS
from unittest.mock import Mock
import unittest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QWidget
from tests.test_standalone import APP
from modules.main_pages.packet_capture.openwrt.capture_file_refresh import CaptureFileRefresh


class Events(QObject):
    radioStateChanged=Signal(str,str)
    completed=Signal(str,object)
    failed=Signal(str,str)


class CaptureFileRefreshTests(unittest.TestCase):
    def setUp(self):
        self.page=QWidget();self.addCleanup(self.page.deleteLater)
        self.service=Events();self.auto=Events();self.auto.is_busy=False
        self.service.owner=NS(auto_capture=self.auto)
        self.service.manager=NS(ssh=NS(_client=object(),connected=True))
        self.page.service=self.service;self.page.pending={}
        self.page.isVisible=Mock(return_value=True);self.page._refresh=Mock()
        self.refresh=CaptureFileRefresh(self.page);self.addCleanup(self.refresh.dispose)

    def test_terminal_event_refreshes_both_panes_without_reentering(self):
        self.auto.radioStateChanged.emit('radio0','capturing')
        self.page._refresh.assert_not_called()
        self.auto.radioStateChanged.emit('radio0','idle')
        self.assertEqual([c.args for c in self.page._refresh.call_args_list],[(False,),(True,)])

    def test_file_operation_defers_and_coalesces_repeated_requests(self):
        self.page.pending={'transfer':'download'}
        for _ in range(4):self.auto.radioStateChanged.emit('radio0','idle')
        self.page._refresh.assert_not_called()
        self.page.pending.clear();self.service.completed.emit('transfer',{})
        self.assertEqual(self.page._refresh.call_count,2)
        self.service.completed.emit('other',{})
        self.assertEqual(self.page._refresh.call_count,2)

    def test_hidden_page_waits_for_enter_and_busy_capture_waits_for_last_lane(self):
        self.page.isVisible.return_value=False
        self.auto.radioStateChanged.emit('radio0','idle')
        self.page._refresh.assert_not_called()
        self.page.isVisible.return_value=True;self.auto.is_busy=True
        self.refresh.flush();self.page._refresh.assert_not_called()
        self.auto.is_busy=False;self.auto.radioStateChanged.emit('radio1','error')
        self.assertEqual(self.page._refresh.call_count,2)

    def test_queued_terminal_event_is_harmless_after_dispose(self):
        import threading
        worker=threading.Thread(target=lambda:self.auto.radioStateChanged.emit('radio0','idle'))
        worker.start();worker.join(timeout=3)
        self.assertFalse(worker.is_alive())
        self.refresh.dispose()
        APP.processEvents()
        self.page._refresh.assert_not_called()

    def test_failed_file_operation_releases_one_pending_refresh(self):
        self.page.pending={'read':'remote'}
        self.auto.radioStateChanged.emit('radio0','error')
        self.page._refresh.assert_not_called()
        self.page.pending.clear()
        self.service.failed.emit('read','Permission denied')
        self.assertEqual(self.page._refresh.call_count,2)
        self.service.failed.emit('unrelated','Permission denied')
        self.assertEqual(self.page._refresh.call_count,2)

    def test_connection_change_or_dispose_discards_pending_refresh(self):
        self.page.pending={'read':'list'}
        self.auto.radioStateChanged.emit('radio0','idle')
        self.service.manager.ssh._client=object();self.page.pending.clear()
        self.service.failed.emit('read','failure');self.page._refresh.assert_not_called()
        self.refresh.dispose();self.auto.radioStateChanged.emit('radio0','idle')
        self.page._refresh.assert_not_called()
