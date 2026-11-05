"""Unit tests for three-state radio takeover prompt in auto capture."""
import unittest
from unittest.mock import MagicMock, patch
from PySide6.QtWidgets import QApplication

from modules.common_widgets.common_dialogs import ConfirmDialog
from modules.capture_services.openwrt.models import (
    OpenWrtConnectionState,
    OpenWrtDeviceInfo,
    OpenWrtRadioInfo,
    OpenWrtSessionSnapshot,
)
from modules.capture_services.openwrt.radio_usage import RadioUsage


class ConfirmDialogThreeButtonTests(unittest.TestCase):
    """Test ConfirmDialog two-button and three-button behaviors."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_two_button_default_behavior(self):
        dialog = ConfirmDialog("标题", "正文", yes_text="确定", no_text="取消")
        self.assertIsNone(dialog.cancel_button)
        self.assertEqual(dialog.yes_button.text(), "确定")
        self.assertEqual(dialog.no_button.text(), "取消")

        # Simulate clicking yes
        dialog._accept()
        self.assertEqual(dialog.result_action, ConfirmDialog.RESULT_YES)

        # Simulate clicking no
        dialog = ConfirmDialog("标题", "正文", yes_text="确定", no_text="取消")
        dialog._select_secondary()
        self.assertEqual(dialog.result_action, ConfirmDialog.RESULT_NO)
        self.assertTrue(dialog.secondary_selected)

    def test_three_button_behavior(self):
        # 1. Click yes
        dialog = ConfirmDialog("标题", "正文", yes_text="全部接管", no_text="仅接管允许", cancel_text="全部取消")
        self.assertIsNotNone(dialog.cancel_button)
        self.assertEqual(dialog.yes_button.text(), "全部接管")
        self.assertEqual(dialog.no_button.text(), "仅接管允许")
        self.assertEqual(dialog.cancel_button.text(), "全部取消")

        dialog._accept()
        self.assertEqual(dialog.result_action, ConfirmDialog.RESULT_YES)

        # 2. Click no
        dialog = ConfirmDialog("标题", "正文", yes_text="全部接管", no_text="仅接管允许", cancel_text="全部取消")
        dialog._select_secondary()
        self.assertEqual(dialog.result_action, ConfirmDialog.RESULT_NO)

        # 3. Click cancel
        dialog = ConfirmDialog("标题", "正文", yes_text="全部接管", no_text="仅接管允许", cancel_text="全部取消")
        dialog._select_cancel()
        self.assertEqual(dialog.result_action, ConfirmDialog.RESULT_CANCEL)

        # 4. Escape / reject
        dialog = ConfirmDialog("标题", "正文", yes_text="全部接管", no_text="仅接管允许", cancel_text="全部取消")
        dialog.reject()
        self.assertEqual(dialog.result_action, ConfirmDialog.RESULT_CANCEL)


class AutoCaptureTakeoverPageTests(unittest.TestCase):
    """Test AutoCapturePage takeover prompt scenarios."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def fixture(self, radios):
        from tests.test_auto_capture_feature import AutoCaptureFeatureTests
        from modules.main_pages.packet_capture.openwrt.auto_capture_page import AutoCapturePage

        owner = MagicMock()
        owner.settings = {}
        owner.openwrt_snapshot = OpenWrtSessionSnapshot(
            connection_state=OpenWrtConnectionState.CONNECTED,
            device=OpenWrtDeviceInfo(radios=tuple(radios)),
        )
        service = AutoCaptureFeatureTests.ui_service(self, owner)
        owner.auto_capture = service
        page = AutoCapturePage(owner, {})
        self.addCleanup(page.dispose)
        return page, service

    def test_mixed_scenario_choose_takeover_all(self):
        """Mixed scenario: radio0 busy, radio1 idle. Choose '全部接管' -> all start."""
        radios = [
            OpenWrtRadioInfo("radio0", interface="phy0", free=False),
            OpenWrtRadioInfo("radio1", interface="phy1", free=True),
        ]
        page, service = self.fixture(radios)

        # Preflight returns radio0 business, radio1 idle
        usages = {
            "radio0": RadioUsage("radio0", "phy0", "business", "enabled"),
            "radio1": RadioUsage("radio1", "phy1", "idle", "disabled"),
        }

        with patch("modules.capture_services.openwrt.capture_start_preflight.read_radio_usage", return_value=usages), \
             patch("modules.main_pages.packet_capture.openwrt.auto_capture_page.ConfirmDialog") as prompt_cls, \
             patch.object(service, "start_all") as start_all, \
             patch.object(service, "start_radio_capture") as start_single:

            prompt_mock = MagicMock()
            prompt_mock.exec.return_value = ConfirmDialog.Accepted
            prompt_mock.result_action = ConfirmDialog.RESULT_YES
            prompt_cls.return_value = prompt_mock
            prompt_cls.Accepted = ConfirmDialog.Accepted
            prompt_cls.ActionNo = ConfirmDialog.ActionNo
            prompt_cls.RESULT_YES = ConfirmDialog.RESULT_YES
            prompt_cls.RESULT_NO = ConfirmDialog.RESULT_NO

            page._on_start_all_clicked()
            page._start_preflight._thread.join(2)
            self.app.processEvents()

            # Confirm dialog should be created with 3 buttons
            prompt_cls.assert_called_once()
            _, kwargs = prompt_cls.call_args
            self.assertEqual(kwargs.get("yes_text"), "全部接管")
            self.assertEqual(kwargs.get("no_text"), "仅接管允许")
            self.assertEqual(kwargs.get("cancel_text"), "全部取消")

            # Check that takeover_check is now checked on radio0 card
            self.assertTrue(page.radio_cards["radio0"].takeover_check.isChecked())

            # Both radios should be started
            start_all.assert_called_once()
            started_configs = start_all.call_args.args[0]
            self.assertEqual(len(started_configs), 2)
            started_names = {c["radio_info"].name for c in started_configs}
            self.assertEqual(started_names, {"radio0", "radio1"})
            start_single.assert_not_called()

    def test_mixed_scenario_choose_not_takeover_starts_only_idle(self):
        """Mixed scenario: radio0 busy, radio1 idle. Choose '仅接管允许' -> only radio1 starts."""
        radios = [
            OpenWrtRadioInfo("radio0", interface="phy0", free=False),
            OpenWrtRadioInfo("radio1", interface="phy1", free=True),
        ]
        page, service = self.fixture(radios)

        usages = {
            "radio0": RadioUsage("radio0", "phy0", "business", "enabled"),
            "radio1": RadioUsage("radio1", "phy1", "idle", "disabled"),
        }

        with patch("modules.capture_services.openwrt.capture_start_preflight.read_radio_usage", return_value=usages), \
             patch("modules.main_pages.packet_capture.openwrt.auto_capture_page.ConfirmDialog") as prompt_cls, \
             patch.object(service, "start_all") as start_all, \
             patch.object(service, "start_radio_capture") as start_single:

            prompt_mock = MagicMock()
            prompt_mock.exec.return_value = ConfirmDialog.ActionNo
            prompt_mock.result_action = ConfirmDialog.RESULT_NO
            prompt_cls.return_value = prompt_mock
            prompt_cls.Accepted = ConfirmDialog.Accepted
            prompt_cls.ActionNo = ConfirmDialog.ActionNo
            prompt_cls.RESULT_YES = ConfirmDialog.RESULT_YES
            prompt_cls.RESULT_NO = ConfirmDialog.RESULT_NO

            page._on_start_all_clicked()
            page._start_preflight._thread.join(2)
            self.app.processEvents()

            # Radio0 takeover_check should remain unchecked
            self.assertFalse(page.radio_cards["radio0"].takeover_check.isChecked())

            # Only radio1 should be started via single capture
            start_single.assert_called_once()
            self.assertEqual(start_single.call_args.args[0].name, "radio1")
            start_all.assert_not_called()

    def test_mixed_scenario_choose_cancel_starts_nothing(self):
        """Mixed scenario: radio0 busy, radio1 idle. Choose '全部取消' -> nothing starts."""
        radios = [
            OpenWrtRadioInfo("radio0", interface="phy0", free=False),
            OpenWrtRadioInfo("radio1", interface="phy1", free=True),
        ]
        page, service = self.fixture(radios)

        usages = {
            "radio0": RadioUsage("radio0", "phy0", "business", "enabled"),
            "radio1": RadioUsage("radio1", "phy1", "idle", "disabled"),
        }

        with patch("modules.capture_services.openwrt.capture_start_preflight.read_radio_usage", return_value=usages), \
             patch("modules.main_pages.packet_capture.openwrt.auto_capture_page.ConfirmDialog") as prompt_cls, \
             patch.object(service, "start_all") as start_all, \
             patch.object(service, "start_radio_capture") as start_single:

            prompt_mock = MagicMock()
            prompt_mock.exec.return_value = 0  # Rejected
            prompt_mock.result_action = ConfirmDialog.RESULT_CANCEL
            prompt_cls.return_value = prompt_mock
            prompt_cls.Accepted = ConfirmDialog.Accepted
            prompt_cls.ActionNo = ConfirmDialog.ActionNo
            prompt_cls.RESULT_YES = ConfirmDialog.RESULT_YES
            prompt_cls.RESULT_NO = ConfirmDialog.RESULT_NO

            page._on_start_all_clicked()
            page._start_preflight._thread.join(2)
            self.app.processEvents()

            start_all.assert_not_called()
            start_single.assert_not_called()

    def test_all_busy_scenario_hides_not_takeover_button(self):
        """Scenario B: all radios are busy. Dialog has only 2 buttons (全部接管 / 全部取消)."""
        radios = [
            OpenWrtRadioInfo("radio0", interface="phy0", free=False),
            OpenWrtRadioInfo("radio1", interface="phy1", free=False),
        ]
        page, service = self.fixture(radios)

        usages = {
            "radio0": RadioUsage("radio0", "phy0", "business", "enabled"),
            "radio1": RadioUsage("radio1", "phy1", "business", "enabled"),
        }

        with patch("modules.capture_services.openwrt.capture_start_preflight.read_radio_usage", return_value=usages), \
             patch("modules.main_pages.packet_capture.openwrt.auto_capture_page.ConfirmDialog") as prompt_cls, \
             patch.object(service, "start_all") as start_all:

            prompt_mock = MagicMock()
            prompt_mock.exec.return_value = 0
            prompt_cls.return_value = prompt_mock
            prompt_cls.Accepted = ConfirmDialog.Accepted

            page._on_start_all_clicked()
            page._start_preflight._thread.join(2)
            self.app.processEvents()

            prompt_cls.assert_called_once()
            _, kwargs = prompt_cls.call_args
            self.assertEqual(kwargs.get("yes_text"), "全部接管")
            self.assertEqual(kwargs.get("no_text"), "全部取消")
            self.assertFalse(kwargs.get("cancel_text"))  # No third button
            start_all.assert_not_called()


if __name__ == "__main__":
    unittest.main()

