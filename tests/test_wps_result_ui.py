"""Concise result wording, persistent detail, conditional MAC settings and warning colors."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
import zipfile
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QWidget
from tests.test_standalone import APP
from modules.capture_services.openwrt.wps_models import WpsTargetResult, WpsUpdate
from modules.capture_services.openwrt.wps_result_text import result_message
from modules.capture_services.capture_service import CaptureService
from modules.capture_services.openwrt.models import OpenWrtSessionSnapshot, CaptureTaskType
from modules.main_pages.packet_capture.openwrt.wps_dialog import AdvancedWpsDialog
from modules.main_pages.packet_capture.openwrt.task_progress import CaptureTaskProgress
from modules.main_pages.packet_capture.capture_page_styles import CapturePageStyleManager
from modules.ui_scale_manager import UiScaleManager


class WpsResultUiTests(unittest.TestCase):
    def test_user_example_is_short_and_native_note_remains_intact(self):
        note="\u5df2\u8fbe\u5230\u8bbe\u5b9a\u7684\u8fd0\u884c\u65f6\u9650\uff1b\u76ee\u6807\u9650\u6d41\u6216 WPS \u9501\u5b9a\uff1b\u4ea4\u4e92\u8bc1\u636e\uff1a\u6536\u5230 M1\u3001\u6536\u5230 M3\u3001\u6536\u5230 WSC NACK\uff1b\u5019\u9009 PIN 00020008"
        entry=WpsTargetResult('02:11:22:33:44:55','fixture',tried_seconds=60,wpc_file='saved.wpc',note=note)
        text,level=result_message(entry)
        self.assertEqual(text,"\u672a\u62ff\u5230 PIN\uff0c\u8def\u7531\u5668\u6682\u65f6\u9650\u5236\u4e86\u5c1d\u8bd5\u3002\u8fdb\u5ea6\u5df2\u4fdd\u5b58\u3002")
        self.assertEqual(level,'warning')
        self.assertEqual(entry.note,note)
        for token in ['M1','M3','NACK','00020008']:self.assertNotIn(token,text)
        pin_text,_=result_message(replace(entry,pin_ok=True))
        self.assertIn('\u6682\u672a\u53d6\u5f97',pin_text)
        self.assertNotIn('M1',pin_text)
        cleanup,_=result_message(replace(entry,note=note+'\uff1b\u6e05\u7406\u5f02\u5e38'))
        self.assertIn('\u672a\u6e05\u7406\u5b8c\u6210',cleanup)

    def test_detailed_result_survives_export_outside_user_information(self):
        from modules.log_history import LogHistory,install_history
        from modules.log_services import export_logs
        from modules.capture_services.backends.openwrt_backend import _emit_wps_message
        with TemporaryDirectory() as folder:
            store=LogHistory(Path(folder)/'history');install_history(store)
            try:
                callback=Mock();detail='\u4ea4\u4e92\u8bc1\u636e\uff1a\u6536\u5230 M1\u3001\u6536\u5230 M3'
                _emit_wps_message(callback,WpsTargetResult('02:11:22:33:44:55','fixture',tried_seconds=60,note=detail))
                self.assertNotIn('M1',callback.call_args.args[0])
                output=Path(folder)/'feedback.zip';export_logs(output,'\u6293\u5305',[])
                with zipfile.ZipFile(output) as archive:
                    self.assertIn('M1',archive.read('current-run/wps-result-details.txt').decode('utf-8'))
            finally:install_history(None);store.close()

    def test_mac_controls_only_show_below_enabled_switch_in_both_themes(self):
        out=Path('build/wps-result-ui-review');out.mkdir(parents=True,exist_ok=True)
        for light in (True,False):
            parent=QWidget();parent.scale_manager=UiScaleManager(100)
            parent.setStyleSheet('QWidget {background:#f5f7fa;}' if light else 'QWidget {background:#181b1f;}')
            dialog=AdvancedWpsDialog(dict(spoof_mac_enabled=False,spoof_mac='02:AA:BB:CC:DD:EE'),parent)
            try:
                dialog.show();APP.processEvents()
                self.assertGreater(dialog.spoof_mac_enabled.mapToGlobal(dialog.spoof_mac_enabled.rect().topLeft()).y(),dialog.random_order.mapToGlobal(dialog.random_order.rect().topLeft()).y())
                self.assertTrue(dialog.spoof_mac_row.isHidden())
                self.assertTrue(dialog.spoof_mac_form_row.itemAt(0).widget().isHidden())
                self.assertTrue(dialog.grab().save(str(out/('light-off.png' if light else 'dark-off.png'))))
                dialog.spoof_mac_enabled.setChecked(True);APP.processEvents()
                self.assertTrue(dialog.spoof_mac_row.isVisible())
                self.assertGreater(dialog.spoof_mac_row.mapToGlobal(dialog.spoof_mac_row.rect().topLeft()).y(),dialog.spoof_mac_enabled.mapToGlobal(dialog.spoof_mac_enabled.rect().topLeft()).y())
                dialog.rotate_mac_each_pin.setChecked(True)
                dialog.spoof_mac_enabled.setChecked(False)
                self.assertFalse(dialog.get_settings()['rotate_mac_each_pin'])
                dialog.spoof_mac_enabled.setChecked(True);APP.processEvents()
                self.assertTrue(dialog.grab().save(str(out/('light-on.png' if light else 'dark-on.png'))))
            finally:dialog.close();dialog.deleteLater();parent.deleteLater();APP.processEvents()

    def test_only_lock_status_is_red_and_stale_warning_clears(self):
        out=Path('build/wps-result-ui-review');out.mkdir(parents=True,exist_ok=True)
        status='\u76ee\u6807\u9650\u6d41\u6216 WPS \u9501\u5b9a'
        for light,color in [(True,'#c62828'),(False,'#ff7070')]:
            parent=QWidget();parent.scale_manager=UiScaleManager(100);parent.setObjectName('packetCapturePage')
            styles=CapturePageStyleManager(parent)
            background='#f5f7fa' if light else '#181b1f'
            parent.setStyleSheet('QWidget#packetCapturePage { background:'+background+'; }'+styles._capture_stylesheet(light))
            widget=CaptureTaskProgress(parent);widget.resize(1150,40)
            service=NS(openwrt_backend=NS(snapshot=OpenWrtSessionSnapshot(task_type=CaptureTaskType.WPS,task_state='capturing')),openwrtSnapshotChanged=Mock())
            try:
                update=WpsUpdate(20,60,current_pin='00020008',status=status,candidate_elapsed=3,average_per_minute=2)
                CaptureService._on_openwrt_wps_progress(service,update)
                progress=service.openwrt_backend.snapshot.progress
                self.assertNotIn(status,progress.title)
                self.assertEqual(progress.warning_text,status)
                widget.apply_progress(progress,'capturing');parent.resize(1170,60);parent.show();APP.processEvents()
                self.assertEqual(widget.warning_label.palette().color(QPalette.WindowText).name(),color)
                self.assertNotEqual(widget.title_label.palette().color(QPalette.WindowText).name(),color)
                self.assertTrue(widget.grab().save(str(out/('status-light.png' if light else 'status-dark.png'))))
                widget.apply_progress(replace(progress,warning_text=''),'capturing')
                self.assertTrue(widget.warning_label.isHidden())
                widget.apply_progress(progress,'idle')
                self.assertEqual(widget.warning_label.text(),'')
            finally:parent.close();parent.deleteLater();APP.processEvents()
