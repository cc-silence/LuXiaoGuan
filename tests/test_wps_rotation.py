"""Opt-in rotation preserves default behavior and requires native progress evidence."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import shlex
import unittest
from unittest.mock import Mock, patch
from modules.capture_services.openwrt.wps_options import prepare_wps_options
from modules.capture_services.openwrt.wps_rotation import WpsSourceRotation, progressed
from modules.capture_services.openwrt.wps_capture import OpenWrtWpsCapture
from modules.capture_services.openwrt.models import AccessPointInfo
from tests.test_wps_lifecycle import native

MAC = '02:11:22:33:44:55'
ADDRESSES = ['02:AA:BB:CC:DD:01','02:AA:BB:CC:DD:02','02:AA:BB:CC:DD:03']


class WpsRotationTests(unittest.TestCase):
    def test_each_new_task_gets_fresh_first_address_without_changing_saved_manual(self):
        saved = dict(spoof_mac_enabled=True, spoof_mac=MAC, rotate_mac_each_pin=True)
        with patch('modules.capture_services.openwrt.wps_options.random_source_mac', side_effect=ADDRESSES) as generator:
            first = prepare_wps_options(saved, mode='normal')
            self.assertEqual(prepare_wps_options(first, mode='normal'), first)
            second = prepare_wps_options(saved, mode='normal')
        self.assertEqual(first['spoof_mac'], ADDRESSES[0])
        self.assertEqual(second['spoof_mac'], ADDRESSES[1])
        self.assertEqual(saved['spoof_mac'], MAC)
        self.assertEqual(generator.call_count, 2)
        for mode in ['offline','combo']:
            with self.assertRaises(ValueError):prepare_wps_options(saved,mode=mode)
        for args in ['-g 4','-q']:
            with self.assertRaises(ValueError):prepare_wps_options(dict(saved,additional_args=args),mode='normal')

    def test_owned_interface_is_verified_before_command_and_failed_readback_never_advances(self):
        rotation = WpsSourceRotation(Mock(), Mock(), Mock(), ADDRESSES[0])
        order = []
        with patch('modules.capture_services.openwrt.wps_rotation.ensure_wps_source',side_effect=lambda *a:order.append('source')), patch('modules.capture_services.openwrt.wps_rotation.ensure_wps_channel',side_effect=lambda *a:order.append('channel')), patch('modules.capture_services.openwrt.wps_options.random_source_mac',return_value=ADDRESSES[1]):
            first = rotation.next_options({})
            second = rotation.next_options({})
        self.assertEqual(order,['source','channel','source','channel'])
        self.assertNotEqual(first['spoof_mac'],second['spoof_mac'])
        with patch('modules.capture_services.openwrt.wps_rotation.ensure_wps_source',side_effect=RuntimeError('readback failed')):
            with self.assertRaises(RuntimeError):rotation.next_options({})
        self.assertEqual(rotation.last_address,ADDRESSES[1])

    def run_native(self, folder, *, marker=True, advance=True, success=True, enabled=True):
        manager = Mock();manager.is_running.return_value=False
        count = [0]
        def start(*args,**kwargs):count[0]+=1;return Mock()
        manager.start.side_effect=start
        manager.read_log_tail.side_effect=lambda *args: ("WPS PIN: '12345670'\nWPA PSK: 'fixturesecret'" if success and count[0]==3 else f'Trying pin "{count[0]:04d}0000"\n'+('Quitting after 1 crack attempts' if marker else 'Failed to initialize interface'))
        capture=OpenWrtWpsCapture(Mock(),manager)
        file=Path(folder)/'state.wpc'
        def checkpoint(*args):
            rows=native().splitlines();rows[:3]=[str(count[0] if advance else 0),'0','0']
            file.write_text('\n'.join(rows)+'\n',encoding='ascii')
            return str(file)
        rotation=Mock()
        rotation.next_options.side_effect=[dict(spoof_mac_enabled=True,spoof_mac=address) for address in ADDRESSES]
        workspace=NS(local_dir=Path(folder),remote_dir='/tmp/owned')
        options=dict(spoof_mac_enabled=True,spoof_mac=MAC,rotate_mac_each_pin=enabled,attack_seconds=0)
        with patch('modules.capture_services.openwrt.wps_options.random_source_mac',return_value=ADDRESSES[0]), patch('modules.capture_services.openwrt.wps_capture.prepare_session',return_value=('/tmp/owned/021122334455.wpc',False)) as prepare, patch('modules.capture_services.openwrt.wps_rotation.session_directory',return_value=Path(folder)/'state'), patch.object(capture,'_download_wpc',side_effect=checkpoint):
            result=capture.run_targets(workspace,'ownmon','owned',(AccessPointInfo(MAC,channel=1,wps_supported=True),),'normal',options,folder,Mock(),source_rotation=rotation if enabled else None)[0]
        return result,manager,rotation,prepare

    def test_changes_address_at_confirmed_candidate_boundary_and_reuses_remote_session(self):
        with TemporaryDirectory() as folder:
            result,manager,rotation,prepare=self.run_native(folder)
        self.assertTrue(result.psk_ok)
        self.assertEqual(manager.start.call_count,3)
        self.assertEqual(manager.stop.call_count,3)
        self.assertEqual(prepare.call_count,1)
        for call,address in zip(manager.start.call_args_list,ADDRESSES):
            args=shlex.split(call.args[1])
            self.assertEqual(args[args.index('-m')+1],address)
            self.assertEqual(args[args.index('-g')+1],'1')
            self.assertEqual(args[args.index('-s')+1],'/tmp/owned/021122334455.wpc')

    def test_no_next_address_without_native_boundary_or_advancing_checkpoint(self):
        for kwargs in [dict(marker=False),dict(advance=False)]:
            with TemporaryDirectory() as folder:
                result,manager,rotation,_=self.run_native(folder,**kwargs)
            self.assertFalse(result.pin_ok)
            self.assertEqual(manager.start.call_count,1)
            self.assertEqual(rotation.next_options.call_count,1)

    def test_default_path_never_rotates_or_adds_single_attempt_argument(self):
        with TemporaryDirectory() as folder:
            _,manager,rotation,_=self.run_native(folder,enabled=False,success=False)
        self.assertEqual(manager.start.call_count,1)
        rotation.next_options.assert_not_called()
        self.assertNotIn('-g',shlex.split(manager.start.call_args.args[1]))

    def test_progress_cannot_regress_or_repeat_or_restart_exhausted_space(self):
        self.assertTrue(progressed((0,0,0),(1,0,0)))
        self.assertTrue(progressed((123,0,0),(123,1,1)))
        for after in [None,(1,0,0),(0,0,0),(10000,0,0),(1,1000,1),(1,1,2)]:
            self.assertFalse(progressed((1,0,0),after))

    def test_checkbox_settings_cancel_reopen_and_mode_dependencies(self):
        from tests.test_standalone import APP
        from modules.main_pages.packet_capture.openwrt.wps_dialog import AdvancedWpsDialog
        from modules.config_services.capture_settings_manager import CaptureSettingsManager
        with TemporaryDirectory() as folder:
            settings=CaptureSettingsManager(Path(folder)/'settings.json')
            data=settings.load();original=dict(data['openwrt']['wps_defaults'])
            dialog=AdvancedWpsDialog(original)
            try:
                self.assertFalse(dialog.rotate_mac_each_pin.isChecked())
                self.assertFalse(dialog.rotate_mac_each_pin.isEnabled())
                dialog.spoof_mac_enabled.setChecked(True)
                dialog.rotate_mac_each_pin.setChecked(True)
                self.assertFalse(dialog.spoof_mac.isEnabled())
                self.assertTrue(dialog.get_settings()['rotate_mac_each_pin'])
                dialog.mode.setCurrentIndex(dialog.mode.findData('offline'))
                self.assertFalse(dialog.get_settings()['rotate_mac_each_pin'])
                dialog.mode.setCurrentIndex(dialog.mode.findData('normal'))
                data['openwrt']['wps_defaults']=dialog.get_settings();settings.save(data)
                dialog.reject()
                self.assertFalse(original['rotate_mac_each_pin'])
            finally:dialog.deleteLater();APP.processEvents()
            reopened=AdvancedWpsDialog(settings.load()['openwrt']['wps_defaults'])
            try:self.assertTrue(reopened.rotate_mac_each_pin.isChecked())
            finally:reopened.deleteLater();APP.processEvents()

    def test_quick_rotates_per_fixed_candidate_and_keeps_normal_session_isolated(self):
        manager=Mock();manager.is_running.return_value=False
        manager.read_log_tail.return_value='Quitting after 1 crack attempts'
        rotation=Mock();rotation.next_options.side_effect=[dict(spoof_mac_enabled=True,spoof_mac=address) for address in ADDRESSES[:2]]
        from modules.capture_services.openwrt.wps_defaults import DefaultPinCandidate
        candidates=tuple(DefaultPinCandidate(pin,'fixture') for pin in ['12345670','00000000'])
        with TemporaryDirectory() as folder, patch('modules.capture_services.openwrt.wps_options.random_source_mac',return_value=ADDRESSES[0]), patch('modules.capture_services.openwrt.wps_quick.default_pin_candidates',return_value=candidates):
            capture=OpenWrtWpsCapture(Mock(),manager)
            with patch.object(capture,'skipped_result',return_value=None), patch('modules.capture_services.openwrt.wps_capture.prepare_session',side_effect=AssertionError('normal resume must not run')):
                capture.run_targets(NS(local_dir=Path(folder),remote_dir='/tmp/owned'),'ownmon','owned',(AccessPointInfo(MAC,channel=1,wps_supported=True),),'second',dict(spoof_mac_enabled=True,rotate_mac_each_pin=True),folder,Mock(),source_rotation=rotation)
        self.assertEqual(manager.start.call_count,2)
        for index,call in enumerate(manager.start.call_args_list):
            args=shlex.split(call.args[1])
            self.assertEqual(args[args.index('-m')+1],ADDRESSES[index])
            self.assertIn('quick-0-'+str(index)+'.wpc',args[args.index('-s')+1])
            self.assertEqual(args[args.index('-g')+1],'1')

    def test_enabled_no_reply_timeout_stops_without_rotating_again_and_cancel_stops(self):
        from threading import Event
        for cancel in (False,True):
            clock=[0.0];event=Event();manager=Mock();manager.is_running.return_value=True
            manager.read_log_tail.return_value='Trying pin "00000000"\nSending EAPOL START request'
            def wait(seconds):
                clock[0]+=10
                if cancel:event.set()
            event.wait=wait
            rotation=Mock();rotation.next_options.return_value=dict(spoof_mac_enabled=True,spoof_mac=ADDRESSES[0])
            with TemporaryDirectory() as folder, patch('modules.capture_services.openwrt.wps_options.random_source_mac',return_value=ADDRESSES[0]), patch('modules.capture_services.openwrt.wps_capture.time.monotonic',side_effect=lambda:clock[0]), patch('modules.capture_services.openwrt.wps_rotation.session_directory',return_value=Path(folder)/'state'):
                capture=OpenWrtWpsCapture(Mock(),manager)
                with patch.object(capture,'_base_args',return_value='reaver'),patch.object(capture,'_download_wpc',return_value=''):
                    args=(NS(local_dir=Path(folder),remote_dir='/tmp/owned'),'ownmon','owned',(AccessPointInfo(MAC,channel=1,wps_supported=True),),'normal',dict(spoof_mac_enabled=True,rotate_mac_each_pin=True,attack_seconds=0),folder,Mock(),event)
                    if cancel:
                        with self.assertRaises(RuntimeError):capture.run_targets(*args,source_rotation=rotation)
                    else:
                        result=capture.run_targets(*args,source_rotation=rotation)[0]
                        self.assertEqual(result.tried_seconds,60)
            self.assertEqual(manager.start.call_count,1)
            self.assertEqual(manager.stop.call_count,1)
            self.assertEqual(rotation.next_options.call_count,1)

    def test_review_rejects_impossible_native_index_jumps(self):
        for before,after in [((10,0,0),(12,0,0)),((10,0,0),(11,1,1)),((10,4,1),(11,0,1)),((10,4,1),(10,6,1))]:
            self.assertFalse(progressed(before,after),(before,after))

    def test_review_cancel_during_mac_preparation_never_launches_native(self):
        from modules.capture_services.openwrt.wps_rotation import run_rotating_normal
        cancelled=[False]
        def check():
            if cancelled[0]:raise RuntimeError('cancelled')
        def prepare():cancelled[0]=True;return 'reaver'
        run=Mock(return_value=(None,None))
        with TemporaryDirectory() as folder, patch('modules.capture_services.openwrt.wps_rotation.session_directory',return_value=Path(folder)), patch('modules.capture_services.openwrt.wps_rotation.time.monotonic',return_value=0):
            with self.assertRaises(RuntimeError):
                run_rotating_normal(MAC,100,prepare,run,{},lambda:'',lambda:True,check)
        run.assert_not_called()

    def test_review_exhausted_session_does_not_launch_and_last_rejection_reports_end(self):
        from modules.capture_services.openwrt.wps_rotation import run_rotating_normal
        state={'native_exit':True,'single_limit':True}
        for before,after,expected_calls in [((10000,0,0),None,0),((9999,0,0),(10000,0,0),1),((1,999,1),(1,1000,1),1)]:
            make=Mock(return_value='reaver');run=Mock(return_value=(None,None))
            with patch('modules.capture_services.openwrt.wps_rotation.session_progress',side_effect=[before,after]), patch('modules.capture_services.openwrt.wps_rotation.time.monotonic',return_value=0):
                result=run_rotating_normal(MAC,100,make,run,state,lambda:'saved',lambda:False,lambda:None)
            self.assertEqual(make.call_count,expected_calls)
            self.assertEqual(run.call_count,expected_calls)
            self.assertTrue(result[2])

    def test_review_bounded_retry_accepts_last_valid_draw_without_extra_generation(self):
        rotation=WpsSourceRotation(Mock(),Mock(),Mock(),ADDRESSES[0]);rotation.last_address=ADDRESSES[0]
        with patch('modules.capture_services.openwrt.wps_options.random_source_mac',side_effect=[ADDRESSES[0]]*7+[ADDRESSES[1]]) as random, patch('modules.capture_services.openwrt.wps_rotation.ensure_wps_source'), patch('modules.capture_services.openwrt.wps_rotation.ensure_wps_channel'):
            self.assertEqual(rotation.next_options({})['spoof_mac'],ADDRESSES[1])
            self.assertEqual(random.call_count,8)

    def test_review_quick_cancel_after_prepare_never_launches(self):
        from threading import Event
        from modules.capture_services.openwrt.wps_defaults import DefaultPinCandidate
        event=Event();manager=Mock();rotation=Mock()
        def prepare(*args):event.set();return dict(spoof_mac_enabled=True,spoof_mac=ADDRESSES[0])
        rotation.next_options.side_effect=prepare
        with TemporaryDirectory() as folder, patch('modules.capture_services.openwrt.wps_options.random_source_mac',return_value=ADDRESSES[0]), patch('modules.capture_services.openwrt.wps_quick.default_pin_candidates',return_value=(DefaultPinCandidate('12345670','fixture'),)):
            capture=OpenWrtWpsCapture(Mock(),manager)
            with patch.object(capture,'skipped_result',return_value=None), patch.object(capture,'_base_args',return_value='reaver'):
                with self.assertRaises(RuntimeError):
                    capture.run_targets(NS(local_dir=Path(folder),remote_dir='/tmp/owned'),'ownmon','owned',(AccessPointInfo(MAC,channel=1,wps_supported=True),),'second',dict(spoof_mac_enabled=True,rotate_mac_each_pin=True),folder,Mock(),event,source_rotation=rotation)
        manager.start.assert_not_called()

    def test_review_session_progress_reads_validated_snapshot_only_once(self):
        from modules.capture_services.openwrt.wps_session import session_progress
        from unittest.mock import mock_open
        reader=mock_open(read_data=native().encode('ascii'))
        with patch('pathlib.Path.open',reader):
            self.assertEqual(session_progress('owned.wpc'),(12,3,1))
        self.assertEqual(reader.call_count,1)
        self.assertIsNone(session_progress(None))
