"""Saved success display and pending-target selection without router access."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
from modules.capture_services.openwrt.models import AccessPointInfo, OpenWrtSessionSnapshot, OpenWrtConnectionState
from modules.capture_services.openwrt.password_history import PasswordAttemptHistory, clear_history
from modules.capture_services.openwrt.wifi_scanner import WifiScanResult, WifiScanUpdate
from modules.capture_services.password_scan_history import load_found_states, scan_with_password_history
from modules.capture_services.password_target_status import restore_scan, pending_password_targets
from modules.capture_services.capture_service import CaptureService

POINT = AccessPointInfo('02:11:22:33:44:55', ssid='fixture', encryption='WPA2')


class SavedStatusTests(unittest.TestCase):
    def test_restart_incremental_final_identity_and_clear(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'history.sqlite3'
            result = Path(folder) / 'fixture-password.txt'
            result.write_text('header\nfixture\t02:11:22:33:44:55\tfixture-only\n', encoding='utf-8')
            with PasswordAttemptHistory(path, key=b'k'*32) as history:
                history.mark_success(POINT, 'fixture-only', result)
            updates = []
            other_name = replace(POINT, ssid='different')
            other_mac = replace(POINT, bssid='02:11:22:33:44:66')
            points = (POINT, other_name, other_mac)
            def scan(*args, cancel_event=None, progress_callback=None):
                progress_callback(WifiScanUpdate(1, 10, points))
                return WifiScanResult(points, '')
            with patch('modules.capture_services.openwrt.password_history.history_path', return_value=path):
                final = scan_with_password_history(scan, cancel_event=Event(), progress_callback=updates.append)
                for snapshot in (updates[0], final):
                    self.assertEqual([p.password_state for p in snapshot.access_points], ['found', '', ''])
                old = {(POINT.bssid, POINT.ssid): ('no_match', '')}
                self.assertEqual(restore_scan(final.access_points[0], old).password_state, 'found')
                clear_history(path)
                self.assertEqual(load_found_states(), {})

    def test_absent_cancelled_and_damaged_records_do_not_break_scan(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'history.sqlite3'
            with patch('modules.capture_services.openwrt.password_history.history_path', return_value=path):
                self.assertEqual(load_found_states(), {})
                self.assertFalse(path.exists())
                path.write_bytes(b'not a database')
                cancel = Event(); cancel.set()
                self.assertEqual(load_found_states(cancel), {})
                scan = Mock(return_value=WifiScanResult((POINT,), ''))
                warning = Mock()
                result = scan_with_password_history(scan, message_callback=warning)
                self.assertEqual(result.access_points, (POINT,))
                warning.assert_called_once()

    def test_only_explicit_success_is_skipped_and_clear_allows_it_again(self):
        found = replace(POINT, password_state='found')
        retry = replace(POINT, bssid='02:11:22:33:44:66', password_state='history_skipped')
        selected = (found.bssid.lower(), retry.bssid)
        self.assertEqual(pending_password_targets((found, retry), selected), (retry,))
        reset = replace(found, password_state='')
        self.assertEqual(pending_password_targets((reset, retry), selected), (reset, retry))

    def test_service_all_found_never_starts_worker(self):
        owner = NS(_shutting_down=False, task_controller=NS(busy=False),
                   openwrt_backend=NS(snapshot=replace(OpenWrtSessionSnapshot(),
                       connection_state=OpenWrtConnectionState.CONNECTED,
                       access_points=(replace(POINT, password_state='found'),))),
                   messageGenerated=Mock(), _start_openwrt_worker=Mock())
        self.assertFalse(CaptureService.capture_openwrt_password_async(owner, {}, (POINT.bssid,)))
        owner._start_openwrt_worker.assert_not_called()
        owner.messageGenerated.emit.assert_called_once()


    def test_mixed_selection_only_submits_remaining_target(self):
        found = replace(POINT, password_state='found')
        pending = replace(POINT, bssid='02:11:22:33:44:66')
        owner = NS(_shutting_down=False, task_controller=NS(busy=False, begin=Mock(return_value=Event()), pause_control=None),
                   openwrt_backend=NS(snapshot=replace(OpenWrtSessionSnapshot(),
                       connection_state=OpenWrtConnectionState.CONNECTED, access_points=(found,pending)),
                       verify_passwords=Mock()),
                   messageGenerated=Mock(), openwrtSnapshotChanged=Mock(), _start_openwrt_worker=Mock(),
                   _stash_privacy_mask=Mock(), _stash_result_prefs=Mock(), _masked_target=lambda name,mac:name,
                   _on_openwrt_password_succeeded=Mock(), _on_openwrt_password_failed=Mock(),
                   _on_openwrt_password_progress=Mock(), _on_openwrt_password_message=Mock())
        with patch('modules.capture_services.capture_service.dictionary_paths', return_value=('fixture.txt',)):
            self.assertTrue(CaptureService.capture_openwrt_password_async(owner, {}, (found.bssid,pending.bssid)))
        worker = owner._start_openwrt_worker.call_args.args[1]
        worker(Event(), Mock(), Mock())
        self.assertEqual(owner.openwrt_backend.verify_passwords.call_args.args[2], (pending,))
        self.assertEqual(owner.openwrt_backend.snapshot.access_points[0].password_state,'found')


if __name__ == '__main__':
    unittest.main()
