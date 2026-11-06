"""Unavailable scan entry explains why without starting network operations."""
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import unittest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget
from tests.test_standalone import APP
from modules.capture_services.openwrt.models import DependencyInfo, DependencyState
from modules.main_pages.packet_capture.scan_entry import scan_block_reason
from modules.main_pages.packet_capture_page import PacketCapturePage
from modules.main_pages.packet_capture.openwrt.capture_tool_panel import CaptureToolPanel


class ScanEntryFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.page = QWidget()
        self.settings = {"enabled_features": {"scan": True}}
        self.snapshot = NS(connection_state=NS(value="connected"), task_state="idle",
                           dependencies=(DependencyInfo("scan", "scan", DependencyState.READY),))
        self.page.capture_service = NS(openwrt_snapshot=self.snapshot, task_controller=NS(busy=False))
        self.page.info_panel = NS(append_info=Mock())
        self.page.openwrt_tab = NS(connection_settings=lambda:self.settings, show_page=Mock(), tabs=Mock())
        self.page._scan_start_controller = NS(start=Mock())

    def tearDown(self):
        self.page.close(); self.page.deleteLater(); APP.processEvents()

    def test_missing_dependency_click_opens_explanation_and_can_go_to_dependencies(self):
        self.snapshot.dependencies = (DependencyInfo("scan", "scan", DependencyState.MISSING,
                                      missing_packages=("aircrack-ng",)),)
        with patch("modules.main_pages.packet_capture.scan_entry.ConfirmDialog") as dialog:
            dialog.Accepted = 1; dialog.return_value.exec.return_value = 1
            PacketCapturePage._start_openwrt_scan(self.page)
            self.assertIn("aircrack-ng", dialog.call_args.args[1])
        self.page._scan_start_controller.start.assert_not_called()
        self.page.openwrt_tab.show_page.assert_called_once_with("login")
        self.page.openwrt_tab.tabs.setCurrentIndex.assert_called_once_with(1)

    def test_disabled_and_unchecked_dependencies_show_dialog_without_launch(self):
        for state in DependencyState:
            if state == DependencyState.READY:
                continue
            self.snapshot.dependencies = (DependencyInfo("scan", "scan", state),)
            with self.subTest(state=state), patch("modules.main_pages.packet_capture.scan_entry.ConfirmDialog") as dialog:
                PacketCapturePage._start_openwrt_scan(self.page)
                dialog.assert_called_once()
        self.snapshot.dependencies = ()
        with patch("modules.main_pages.packet_capture.scan_entry.ConfirmDialog") as dialog:
            PacketCapturePage._start_openwrt_scan(self.page)
            dialog.assert_called_once()
        self.page._scan_start_controller.start.assert_not_called()

    def test_feature_switch_off_and_busy_have_specific_explanation(self):
        reason = scan_block_reason(self.snapshot, {"scan": False})
        self.assertIn("\u5df2\u5173\u95ed", reason[1])
        self.snapshot.task_state = "stopping"
        with patch("modules.main_pages.packet_capture.scan_entry.MessageDialog") as dialog:
            PacketCapturePage._start_openwrt_scan(self.page)
            self.assertIn("\u6536\u5c3e", dialog.call_args.args[1])
        self.page._scan_start_controller.start.assert_not_called()

    def test_ready_scan_does_not_require_unrelated_dependencies(self):
        self.snapshot.dependencies += (DependencyInfo("wps", "wps", DependencyState.MISSING),)
        PacketCapturePage._start_openwrt_scan(self.page)
        self.page._scan_start_controller.start.assert_called_once_with(self.settings)

    def test_button_remains_clickable_but_active_scan_keeps_stop_action(self):
        panel = CaptureToolPanel({}, self.page)
        actions = []; panel.actionRequested.connect(actions.append)
        for connected, busy, enabled in ((True, False, {}), (True, True, {}), (False, False, {})):
            panel.set_action_availability(connected, busy, enabled)
            self.assertTrue(panel.scan_button.isEnabled())
            panel.scan_button.click()
            self.assertEqual(actions[-1], "scan")
        panel.set_action_availability(True, True, {}, "wifi_scan", "capturing")
        panel.scan_button.click()
        self.assertEqual(actions[-1], "stop")
