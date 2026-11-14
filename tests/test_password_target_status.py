"""Per-target outcomes, cancelled batches, history skips and active-row following."""
import json,unittest
from dataclasses import replace,asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
from unittest.mock import Mock,patch
from PySide6.QtCore import QObject,Signal,Qt
from PySide6.QtWidgets import QWidget,QVBoxLayout
from tests.test_file_selection import APP
from modules.capture_services.capture_service import CaptureService
from modules.capture_services.models import CaptureSessionState
from modules.capture_services.openwrt.models import AccessPointInfo,OpenWrtSessionSnapshot,CaptureTaskType,AccessPointCaptureState
from modules.capture_services.openwrt.password_events import PasswordFoundNotice,PasswordTargetUpdate,target_update_for
from modules.capture_services.openwrt.password_verification import PasswordTargetResult,OpenWrtPasswordVerifier,PasswordVerifyCancelled,PasswordVerifyUpdate
from modules.capture_services.openwrt.password_reporting import PasswordVerificationReporter
from modules.capture_services.openwrt.password_history import PasswordAttemptHistory
from modules.capture_services.password_target_status import begin,finish,apply_update,scan_states,restore_scan,complete
from modules.main_pages.packet_capture.openwrt.access_point_table import AccessPointTable
from modules.ui_scale_manager import UiScaleManager

POINTS=tuple(AccessPointInfo('02:11:22:33:44:'+format(i,'02X'),ssid='Target '+str(i),encryption='WPA2-PSK',selected=True) for i in range(1,7))
class Service(QObject):
    _publish_failure = CaptureService._publish_failure
    openwrtSnapshotChanged=Signal(object)
    passwordFound=Signal(object)
    def __init__(self):
        super().__init__();self._shutting_down=False
        self.openwrt_backend=NS(snapshot=OpenWrtSessionSnapshot(access_points=POINTS,task_type=CaptureTaskType.PASSWORD_VERIFY,task_state='capturing'))
        self.messageGenerated=Mock();self._notify_task_result=Mock();self._maybe_auto_open_dir=Mock();self.task_controller=Mock()
        self.password_verification_candidate=''
        self._task_failure_snapshot=lambda summary:replace(self.openwrt_backend.snapshot,last_error=summary,task_state='idle',capturing_bssids=())
    def progress(self,update):CaptureService._on_openwrt_password_progress(self,update)

class TargetStatusTests(unittest.TestCase):
    def setUp(self):self.service=Service();begin(self.service,POINTS)
    def state(self,index):return self.service.openwrt_backend.snapshot.access_points[index].password_state
    def test_found_updates_before_batch_finishes_and_never_becomes_capture_success(self):
        event=PasswordFoundNotice(POINTS[0].ssid,POINTS[0].bssid,'result.txt','private-fixture')
        self.service.progress(event)
        self.assertEqual(self.state(0),'found')
        self.assertEqual(self.service.openwrt_backend.snapshot.access_points[0].capture_state,AccessPointCaptureState.PENDING)
        self.assertNotIn('private-fixture',json.dumps(asdict(self.service.openwrt_backend.snapshot),default=str))
        self.assertEqual(self.service.openwrt_backend.snapshot.task_state,'capturing')
    def test_every_target_has_explicit_end_state_after_cancellation(self):
        for i,state in enumerate(('found','timeout','verifying','round_pending','unconfirmed')):
            self.service.progress(PasswordTargetUpdate(POINTS[i].ssid,POINTS[i].bssid,state))
        CaptureService._on_openwrt_password_failed(self.service,'\u5728\u7ebf\u5bc6\u7801\u9a8c\u8bc1\u5df2\u53d6\u6d88\u3002','')
        self.assertEqual([self.state(i) for i in range(6)],['found','timeout','cancelled','cancelled','unconfirmed','not_started'])
        self.service._notify_task_result.assert_called_once()
        self.assertIn('\u5386\u53f2',target_update_for(POINTS[0],PasswordTargetResult(POINTS[0].bssid,POINTS[0].ssid,False,0,4,skipped=4)).note)
    def test_history_skip_and_pending_next_dictionary_are_distinct_from_success(self):
        result=PasswordTargetResult(POINTS[0].bssid,POINTS[0].ssid,False,0,4,skipped=4)
        self.assertEqual(target_update_for(POINTS[0],result).state,'history_skipped')
        self.assertEqual(target_update_for(POINTS[0],replace(result,final=False)).state,'round_pending')
        self.assertEqual(target_update_for(POINTS[0],replace(result,error=True,outcome='timeout')).state,'timeout')
        self.assertEqual(target_update_for(POINTS[0],replace(result,error=True,outcome='unconfirmed')).state,'unconfirmed')
    def test_found_survives_later_start_or_cancel_events(self):
        for state in ('found','verifying','cancelled','no_match'):
            apply_update(self.service,PasswordTargetUpdate(POINTS[0].ssid,POINTS[0].bssid,state))
        self.assertEqual(self.state(0),'found')
    def test_scan_keeps_same_identity_result_and_rejects_changed_ssid(self):
        apply_update(self.service,PasswordTargetUpdate(POINTS[0].ssid,POINTS[0].bssid,'timeout','safe-note'))
        states=scan_states(self.service.openwrt_backend.snapshot.access_points)
        self.assertEqual(restore_scan(POINTS[0],states).password_state,'timeout')
        self.assertEqual(restore_scan(replace(POINTS[0],ssid='different'),states).password_state,'')
    def test_final_results_reconcile_row_states_even_without_progress_delivery(self):
        entry=PasswordTargetResult(POINTS[0].bssid,POINTS[0].ssid,True,1,4,password_file='result.txt')
        CaptureService._on_openwrt_password_succeeded(self.service,((entry,),''))
        self.assertEqual(self.state(0),'found')
        self.assertEqual(self.service.openwrt_backend.snapshot.artifacts[0].kind,'password_result')
    def test_reporter_exports_outcome_events_and_early_history_skip_count(self):
        messages=Mock();reporter=PasswordVerificationReporter(POINTS,{},messages,self.service.progress)
        reporter.target_event(0)
        reporter.progress(PasswordVerifyUpdate(1,2,target_total=6,target_bssid=POINTS[0].bssid,history_skipped=4))
        self.assertIn('4',repr(messages.call_args_list))
        reporter.target_event(0,PasswordTargetResult(POINTS[0].bssid,POINTS[0].ssid,False,1,4,error=True,outcome='timeout'))
        self.assertEqual(self.state(0),'timeout')

class TableFollowTests(unittest.TestCase):
    def test_active_target_follows_only_on_switch_without_changing_checks_or_sort(self):
        owner=QWidget();owner.scale_manager=UiScaleManager(100)
        table=AccessPointTable(owner);QVBoxLayout(owner).addWidget(table)
        points=tuple(replace(POINTS[i%6],bssid='02:22:33:44:'+format(i//256,'02X')+':'+format(i%256,'02X'),ssid='Target '+str(i)) for i in range(80))
        owner.resize(900,250);table.set_access_points(points);owner.show();APP.processEvents()
        try:
            checked=table.selected_bssids()
            with patch.object(table,'scrollToItem',wraps=table.scrollToItem) as scroll:
                table.set_capturing_bssids((points[-1].bssid,),activity='password_verify')
                self.assertEqual(scroll.call_count,1)
                self.assertEqual(table.item(table.currentRow(),table.STATUS_COLUMN).data(Qt.UserRole),points[-1].bssid)
                self.assertGreater(table.verticalScrollBar().value(),0)
                table.set_capturing_bssids((points[-1].bssid,),activity='password_verify')
                self.assertEqual(scroll.call_count,1)
                table.set_capturing_bssids((points[0].bssid,),activity='password_verify')
                self.assertEqual(scroll.call_count,2)
                self.assertEqual(table.selected_bssids(),checked)
        finally:owner.close();owner.deleteLater();APP.processEvents()
    def test_found_and_timeout_labels_survive_active_reset_and_incremental_refresh(self):
        owner=QWidget();owner.scale_manager=UiScaleManager(100);table=AccessPointTable(owner)
        try:
            points=(replace(POINTS[0],password_state='found'),replace(POINTS[1],password_state='timeout'))
            table.set_access_points(points)
            table.set_capturing_bssids((points[0].bssid,),activity='password_verify')
            self.assertEqual(table.item(0,table.STATUS_COLUMN).text(),'\u627e\u5230\u5bc6\u7801')
            table.set_capturing_bssids((),activity='password_verify');table.set_access_points(points)
            self.assertEqual(table.item(0,table.STATUS_COLUMN).text(),'\u627e\u5230\u5bc6\u7801')
            self.assertEqual(table.item(1,table.STATUS_COLUMN).text(),'\u5df2\u8d85\u65f6')
        finally:owner.deleteLater();APP.processEvents()

    def test_previous_password_failure_does_not_recolor_an_active_capture(self):
        owner=QWidget();owner.scale_manager=UiScaleManager(100);table=AccessPointTable(owner)
        try:
            point=replace(POINTS[0],password_state='timeout')
            table.set_access_points((point,))
            table.set_capturing_bssids((point.bssid,),activity='handshake')
            item=table.item(0,table.STATUS_COLUMN)
            self.assertEqual(item.text(),'\u6293\u5305\u4e2d')
            self.assertIsNone(item.data(Qt.ForegroundRole))
        finally:owner.deleteLater();APP.processEvents()

    def test_result_colors_override_captured_file_color_and_reset_with_theme(self):
        owner=QWidget();owner.scale_manager=UiScaleManager(100);table=AccessPointTable(owner)
        states=('found','no_match','failed','timeout','cancelled','unconfirmed')
        points=tuple(replace(p,password_state=state,capture_state=AccessPointCaptureState.CAPTURED) for p,state in zip(POINTS,states))
        try:
            table.set_access_points(points)
            for light in (False,True):
                table.set_light_theme(light)
                self.assertEqual(table.item(0,table.STATUS_COLUMN).foreground().color(),table._highlight_green())
                for row in (2,3):
                    self.assertEqual(table.item(row,table.STATUS_COLUMN).foreground().color().name(),'#b52d2d' if light else '#ef7777')
                for row in (1,4,5):self.assertIsNone(table.item(row,table.STATUS_COLUMN).data(Qt.ForegroundRole))
            changed=(replace(points[2],password_state='no_match'),)
            table.set_access_points(changed)
            self.assertIsNone(table.item(0,table.STATUS_COLUMN).data(Qt.ForegroundRole))
        finally:owner.deleteLater();APP.processEvents()

    def test_render_outcomes_and_active_follow_in_both_themes(self):
        from modules.main_pages.packet_capture.capture_page_styles import CapturePageStyleManager
        from modules.main_pages.base_page_styles import light_base_page_stylesheet,dark_base_page_stylesheet
        owner=QWidget();owner.scale_manager=UiScaleManager(100);owner.setObjectName('packetCapturePage')
        table=AccessPointTable(owner);QVBoxLayout(owner).addWidget(table)
        states=('found','no_match','timeout','unconfirmed','cancelled','history_skipped','not_started','round_pending','verifying')
        points=tuple(replace(POINTS[i%6],bssid='02:22:33:44:55:'+format(i,'02X'),ssid='Target '+str(i+1),password_state=state) for i,state in enumerate(states))
        output=Path('build/previews/password-target-status');output.mkdir(parents=True,exist_ok=True)
        owner.resize(1220,470);table.set_access_points(points)
        try:
            for light in (False,True):
                owner.light_theme=light;styles=CapturePageStyleManager(owner)
                base=light_base_page_stylesheet('',owner.scale_manager) if light else dark_base_page_stylesheet(owner.scale_manager)
                owner.setStyleSheet(base+styles._capture_stylesheet(light));table.set_light_theme(light)
                owner.show();table.set_capturing_bssids((points[-1].bssid,),activity='password_verify');APP.processEvents()
                self.assertTrue(owner.grab().save(str(output/('light.png' if light else 'dark.png'))))
        finally:owner.close();owner.deleteLater();APP.processEvents()

class ResumeTests(unittest.TestCase):
    def test_confirmed_candidate_commits_before_cancel_and_resumes_with_skip(self):
        target=POINTS[0];verifier=OpenWrtPasswordVerifier(Mock())
        verifier._pick_radio=Mock(return_value='radio1');verifier._setup_environment=Mock()
        control=Mock();control.attempt.side_effect=[False,InterruptedError()]
        events=[]
        with TemporaryDirectory() as folder:
            path=Path(folder)/'history.db'
            with PasswordAttemptHistory(path,key=b'k'*32) as history:
                verifier._attempt_history=history
                with patch('modules.capture_services.openwrt.password_control.open_control',return_value=control):
                    with self.assertRaises(PasswordVerifyCancelled):
                        verifier._verify_single(target,('first-pass','second-pass'),0,1,'owned','net',folder,None,events.append,False)
            with PasswordAttemptHistory(path,key=b'k'*32) as history:
                self.assertTrue(history.contains(target,'first-pass'));self.assertFalse(history.contains(target,'second-pass'))
                verifier._attempt_history=history;control.attempt=Mock(return_value=True);events.clear()
                with patch('modules.capture_services.openwrt.password_control.open_control',return_value=control):
                    verifier._verify_single(target,('first-pass','second-pass'),0,1,'owned','net',folder,None,events.append,False)
                control.attempt.assert_called_once()
                self.assertEqual(control.attempt.call_args.args[-1],'second-pass')
                progress=next(e for e in events if isinstance(e,PasswordVerifyUpdate) and not e.phase)
                self.assertEqual((progress.tried,progress.total,progress.history_skipped),(1,1,1))

if __name__=='__main__':unittest.main()
