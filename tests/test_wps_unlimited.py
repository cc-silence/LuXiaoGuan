"""No total runtime cap still requires cancellation, lease renewal and checkpoint cleanup."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from modules.capture_services.openwrt.wps_capture import OpenWrtWpsCapture
from modules.capture_services.openwrt.models import AccessPointInfo, OpenWrtSessionSnapshot, CaptureTaskType
from modules.capture_services.capture_service import CaptureService
from modules.capture_services.openwrt.wps_models import WpsUpdate
from modules.config_services.capture_settings_manager import CaptureSettingsManager


class WpsUnlimitedTests(unittest.TestCase):
    def run_case(self, seconds, *, cancel=False, renew_error=False, success=False):
        clock=[0.0];event=Event();manager=Mock();updates=[]
        manager.is_running.side_effect=lambda *args: clock[0]<7200
        manager.read_log_tail.side_effect=lambda *args: ('WPS PIN: 12345670\nWPA PSK: fixturesecret' if success and clock[0]>=120 else f'Trying pin "00000000"\nReceived M1 message at {clock[0]}')
        if renew_error:manager.renew_watchdog.side_effect=RuntimeError('controlled lease failure')
        def wait(_seconds):
            clock[0]+=60
            if cancel and clock[0]>=120:event.set()
        event.wait=wait
        with TemporaryDirectory() as folder:
            capture=OpenWrtWpsCapture(Mock(),manager)
            with patch.object(capture,'_base_args',return_value='reaver'),patch.object(capture,'_download_wpc',return_value='saved.wpc') as checkpoint,patch('modules.capture_services.openwrt.wps_capture.time.monotonic',side_effect=lambda:clock[0]):
                try:
                    result=capture.run_targets(NS(local_dir=Path(folder),remote_dir='/tmp/owned'),'ownedmon','owned',(AccessPointInfo('02:11:22:33:44:55',channel=1,wps_supported=True),),'normal',dict(attack_seconds=seconds),folder,Mock(),event,updates.append)[0]
                except RuntimeError as exc:
                    result=exc
        return result,manager,updates,checkpoint

    def test_unlimited_survives_two_hours_with_renewal_and_real_elapsed_time(self):
        result,manager,updates,checkpoint=self.run_case(0)
        self.assertEqual(result.tried_seconds,7200)
        self.assertEqual(manager.start.call_args.kwargs['max_runtime'],0)
        self.assertEqual(manager.start.call_args.kwargs['renewable_output'],'/tmp/owned/reaver.log')
        self.assertGreater(manager.renew_watchdog.call_count,100)
        self.assertTrue(all(update.total==0 for update in updates))
        self.assertGreater(checkpoint.call_count,100)
        manager.stop.assert_called_once()

    def test_positive_budget_still_stops_without_renewable_lease(self):
        result,manager,updates,_=self.run_case(60)
        self.assertEqual(result.tried_seconds,60)
        self.assertNotIn('renewable_output',manager.start.call_args.kwargs)
        manager.renew_watchdog.assert_not_called()
        self.assertEqual(updates[-1].total,60)

    def test_cancel_and_lease_failure_stop_and_save(self):
        for options in [dict(cancel=True),dict(renew_error=True)]:
            result,manager,updates,checkpoint=self.run_case(0,**options)
            self.assertIsInstance(result,RuntimeError)
            manager.stop.assert_called_once_with(manager.start.return_value,None,graceful_signal='INT')
            self.assertTrue(checkpoint.called)

    def test_success_ends_unlimited_without_waiting_for_tool_exit(self):
        result,manager,updates,_=self.run_case(0,success=True)
        self.assertTrue(result.pin_ok);self.assertTrue(result.psk_ok)
        self.assertEqual(result.tried_seconds,120)
        manager.stop.assert_called_once()

    def test_zero_configuration_survives_save_reopen_and_progress_is_indeterminate(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'settings.json';manager=CaptureSettingsManager(path)
            data=manager.load();self.assertEqual(data['openwrt']['wps_defaults']['attack_seconds'],0)
            manager.save(data)
            self.assertEqual(CaptureSettingsManager(path).load()['openwrt']['wps_defaults']['attack_seconds'],0)
        snapshot=OpenWrtSessionSnapshot(task_type=CaptureTaskType.WPS,task_state='capturing')
        service=NS(openwrt_backend=NS(snapshot=snapshot),openwrtSnapshotChanged=Mock())
        CaptureService._on_openwrt_wps_progress(service,WpsUpdate(7200,0,current_pin='00000000'))
        progress=service.openwrt_backend.snapshot.progress
        self.assertTrue(progress.indeterminate);self.assertEqual(progress.current,7200)
        self.assertIn('7200',progress.detail)
        self.assertIn('00000000',progress.title)

    def test_dialog_zero_disables_rounds_and_resource_stop_has_a_reason(self):
        from tests.test_standalone import APP
        from modules.main_pages.packet_capture.openwrt.wps_dialog import AdvancedWpsDialog
        from modules.capture_services.openwrt.wps_observation import analyze
        dialog=AdvancedWpsDialog({})
        try:
            self.assertEqual(dialog.attack_seconds.value(),0)
            self.assertFalse(dialog.attack_rounds.isEnabled())
            self.assertTrue(dialog.attack_seconds.specialValueText())
            dialog.attack_seconds.setValue(60)
            self.assertTrue(dialog.attack_rounds.isEnabled())
            self.assertEqual(analyze('WINBOX_WATCHDOG_TMP_LOW').stage,'tool_error')
        finally:
            dialog.deleteLater();APP.processEvents()
