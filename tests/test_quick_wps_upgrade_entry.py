"""Upgrade exits quick settings without saving or starting PIN capture."""
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from tests.test_file_selection import APP
from modules.main_pages.packet_capture_page import PacketCapturePage


class QuickUpgradeEntryTests(unittest.TestCase):
    def test_upgrade_has_separate_action_and_current_availability(self):
        for connected, busy, support, expected in (
                (True, False, 'basic', True), (True, False, 'full', False),
                (False, False, 'basic', False), (True, True, 'basic', False)):
            with self.subTest(connected=connected, busy=busy, support=support):
                page = NS(settings={}, capture_service=NS(
                    openwrt_snapshot=NS(connection_state=NS(value='connected' if connected else 'disconnected'),
                        wps_system_support=support), task_controller=NS(busy=busy)),
                    _clear_remembered_wps_pins=Mock(), _prompt_wpad_upgrade=Mock(),
                    _save_settings_with_feedback=Mock(), _start_openwrt_wps=Mock())
                with patch('modules.main_pages.packet_capture.openwrt.quick_wps_dialog.QuickWpsDialog') as cls:
                    cls.Accepted = 1; cls.UpgradeRequested = 2
                    dialog = cls.return_value; dialog.exec.return_value = 2
                    PacketCapturePage._prompt_quick_wps(page)
                    self.assertEqual(cls.call_args.kwargs['upgrade_available'], expected)
                    dialog.deleteLater.assert_called_once()
                    dialog.get_settings.assert_not_called()
                page._prompt_wpad_upgrade.assert_called_once()
                page._save_settings_with_feedback.assert_not_called()
                page._start_openwrt_wps.assert_not_called()
