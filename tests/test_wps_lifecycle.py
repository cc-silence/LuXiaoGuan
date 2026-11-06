"""Offline WPS UI lifecycle, native checkpoint and termination contracts."""
import unittest
from pathlib import Path,PurePosixPath
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace as NS
from unittest.mock import Mock,patch
from modules.capture_services.openwrt.wps_capture import OpenWrtWpsCapture
from modules.capture_services.openwrt.models import AccessPointInfo,CaptureCleanupError
from modules.capture_services.openwrt.wps_session import valid_session,prepare_session,checkpoint,session_name
from modules.capture_services.openwrt.wps_observation import observe

MAC='02:11:22:33:44:55'
def native():
    return '12\n3\n1\n'+'\n'.join(f'{n:04d}' for n in range(10000))+'\n'+'\n'.join(f'{n:03d}' for n in range(1000))+'\n'

class WpsLifecycleTests(unittest.TestCase):
    def run_target(self,folder,*,cancel=None,stop_error=False,timed=False,mode="normal"):
        runner,manager,workspace_manager=Mock(),Mock(),Mock()
        workspace=NS(local_dir=Path(folder),remote_dir='/tmp/owned')
        capture=OpenWrtWpsCapture(runner,manager)
        clock=[0.0]; updates=[]
        manager.is_running.return_value=timed or cancel is not None
        def tail(*args):
            if cancel is not None:cancel.set()
            return '[+] Trying pin "00000000"\n[!] WARNING: Detected AP rate limiting, waiting 60 seconds before re-checking'
        manager.read_log_tail.side_effect=tail
        if stop_error:manager.stop.side_effect=RuntimeError('controlled stop failure')
        def sleep(seconds):clock[0]+=60
        with patch.object(capture,'_base_args',return_value='reaver'),patch.object(capture,'_download_wpc',return_value='saved.wpc'),patch('modules.capture_services.openwrt.wps_capture.time.monotonic',side_effect=lambda:clock[0]),patch('modules.capture_services.openwrt.wps_capture.time.sleep',side_effect=sleep):
            result=capture.run_targets(workspace,'ownmon','own-session',(AccessPointInfo(MAC,ssid='offline',channel=1,wps_supported=True),),mode,{'attack_seconds':60,'attack_rounds':1},folder,workspace_manager,cancel,updates.append)
        return result,manager,updates

    def test_early_exit_reports_reason_and_native_status(self):
        with TemporaryDirectory() as folder:
            result,manager,updates=self.run_target(folder)
            self.assertIn('Reaver',result[0].note)
            self.assertIn('\u63d0\u524d\u9000\u51fa',result[0].note)
            self.assertIn('\u9650\u6d41',result[0].note)
            self.assertEqual(updates[-1].current_pin,'00000000')
            manager.stop.assert_called_once_with(manager.start.return_value,None,graceful_signal='INT')

    def test_time_budget_is_not_reported_as_exhausted_pin_space(self):
        with TemporaryDirectory() as folder:
            result,_,updates=self.run_target(folder,timed=True)
            self.assertIn('\u65f6\u9650',result[0].note)
            self.assertIn('\u4e0d\u4ee3\u8868',result[0].note)
            self.assertEqual(result[0].tried_seconds,60)
            self.assertTrue(any(update.current_pin=='00000000' for update in updates))

    def test_cancel_saves_before_raising_and_stop_failure_is_not_swallowed(self):
        with TemporaryDirectory() as folder:
            with self.assertRaisesRegex(RuntimeError,'\u8fdb\u5ea6\u5df2\u81ea\u52a8\u4fdd\u5b58'):
                self.run_target(folder,cancel=Event())
            with self.assertRaisesRegex(CaptureCleanupError,'controlled stop failure'):
                self.run_target(folder,stop_error=True)

    def test_offline_pin_uses_pixie_dust(self):
        with TemporaryDirectory() as folder:
            _,manager,_=self.run_target(folder,mode='offline')
        self.assertIn('-K 1',manager.start.call_args.args[1])
        self.assertNotIn(' -p ',manager.start.call_args.args[1])

    def test_unsupported_and_locked_targets_report_before_monitor_preparation(self):
        from modules.capture_services.backends.openwrt_backend import OpenWrtCaptureBackend
        with TemporaryDirectory() as folder:
            backend=object.__new__(OpenWrtCaptureBackend)
            backend.ssh_client=NS(connected=True);backend.reconnect_cleanup=Mock();backend.workspace_manager=Mock()
            backend.wps_capture=OpenWrtWpsCapture(Mock(),Mock())
            backend._find_scan_radio=Mock();backend._finish_capture_task=Mock()
            workspace=NS(local_dir=Path(folder),remote_dir='/tmp/owned')
            targets=(AccessPointInfo(MAC,ssid='offline',channel=1),AccessPointInfo('02:11:22:33:44:66',ssid='locked',channel=1,wps_supported=True,wps_locked=True))
            messages=[]
            with patch('modules.capture_services.backends.openwrt_backend.CaptureWorkspaceFactory.create',return_value=workspace):
                results=backend.run_wps('owned',{'resolved_output_dir':folder},targets,'normal',message_callback=lambda text,level:messages.append(text))
            self.assertEqual(len(results),2);backend._find_scan_radio.assert_not_called()
            self.assertTrue(any('\u672a\u68c0\u6d4b' in text for text in messages))
            self.assertTrue(any('\u9501\u5b9a' in text for text in messages))

    def test_result_credentials_are_never_status_candidates(self):
        self.assertEqual(observe('WPS PIN: 12345670\nWPA PSK: secret'),('',''))
        self.assertEqual(observe('[+] Trying pin "12345670"')[0],'12345670')
        self.assertEqual(observe('[+] Trying pin "123456789"')[0],'')

    def test_owned_native_format_auto_resume_and_atomic_rejection(self):
        from modules.capture_services.openwrt.result_converter import final_results_dir
        with TemporaryDirectory() as folder, patch("modules.capture_services.openwrt.wps_session.session_directory", side_effect=lambda: Path(folder)/"state"):
            workspace=NS(local_dir=Path(folder),remote_dir='/tmp/owned')
            manager=Mock();capture=OpenWrtWpsCapture(Mock(),Mock())
            remote=capture._build_order_wpc({'pin_order':'0123456789'},MAC,workspace,manager,None)
            local=Path(folder)/session_name(MAC)
            self.assertTrue(valid_session(local))
            self.assertEqual(local.read_text().splitlines()[:3],['0','0','0'])
            destination=Path(folder)/"state"/session_name(MAC)
            destination.parent.mkdir(parents=True,exist_ok=True)
            destination.write_text(native(),encoding='ascii')
            remote,loaded=prepare_session(workspace,manager,MAC,{},folder)
            self.assertTrue(loaded);self.assertEqual(remote,'/tmp/owned/'+session_name(MAC))
            manager.upload_atomic.assert_called_with(destination,PurePosixPath('/tmp/owned')/session_name(MAC))
            previous=destination.read_bytes()
            manager.download_atomic.side_effect=lambda remote,path:Path(path).write_text('broken',encoding='ascii')
            self.assertEqual(checkpoint(workspace,manager,MAC,folder),'')
            self.assertEqual(destination.read_bytes(),previous)
            self.assertFalse(destination.with_suffix('.wpc.download').exists())
            manager.download_atomic.side_effect=lambda remote,path:Path(path).write_text(native(),encoding='ascii')
            self.assertEqual(checkpoint(workspace,manager,MAC,folder),str(destination))
            _,loaded=prepare_session(workspace,manager,'02:11:22:33:44:66',{},folder)
            self.assertFalse(loaded)
            with self.assertRaises(ValueError):prepare_session(workspace,manager,MAC,{'resume_file':str(Path(folder)/'missing')},folder)

class WpsUiTests(unittest.TestCase):
    def test_same_buttons_stop_and_advanced_dialog_uses_common_controls(self):
        from tests.test_standalone import APP
        from modules.main_pages.packet_capture.openwrt.capture_tool_panel import CaptureToolPanel
        from modules.main_pages.packet_capture.openwrt.wps_dialog import AdvancedWpsDialog
        panel=CaptureToolPanel({})
        actions=[];advanced=[]
        panel.actionRequested.connect(actions.append);panel.advancedWpsRequested.connect(lambda:advanced.append(True))
        for entry,active,inactive,idle in (('quick',panel.wps_button,panel.advanced_wps_button,'\u9ad8\u7ea7 Pin'),('advanced',panel.advanced_wps_button,panel.wps_button,'\u5f00\u59cb\u79d2 Pin')):
            panel.set_action_availability(True,True,{'wps':True},'wps','capturing',wps_entry=entry)
            self.assertTrue(active.isEnabled());self.assertEqual(active.text(),'\u505c\u6b62 Pin')
            self.assertFalse(inactive.isEnabled());self.assertEqual(inactive.text(),idle)
            active.click()
        self.assertEqual(actions,['stop','stop']);self.assertFalse(advanced)
        panel.set_action_availability(True,True,{'wps':True},'wps','stopping')
        self.assertFalse(panel.wps_button.isEnabled());self.assertFalse(panel.advanced_wps_button.isEnabled())
        panel.set_action_availability(True,False,{'wps':True})
        panel.advanced_wps_button.click();self.assertEqual(advanced,[True])
        dialog=AdvancedWpsDialog({})
        self.assertTrue(dialog.ok_button.property('dialogAcceptButton'))
        self.assertGreaterEqual(dialog.ok_button.minimumWidth(),88)
        self.assertIn('QPushButton#settingsPrimaryButton',dialog.styleSheet())
        self.assertEqual(dialog.get_settings()['attack_seconds'],0)
        dialog.close();dialog.deleteLater();panel.deleteLater();APP.processEvents()

    def test_dictionary_drop_resize_and_mac_visual_order(self):
        from tests.test_standalone import APP
        from PySide6.QtCore import QMimeData,QUrl,Qt,QPointF
        from modules.main_pages.packet_capture.openwrt.password_dictionary_editor import PasswordDictionaryEditor
        from modules.main_pages.packet_capture.openwrt.access_point_table import AccessPointTable
        with TemporaryDirectory() as folder:
            a=Path(folder)/'one.txt';b=Path(folder)/'two.dic';a.write_text('offline');b.write_text('offline')
            editor=PasswordDictionaryEditor();mime=QMimeData();mime.setUrls([QUrl.fromLocalFile(str(a)),QUrl.fromLocalFile(str(b)),QUrl.fromLocalFile(folder),QUrl('https://example.com/no')])
            event=Mock();event.mimeData.return_value=mime
            editor.tree.dropEvent(event);editor.tree.dropEvent(event)
            self.assertEqual(editor.paths(),(str(a),str(b)))
            event.acceptProposedAction.assert_called()
            editor.resize(700,400);editor.show();APP.processEvents();before=editor.tree.height()
            handle=editor.resize_handle
            press=Mock();press.button.return_value=Qt.LeftButton;press.globalPosition.return_value=QPointF(10,100)
            handle.mousePressEvent(press)
            move=Mock();move.globalPosition.return_value=QPointF(10,150);handle.mouseMoveEvent(move)
            self.assertEqual(editor.tree.height(),before+50)
            editor.close();editor.deleteLater()
        table=AccessPointTable();header=table.horizontalHeader()
        self.assertEqual(header.visualIndex(table.BSSID_COLUMN),header.visualIndex(7)+1)
        table.deleteLater();APP.processEvents()
