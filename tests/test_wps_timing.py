"""Candidate clock, receive-idle cutoff, average speed and batch continuation."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from modules.capture_services.openwrt.wps_timing import WpsCandidateTiming
from modules.capture_services.openwrt.wps_capture import OpenWrtWpsCapture
from modules.capture_services.openwrt.wps_models import WpsUpdate
from modules.capture_services.openwrt.models import AccessPointInfo, CaptureTaskType, OpenWrtSessionSnapshot
from modules.capture_services.capture_service import CaptureService


class CandidateTimingTests(unittest.TestCase):
    def test_switch_resets_clock_same_pin_and_replayed_tail_do_not(self):
        timing = WpsCandidateTiming()
        timing.feed('Trying pin "00000000"', now=0)
        timing.feed('Trying pin "00000000"', now=20)
        self.assertEqual(timing.snapshot(now=25), (25, None))
        timing.feed('Trying pin "00000000"\nTrying pin "00010009"', now=30)
        elapsed, rate = timing.snapshot(now=40)
        self.assertEqual(elapsed, 10)
        self.assertAlmostEqual(rate, 1.5)
        timing.feed('Trying pin "00010009"\nTrying pin "00000000"', now=45)
        self.assertEqual(timing.snapshot(now=60), (15, 2))
        timing.freeze(now=60)
        self.assertEqual(timing.snapshot(now=100), (15, 2))

    def test_only_new_wps_replies_extend_liveness_not_sends_beacons_or_replayed_m1(self):
        timing = WpsCandidateTiming()
        timing.feed('Trying pin "00000000"\nReceived M1 message', now=0)
        timing.feed('Trying pin "00000000"\nReceived M1 message\nSending EAPOL START request', now=50)
        self.assertFalse(timing.reply_timed_out(now=59.9))
        self.assertTrue(timing.reply_timed_out(now=60))
        timing.feed('Received M3 message', now=61)
        self.assertFalse(timing.reply_timed_out(now=120))
        self.assertTrue(timing.reply_timed_out(now=121))
        timing.feed('Trying pin "00010009"', now=122)
        self.assertFalse(timing.reply_timed_out(now=181))

    def test_pending_candidate_and_recovered_secrets_do_not_start_statistics(self):
        timing = WpsCandidateTiming()
        timing.feed('WPS PIN: 12345670\nWPA PSK: fixturesecret', now=0)
        self.assertEqual(timing.snapshot(now=500), (0, None))
        self.assertFalse(timing.reply_timed_out(now=500))
        self.assertNotIn('12345670', repr(vars(timing)))
        self.assertNotIn('fixturesecret', repr(vars(timing)))

    def run_targets(self, *, reply=False, mode='normal'):
        clock = [0.0]
        manager = Mock()
        manager.is_running.side_effect = lambda *args: not reply or clock[0] % 180 < 120
        manager.read_log_tail.side_effect = lambda *args: ('Trying pin "00000000"\n' +
            (f'Received M1 message at {clock[0]}' if reply else 'Sending EAPOL START request'))
        event = Event()
        event.wait = lambda seconds: clock.__setitem__(0, clock[0]+10)
        updates = []
        points = tuple(AccessPointInfo(f'02:11:22:33:44:{n:02X}', channel=1, wps_supported=True) for n in (1, 2))
        with TemporaryDirectory() as folder:
            capture = OpenWrtWpsCapture(Mock(), manager)
            with patch.object(capture, '_base_args', return_value='reaver'), patch.object(capture, '_download_wpc', return_value='saved.wpc'), patch('modules.capture_services.openwrt.wps_capture.time.monotonic', side_effect=lambda: clock[0]):
                results = capture.run_targets(NS(local_dir=Path(folder), remote_dir='/tmp/owned'), 'ownmon', 'owned', points, mode,
                    {'attack_seconds': 0, 'attack_rounds': 3}, folder, Mock(), event, updates.append)
        return results, manager, updates

    def test_no_reply_stops_target_at_sixty_saves_progress_then_continues(self):
        results, manager, updates = self.run_targets()
        self.assertEqual(len(results), 2)
        self.assertEqual(manager.start.call_count, 2)
        self.assertEqual(manager.stop.call_count, 2)
        self.assertTrue(all(r.tried_seconds == 60 for r in results))
        self.assertTrue(all('60' in r.note and r.wpc_file for r in results))
        self.assertTrue(any(u.target_index == 1 for u in updates))
        self.assertTrue(any(u.candidate_elapsed >= 50 for u in updates))
        self.assertTrue(all(u.average_per_minute is None for u in updates))

    def test_live_replies_allow_same_candidate_to_continue_past_sixty(self):
        results, manager, updates = self.run_targets(reply=True)
        self.assertGreaterEqual(results[0].tried_seconds, 120)
        self.assertNotIn('60', results[0].note)
        self.assertTrue(any(u.candidate_elapsed > 60 for u in updates))

    def test_service_snapshot_displays_timing_and_resets_at_preparation(self):
        service = NS(openwrt_backend=NS(snapshot=OpenWrtSessionSnapshot(task_type=CaptureTaskType.WPS, task_state='capturing')), openwrtSnapshotChanged=Mock())
        update = WpsUpdate(40, 0, current_pin='00010009', candidate_elapsed=10, average_per_minute=1.5)
        CaptureService._on_openwrt_wps_progress(service, update)
        title = service.openwrt_backend.snapshot.progress.title
        self.assertIn('10.0', title)
        self.assertIn('1.50', title)
        CaptureService._on_openwrt_wps_progress(service, replace(update, current_pin='', candidate_elapsed=0, average_per_minute=None))
        self.assertNotIn('1.50', service.openwrt_backend.snapshot.progress.title)

    def test_combo_timeout_does_not_launch_another_phase_for_same_target(self):
        results, manager, _ = self.run_targets(mode='combo')
        self.assertEqual(len(results), 2)
        self.assertEqual(manager.start.call_count, 2)
        self.assertTrue(all('60' in result.note for result in results))

    def test_review_new_process_does_not_inherit_previous_receive_deadline(self):
        timing=WpsCandidateTiming()
        timing.feed('Trying pin "00000000"',now=0)
        timing.begin_phase()
        timing.feed('Waiting for beacon',now=80)
        self.assertFalse(timing.reply_timed_out(now=80))
        timing.feed('Trying pin "00000000"',now=90)
        self.assertFalse(timing.reply_timed_out(now=149))
        self.assertTrue(timing.reply_timed_out(now=150))
