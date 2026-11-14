"""Automatic capture messages must not enter the ordinary capture information pane."""
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from PySide6.QtTest import QSignalSpy
from tests import test_standalone as standalone
from modules.log_history import LogHistory, HistoryMasker
from modules.log_history_export import history_attachments
from modules.main_pages.packet_capture.openwrt.auto_capture_page import AutoCapturePage
APP = standalone.APP


class CaptureMessageRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        standalone.StandaloneTests.setUpClass()
        cls.service = standalone.StandaloneTests.runtime.capture_service
        cls.page = standalone.StandaloneTests.window.packet_capture_page

    @classmethod
    def tearDownClass(cls):
        standalone.StandaloneTests.tearDownClass()

    def test_auto_messages_do_not_reach_regular_information_pane(self):
        regular = QSignalSpy(self.service.messageGenerated)
        automatic = QSignalSpy(self.service.auto_capture.messageGenerated)
        self.service.auto_capture.messageGenerated.emit('AUTO_ONLY_ROUTING_CHECK', 'info')
        APP.processEvents()
        self.assertEqual(automatic.count(), 1)
        self.assertEqual(regular.count(), 0)
        self.assertNotIn('AUTO_ONLY_ROUTING_CHECK', self.page.info_panel.info_box.toPlainText())
        self.service.messageGenerated.emit('REGULAR_ONLY_ROUTING_CHECK', 'info')
        APP.processEvents()
        self.assertEqual(regular.count(), 1)
        self.assertEqual(automatic.count(), 1)
        self.assertIn('REGULAR_ONLY_ROUTING_CHECK', self.page.info_panel.info_box.toPlainText())

    def test_auto_install_and_upgrade_notices_use_auto_channel(self):
        regular = QSignalSpy(self.service.messageGenerated)
        automatic = QSignalSpy(self.service.auto_capture.messageGenerated)
        owner = NS(service=self.service, auto_capture_service=self.service.auto_capture,
                   _upgrade_progress=NS(start=Mock(return_value=True)),
                   _install_progress=NS(start=Mock(return_value=True)))
        AutoCapturePage._on_upgrade_hcx_clicked(owner)
        AutoCapturePage._on_install_dependency_clicked(owner)
        self.assertEqual(automatic.count(), 2)
        self.assertEqual(regular.count(), 0)

    def test_auto_message_history_stays_separate_and_redacted(self):
        with tempfile.TemporaryDirectory() as folder:
            history = LogHistory(folder, 'routing-test')
            try:
                with patch('modules.capture_services.local_capture_diagnostics.current_history', return_value=history):
                    self.service.auto_capture.messageGenerated.emit('AUTO_HISTORY_RETAINED', 'info')
                    self.service.auto_capture.messageGenerated.emit('password=secret-routing-value', 'info')
                history.flush()
            finally:
                history.close()
            history = LogHistory(folder, 'next-run')
            try:
                automatic = history_attachments(history, 'auto-capture', None, HistoryMasker())
                regular = history_attachments(history, '\u6293\u5305', None, HistoryMasker())
                auto_bytes = b'\n'.join(data for name, data in automatic)
                regular_bytes = b'\n'.join(data for name, data in regular)
                self.assertIn(b'AUTO_HISTORY_RETAINED', auto_bytes)
                self.assertNotIn(b'AUTO_HISTORY_RETAINED', regular_bytes)
                self.assertNotIn(b'secret-routing-value', auto_bytes)
            finally:
                history.close()
