"""Real Qt button/export integration; no router commands are executed."""
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch
import zipfile
from PySide6.QtWidgets import QApplication
from modules.log_history import LogHistory, install_history
from modules.main_pages.info_panel import InfoPanel
from modules.capture_services.openwrt.auto_capture_service import AutoCaptureService
from modules.capture_services.openwrt.models import OpenWrtSessionSnapshot
from modules.main_pages.packet_capture.openwrt.auto_capture_page import AutoCapturePage

APP = QApplication.instance() or QApplication([])

class LogHistoryUiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=LogHistory(Path(self.tmp.name)/'history',version='test')
        install_history(self.store)
        self.widgets=[]

    def tearDown(self):
        install_history(None)
        for widget in self.widgets:
            if hasattr(widget,'dispose'):widget.dispose()
            widget.close()
            widget.deleteLater()
        APP.processEvents()
        self.store.close()
        self.tmp.cleanup()

    def content(self,category):
        return '\n'.join(row[0] for run in self.store.snapshot(category)['runs'] for row in run['records'])

    def test_clear_and_page_rebuild_do_not_erase_history(self):
        panel=InfoPanel(log_category='ui-page')
        self.widgets.append(panel)
        panel.append_info('first page event')
        panel.clear()
        self.assertEqual(panel.export_entries(),())
        another=InfoPanel(log_category='ui-page')
        self.widgets.append(another)
        another.append_info('rebuilt page event')
        self.assertIn('first page event',self.content('ui-page'))
        self.assertIn('rebuilt page event',self.content('ui-page'))

    def test_auto_button_click_uses_common_worker_and_leaves_background_untouched(self):
        service=MagicMock()
        service.settings={}
        service.openwrt_snapshot=OpenWrtSessionSnapshot.initial()
        service.auto_capture=AutoCaptureService(service)
        page=AutoCapturePage(service,{})
        self.widgets.append(page)
        layout=page.guard_card.layout()
        self.assertEqual(layout.indexOf(page.export_logs_btn),layout.indexOf(page.open_output_dir_btn)+1)
        service.auto_capture.diagnostics.begin('radio1')
        service.auto_capture.radioStreamOutput.emit('radio1','initialization failed\n')
        service.auto_capture.bootLogUpdated.emit('background-must-stay-out')
        page._clear_bg_console()
        output=Path(self.tmp.name)/'auto.zip'
        context=json.dumps(dict(schema_version=1,connection_state='disconnected',device=None))
        with patch('modules.main_pages.packet_capture.openwrt.auto_capture_log_export.context_for',return_value=context), \
             patch('PySide6.QtWidgets.QFileDialog.getSaveFileName',return_value=(str(output),'ZIP')), \
             patch('modules.common_widgets.common_dialogs.MessageDialog.exec',return_value=0):
            page.export_logs_btn.click()
            deadline=time.monotonic()+10
            while page.window().log_export_service.active and time.monotonic()<deadline:
                APP.processEvents()
                time.sleep(.005)
            APP.processEvents()
        self.assertTrue(output.is_file())
        with zipfile.ZipFile(output) as archive:
            content='\n'.join(archive.read(name).decode() for name in archive.namelist())
        self.assertIn('initialization failed',content)
        self.assertNotIn('background-must-stay-out',content)
        service.openwrt_backend.command_runner.run.assert_not_called()

    def test_automatic_recorder_keeps_origin_after_router_switch(self):
        from dataclasses import replace
        owner=MagicMock()
        owner.openwrt_snapshot=replace(OpenWrtSessionSnapshot.initial(),host_fingerprint='device-a')
        owner.auto_capture=AutoCaptureService(owner)
        recorder=owner.auto_capture.diagnostics
        recorder.begin('radio1')
        origin=recorder.device
        owner.openwrt_snapshot=replace(owner.openwrt_snapshot,host_fingerprint='device-b')
        recorder.snapshot_changed(owner.openwrt_snapshot)
        owner.auto_capture.radioStreamOutput.emit('radio1','old cleanup failure\n')
        content='\n'.join(row[0] for run in self.store.snapshot('auto-capture',origin)['runs'] for row in run['records'])
        self.assertIn('old cleanup failure',content)
        current='\n'.join(row[0] for run in self.store.snapshot('auto-capture',recorder.device)['runs'] for row in run['records'])
        self.assertNotIn('old cleanup failure',current)
        owner.auto_capture.deleteLater()

if __name__=='__main__':unittest.main()
