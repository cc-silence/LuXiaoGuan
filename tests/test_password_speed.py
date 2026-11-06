"""Latest completed attempt speed, target resets and secret-safe progress."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
import unittest
from PySide6.QtWidgets import QWidget, QFrame, QVBoxLayout
from PySide6.QtTest import QTest
from tests.test_file_selection import APP
from modules.capture_services.openwrt.password_speed import PasswordAttemptElapsed
from modules.capture_services.openwrt.password_reporting import PasswordVerificationReporter
from modules.capture_services.openwrt.password_verification import PasswordVerifyUpdate
from modules.capture_services.openwrt.models import CaptureProgress
from modules.main_pages.packet_capture.openwrt.task_progress import CaptureTaskProgress


class SpeedTests(unittest.TestCase):
    def test_every_completed_attempt_updates_immediately_without_retaining_secret(self):
        updates = []
        reporter = PasswordVerificationReporter([NS(ssid='fixture', bssid='owned')], {}, None, updates.append)
        reporter.target_event(0)
        reporter.progress(PasswordVerifyUpdate(1, 100, candidate='fixture-secret', attempt_started=100))
        self.assertNotIn('fixture-secret', repr(reporter._attempt_update))
        reporter.progress(PasswordAttemptElapsed(3.5))
        self.assertTrue(updates[-1].speed_only)
        self.assertEqual(updates[-1].attempt_elapsed, 3.5)
        self.assertEqual(updates[-1].speed_text, '\u901f\u5ea6\uff1a3.5 \u79d2/\u4e2a')
        self.assertEqual(updates[-1].attempt_started, 0)
        self.assertFalse(updates[-1].candidate)
        self.assertIsNone(reporter._attempt_update)

    def test_switch_target_discards_previous_attempt(self):
        updates = []
        targets = [NS(ssid='first', bssid='one'), NS(ssid='second', bssid='two')]
        reporter = PasswordVerificationReporter(targets, {}, None, updates.append)
        reporter.target_event(0)
        reporter.progress(PasswordVerifyUpdate(1, 10, attempt_started=100))
        reporter.target_event(1)
        self.assertIsNone(reporter._attempt_update)
        self.assertEqual(updates[-1].attempt_started, 0)
        self.assertEqual(updates[-1].attempt_elapsed, -1)
        self.assertEqual(updates[-1].speed_text, '\u901f\u5ea6\uff1a\u7edf\u8ba1\u4e2d')

    def test_service_transfers_clock_without_changing_candidate_on_completion(self):
        from tests.test_password_target_status import Service
        from modules.capture_services.capture_service import CaptureService
        service = Service()
        service.password_verification_candidate = 'fixture-current'
        CaptureService._on_openwrt_password_progress(service, PasswordVerifyUpdate(
            3, 10, target_bssid='02:11:22:33:44:01', speed_text='6.7 seconds/attempt', speed_only=True))
        self.assertEqual(service.password_verification_candidate, 'fixture-current')
        self.assertIn('6.7 seconds/attempt',service.openwrt_backend.snapshot.progress.title)
        self.assertEqual(service.openwrt_backend.snapshot.progress.current, 3)

    def test_last_attempt_speed_stays_visible_until_next_completion(self):
        updates=[]
        reporter=PasswordVerificationReporter([NS(ssid='fixture',bssid='owned')],{},None,updates.append)
        reporter.target_event(0)
        reporter.progress(PasswordVerifyUpdate(1,10,attempt_started=100))
        reporter.progress(PasswordAttemptElapsed(2))
        self.assertEqual(updates[-1].speed_text,'\u901f\u5ea6\uff1a2.0 \u79d2/\u4e2a')
        reporter.progress(PasswordVerifyUpdate(2,10,attempt_started=102))
        self.assertEqual(updates[-1].speed_text,'\u901f\u5ea6\uff1a2.0 \u79d2/\u4e2a')
        reporter.progress(PasswordAttemptElapsed(5.7))
        self.assertEqual(updates[-1].speed_text,'\u901f\u5ea6\uff1a5.7 \u79d2/\u4e2a')
        reporter.progress(PasswordVerifyUpdate(3,10,attempt_started=108))
        self.assertEqual(updates[-1].speed_text,'\u901f\u5ea6\uff1a5.7 \u79d2/\u4e2a')

    def test_render_elapsed_both_themes(self):
        from modules.ui_scale_manager import UiScaleManager
        from modules.main_pages.packet_capture.capture_page_styles import CapturePageStyleManager
        from modules.main_pages.base_page_styles import light_base_page_stylesheet, dark_base_page_stylesheet
        output=Path('build/previews/password-speed');output.mkdir(parents=True,exist_ok=True)
        for light in (True,False):
            owner=QFrame();owner.scale_manager=UiScaleManager(100);owner.light_theme=light
            owner.setObjectName('packetCapturePage')
            widget=CaptureTaskProgress(owner);QVBoxLayout(owner).addWidget(widget)
            styles=CapturePageStyleManager(owner)
            base=light_base_page_stylesheet('',owner.scale_manager) if light else dark_base_page_stylesheet(owner.scale_manager)
            owner.setStyleSheet(base+styles._capture_stylesheet(light))
            owner.resize(950,100);owner.show()
            widget.apply_progress(CaptureProgress('Target 1/3 | \u901f\u5ea6\uff1a6.1 \u79d2/\u4e2a',2,10),'capturing')
            APP.processEvents()
            self.assertTrue(owner.grab().save(str(output/('light.png' if light else 'dark.png'))))
            owner.close();owner.deleteLater();APP.processEvents()


if __name__ == '__main__':
    unittest.main()
