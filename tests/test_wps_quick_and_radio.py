"""Default-PIN quick checks and owned nl80211 channel preparation contracts."""
import shlex
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from modules.capture_services.openwrt.models import AccessPointInfo, CaptureCleanupError
from modules.capture_services.openwrt.radio_session import RadioSessionRecord
from modules.capture_services.openwrt.command_runner import RemoteCommandResult
from modules.capture_services.openwrt.wps_capture import OpenWrtWpsCapture
from modules.capture_services.openwrt.wps_defaults import default_pin_candidates, QUICK_TARGET_SECONDS
from modules.capture_services.openwrt.wps_options import prepare_wps_options
from modules.capture_services.openwrt.wps_radio import ensure_wps_channel

MAC = '54:A0:50:75:D2:40'
UNKNOWN = '02:11:22:33:44:55'

class DefaultPinTests(unittest.TestCase):
    def test_dialog_quick_mode_does_not_present_normal_duration_or_resume_as_active(self):
        from tests.test_standalone import APP
        from modules.main_pages.packet_capture.openwrt.wps_dialog import AdvancedWpsDialog
        dialog=AdvancedWpsDialog({})
        try:
            dialog.mode.setCurrentIndex(dialog.mode.findData('quick'))
            self.assertFalse(dialog.attack_seconds.isEnabled())
            self.assertFalse(dialog.resume_file.isEnabled())
            self.assertTrue(dialog.spoof_mac_enabled.isEnabled())
            dialog.mode.setCurrentIndex(dialog.mode.findData('normal'))
            self.assertTrue(dialog.attack_seconds.isEnabled())
            self.assertTrue(dialog.resume_file.isEnabled())
        finally:
            dialog.deleteLater();APP.processEvents()

    def test_upstream_known_vectors_unknown_and_deduplication(self):
        self.assertEqual([x.pin for x in default_pin_candidates(MAC)], ['40414089'])
        pins = [x.pin for x in default_pin_candidates('00:14:D1:11:22:33')]
        self.assertEqual(pins, ['11228677','22891587','56098419','95661469','99956042'])
        self.assertEqual(len(pins), len(set(pins)))
        self.assertFalse(default_pin_candidates(UNKNOWN))
        self.assertEqual(default_pin_candidates(UNKNOWN, {'partial_pin':'00000000'})[0].pin, '00000000')
        with self.assertRaises(ValueError):
            prepare_wps_options({'partial_pin':'1234'}, mode='second')
        self.assertEqual(prepare_wps_options({'partial_pin':'1234'}, mode='normal')['partial_pin'], '1234')

    def run_quick(self, folder, tail, *, mac=MAC, event=None, manager=None):
        manager = manager or Mock()
        manager.is_running.return_value = False
        manager.read_log_tail.side_effect = tail if callable(tail) else None
        if not callable(tail): manager.read_log_tail.return_value = tail
        capture = OpenWrtWpsCapture(Mock(), manager)
        updates=[]; workspace_manager=Mock()
        result = capture.run_targets(NS(local_dir=Path(folder),remote_dir='/tmp/owned'),
            'ownedmon','owned',(AccessPointInfo(mac,ssid='fixture',channel=1,wps_supported=True),),
            'second',{},folder,workspace_manager,event,updates.append)[0]
        return result, manager, workspace_manager, updates

    def test_unmatched_target_skips_before_any_remote_work_or_interface(self):
        from modules.capture_services.backends.openwrt_backend import OpenWrtCaptureBackend
        backend=object.__new__(OpenWrtCaptureBackend)
        backend.ssh_client=NS(connected=True);backend.reconnect_cleanup=Mock();backend.workspace_manager=Mock()
        backend.wps_capture=OpenWrtWpsCapture(Mock(),Mock())
        backend._find_scan_radio=Mock();backend._finish_capture_task=Mock()
        with TemporaryDirectory() as folder, patch('modules.capture_services.backends.openwrt_backend.CaptureWorkspaceFactory.create', return_value=NS(local_dir=Path(folder),remote_dir='/tmp/owned')):
            results=backend.run_wps('owned',{'resolved_output_dir':folder},(AccessPointInfo(UNKNOWN,channel=1,wps_supported=True),),'second')
        backend._find_scan_radio.assert_not_called()
        self.assertFalse(results[0].pin_ok)
        self.assertIn('未匹配',results[0].note)

    def test_fixed_candidate_never_runs_pixie_or_restores_normal_progress(self):
        with TemporaryDirectory() as folder, patch('modules.capture_services.openwrt.wps_capture.prepare_session',side_effect=AssertionError('must not restore normal progress')):
            result, manager, workspace_manager, _ = self.run_quick(folder, '[+] Quitting after 1 crack attempts')
        args=shlex.split(manager.start.call_args.args[1])
        self.assertNotIn('-K',args)
        self.assertEqual(args[args.index('-p')+1],'40414089')
        self.assertEqual(args[args.index('-s')+1],'/tmp/owned/quick-0-0.wpc')
        workspace_manager.upload_atomic.assert_not_called()
        workspace_manager.download_atomic.assert_not_called()
        self.assertFalse(result.pin_ok)
        self.assertEqual(result.wpc_file,'')
        self.assertIn('未通过',result.note)

    def test_generated_pin_is_not_success_without_native_confirmation(self):
        with TemporaryDirectory() as folder:
            result, manager, _, _ = self.run_quick(folder, '[+] Waiting for beacon from fixture')
        self.assertFalse(result.pin_ok);self.assertEqual(result.result_file,'')
        self.assertNotIn('40414089',result.note)
        manager.stop.assert_called_once()

    def test_only_confirmed_results_are_written_and_recovered_secrets_stay_out_of_progress(self):
        with TemporaryDirectory() as folder:
            result, _, _, updates = self.run_quick(folder, "WPS PIN: '40414089'\nWPA PSK: 'AuditOnlySecret'")
            self.assertTrue(result.pin_ok);self.assertTrue(result.psk_ok)
            self.assertIn('AuditOnlySecret',Path(result.result_file).read_text(encoding='utf-8'))
            self.assertNotIn('AuditOnlySecret',repr(updates))
            self.assertNotIn('AuditOnlySecret',result.note)

    def test_rejected_candidates_advance_and_finish_without_bruteforce(self):
        with TemporaryDirectory() as folder:
            result, manager, _, _ = self.run_quick(folder, '[+] Quitting after 1 crack attempts',mac='00:14:D1:11:22:33')
        self.assertEqual(manager.start.call_count,5)
        self.assertEqual(manager.stop.call_count,5)
        self.assertIn('5',result.note)
        self.assertFalse(result.pin_ok)
        for call in manager.start.call_args_list:self.assertIn(' -p ',call.args[1])

    def test_cancel_and_cleanup_failure_stop_exact_owned_process(self):
        with TemporaryDirectory() as folder:
            event=Event()
            def tail(*args):
                event.set();return '[+] Waiting for beacon'
            with self.assertRaisesRegex(RuntimeError,'已取消'):
                self.run_quick(folder,tail,event=event)
            manager=Mock();manager.stop.side_effect=RuntimeError('controlled stop failure')
            with self.assertRaises(CaptureCleanupError):
                self.run_quick(folder,'',manager=manager)

    def test_target_budget_is_finite_and_timeout_is_incomplete(self):
        with TemporaryDirectory() as folder:
            clock=[0.0];manager=Mock();manager.is_running.return_value=True
            manager.read_log_tail.return_value='[+] Waiting for beacon from fixture'
            capture=OpenWrtWpsCapture(Mock(),manager)
            with patch('modules.capture_services.openwrt.wps_quick.time.monotonic',side_effect=lambda:clock[0]),patch('modules.capture_services.openwrt.wps_quick.time.sleep',side_effect=lambda seconds:clock.__setitem__(0,clock[0]+seconds)):
                result=capture.run_targets(NS(local_dir=Path(folder),remote_dir='/tmp/owned'),'ownedmon','owned',(AccessPointInfo(MAC,channel=1,wps_supported=True),),'second',{},folder,Mock())[0]
        self.assertEqual(result.tried_seconds,QUICK_TARGET_SECONDS)
        manager.start.assert_called_once()
        self.assertIn('上限',result.note)
        self.assertNotIn('均已确认未通过',result.note)

class WpsRadioTests(unittest.TestCase):
    def setUp(self):
        self.record=RadioSessionRecord('owned','phy1','ownedmon','',ownership_token='routermanager:owned')
        self.target=AccessPointInfo(MAC,channel=161,band='5G')
    def info(self,channel=0,freq=0,kind='monitor',code=0):
        return RemoteCommandResult('fixture',code,stdout=f'Interface ownedmon\n wiphy 1\n type {kind}\n'+(f' channel {channel} ({freq} MHz), width: 20 MHz\n' if channel else ''))
    def test_already_tuned_is_read_only_for_24_and_5g(self):
        for band,channel,freq in [('2.4G',6,2437),('5G',161,5805)]:
            runner=Mock();runner.run.return_value=self.info(channel,freq)
            ensure_wps_channel(runner,self.record,AccessPointInfo(MAC,channel=channel,band=band))
            runner.run.assert_called_once()
            self.assertNotIn('set channel',runner.run.call_args.args[1])
    def test_one_nl80211_tune_then_readback_with_ownership_on_all_commands(self):
        runner=Mock();runner.run.side_effect=[self.info(),RemoteCommandResult('tune',0),self.info(161,5805)]
        ensure_wps_channel(runner,self.record,self.target)
        self.assertEqual(runner.run.call_count,3)
        self.assertIn('iw dev ownedmon set channel 161',runner.run.call_args_list[1].args[1])
        for call in runner.run.call_args_list:
            self.assertIn('ifalias',call.args[1]);self.assertIn('routermanager:owned',call.args[1]);self.assertIn('phy80211',call.args[1])
    def test_failed_or_unconfirmed_tune_is_not_allowed_to_start_pin(self):
        for after in [self.info(),self.info(36,5180),self.info(161,2437)]:
            runner=Mock();runner.run.side_effect=[self.info(),RemoteCommandResult('tune',1,stderr='Operation not supported'),after]
            with self.assertRaisesRegex(RuntimeError,'未启动 Reaver'):
                ensure_wps_channel(runner,self.record,self.target)
            self.assertEqual(runner.run.call_count,3)
    def test_missing_foreign_or_nonmonitor_interface_is_not_mutated(self):
        for before in [self.info(code=83),self.info(kind='managed')]:
            runner=Mock();runner.run.return_value=before
            with self.assertRaises(RuntimeError):ensure_wps_channel(runner,self.record,self.target)
            runner.run.assert_called_once()
    def test_cancelled_tune_aborts_without_readback(self):
        runner=Mock();runner.run.side_effect=[self.info(),RemoteCommandResult('tune',-1,cancelled=True)]
        with self.assertRaises(InterruptedError):ensure_wps_channel(runner,self.record,self.target)
        self.assertEqual(runner.run.call_count,2)

    def test_backend_tuning_failure_restores_owned_environment_without_launching_reaver(self):
        from modules.capture_services.backends.openwrt_backend import OpenWrtCaptureBackend
        with TemporaryDirectory() as folder:
            backend=object.__new__(OpenWrtCaptureBackend)
            backend.ssh_client=NS(connected=True);backend.reconnect_cleanup=Mock();backend.workspace_manager=Mock()
            backend.command_runner=Mock()
            backend.command_runner.run.side_effect=[self.info(),RemoteCommandResult('tune',1),self.info()]
            backend.wps_capture=Mock();backend.wps_capture.skipped_result.return_value=None
            backend._find_scan_radio=Mock(return_value='phy1');backend._maybe_takeover=Mock(return_value=None)
            backend.radio_session=Mock();backend.radio_session.create.return_value=self.record
            backend._restore_task_wireless=Mock(return_value=[]);backend._finish_capture_task=Mock()
            with patch('modules.capture_services.backends.openwrt_backend.CaptureWorkspaceFactory.create',return_value=NS(local_dir=Path(folder),remote_dir='/tmp/owned')):
                results=backend.run_wps('owned',{'resolved_output_dir':folder},(self.target,),'normal')
        backend.wps_capture.run_targets.assert_not_called()
        backend._restore_task_wireless.assert_called_once()
        backend._finish_capture_task.assert_called_once()
        self.assertIn('未启动 Reaver',results[0].note)
