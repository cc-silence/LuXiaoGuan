# -*- coding: utf-8 -*-
"""自动抓包转换 CAP 开关、合并启停按钮与拉取后清理路由器抓包目录测试。"""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from PySide6.QtWidgets import QApplication

from modules.capture_services.openwrt.auto_capture_boot_manager import (
    AutoCaptureBootManager,
    AutoCaptureBootStatus,
)
from modules.capture_services.openwrt.auto_capture_service import (
    AutoCaptureService,
)
from modules.capture_services.openwrt.capture_products import (
    cap_product,
    cap_product_note,
)
from modules.capture_services.openwrt.command_runner import RemoteCommandResult
from modules.capture_services.openwrt.models import (
    OpenWrtConnectionState,
    OpenWrtDeviceInfo,
    OpenWrtRadioInfo,
    OpenWrtSessionSnapshot,
)
from modules.config_services.capture_settings_manager import CaptureSettingsManager
from modules.main_pages.packet_capture.openwrt.auto_capture_page import (
    AutoCapturePage,
)


class AutoCaptureCapToggleAndPullCleanupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_settings_default_auto_capture_generate_cap_is_false(self):
        """验证 capture_settings 默认 auto_capture_generate_cap 为 False，且常规抓包 generate_cap 保持原状。"""
        with tempfile.TemporaryDirectory() as td:
            mgr = CaptureSettingsManager(path=Path(td) / "test_settings.json")
            cfg = mgr.load()
            # 常规抓包设置不受影响，仍为 True
            self.assertTrue(cfg.get("openwrt", {}).get("generate_cap", False))
            # 自动抓包独立开关默认为 False
            self.assertFalse(cfg.get("openwrt", {}).get("auto_capture_generate_cap", True))

            # 验证当显式保存 auto_capture_generate_cap=True 时可正常保存与回显
            cfg["openwrt"]["auto_capture_generate_cap"] = True
            mgr.save(cfg)
            reloaded = mgr.load()
            self.assertTrue(reloaded.get("openwrt", {}).get("auto_capture_generate_cap", False))

            # 验证当显式保存 auto_capture_generate_cap=False 时正确保留
            cfg["openwrt"]["auto_capture_generate_cap"] = False
            mgr.save(cfg)
            reloaded2 = mgr.load()
            self.assertFalse(reloaded2.get("openwrt", {}).get("auto_capture_generate_cap", True))

    def test_cap_product_disabled_short_circuit(self):
        """验证 cap_product 与 cap_product_note 在 enabled=False 时完全禁用转换。"""
        with tempfile.TemporaryDirectory() as td:
            dummy_pcapng = Path(td) / "test.pcapng"
            dummy_pcapng.write_bytes(b"\x0a\x0d\x0d\x0a" + b"\x00" * 32)

            prod = cap_product(dummy_pcapng, enabled=False)
            self.assertIsNone(prod)

            prod, note = cap_product_note(dummy_pcapng, enabled=False)
            self.assertIsNone(prod)
            self.assertIn("未开启同时输出 CAP 格式", note)

    def test_is_any_capture_running_detection(self):
        """验证 AutoCaptureService.is_any_capture_running 对前后台状态的识别。"""
        owner = MagicMock()
        owner.openwrt_backend.command_runner = MagicMock()
        owner.openwrt_backend.ssh_client = MagicMock()
        service = AutoCaptureService(owner)

        self.assertFalse(service.is_any_capture_running())

        # 前台运行中
        session = MagicMock()
        session.stats.state = "capturing"
        service.sessions["radio0"] = session
        self.assertTrue(service.is_any_capture_running())

        session.stats.state = "idle"
        self.assertFalse(service.is_any_capture_running())

        # 后台运行中
        service._boot_status = AutoCaptureBootStatus(is_running=True)
        self.assertTrue(service.is_any_capture_running())

        service._boot_status = AutoCaptureBootStatus(is_running=False)
        self.assertFalse(service.is_any_capture_running())

    def test_boot_manager_clear_remote_artifacts_refuses_when_running(self):
        """验证第二道防线：抓包运行中 clear_remote_capture_artifacts 拒绝清理。"""
        runner = MagicMock()
        ssh = MagicMock()
        bm = AutoCaptureBootManager(runner, ssh)

        bm.probe_boot_status = MagicMock(return_value=AutoCaptureBootStatus(is_running=True))
        success, msg = bm.clear_remote_capture_artifacts()
        self.assertFalse(success)
        self.assertIn("任务正在运行", msg)
        runner.run.assert_not_called()

        # 抓包停止状态下正常执行清理
        bm.probe_boot_status = MagicMock(return_value=AutoCaptureBootStatus(is_running=False))
        runner.run.return_value = RemoteCommandResult(
            command_name="clear_cap_dir", exit_code=0, stdout="CLEAR_OK\n", stderr="", cancelled=False
        )
        success2, msg2 = bm.clear_remote_capture_artifacts()
        self.assertTrue(success2)
        self.assertIn("已成功清空", msg2)
        runner.run.assert_called_once()
        self.assertIn("/root/cap/*.pcapng", runner.run.call_args[0][1])

    def test_foreground_toggle_all_button_switching(self):
        """验证前台『全部开始 / 全部停止』合并按钮文案切换与交互。"""
        service = MagicMock()
        service.auto_capture = MagicMock()
        service.auto_capture.get_output_dir.return_value = Path(tempfile.gettempdir())
        service.auto_capture.is_busy = False
        service.auto_capture.sessions = {}
        service.auto_capture.boot_status = None

        settings = {"auto_capture_generate_cap": False}
        page = AutoCapturePage(service, settings)

        # 连接路由器
        device = OpenWrtDeviceInfo(
            model="Test",
            architecture="x86_64",
            version="23.05.0",
            radios=(OpenWrtRadioInfo(name="radio0", interface="phy0", bands=("5G",), free=True),),
        )
        snapshot = OpenWrtSessionSnapshot(
            connection_state=OpenWrtConnectionState.CONNECTED,
            device=device,
            dependencies={},
        )
        page._on_snapshot_changed(snapshot)

        # 空闲状态：显示全部开始
        self.assertIn("全部开始", page.toggle_all_btn.text())

        # 模拟抓包开始，进入繁忙状态：显示全部停止
        service.auto_capture.is_busy = True
        page._update_toggle_all_btn_state(snapshot)
        self.assertIn("全部停止", page.toggle_all_btn.text())

        # 点击切换按钮触发停止
        with patch.object(page, "_on_stop_all_clicked") as mock_stop:
            page._on_toggle_all_clicked()
            mock_stop.assert_called_once()

        # 模拟抓包结束，返回空闲状态：显示全部开始
        service.auto_capture.is_busy = False
        page._update_toggle_all_btn_state(snapshot)
        self.assertIn("全部开始", page.toggle_all_btn.text())

        # 点击切换按钮触发开始
        with patch.object(page, "_on_start_all_clicked") as mock_start:
            page._on_toggle_all_clicked()
            mock_start.assert_called_once()

    @patch("modules.main_pages.packet_capture.openwrt.auto_capture_page.ConfirmDialog")
    def test_foreground_pull_prompts_unified_cleanup(self, mock_confirm):
        """验证前台停止后自动拉取完成，触发统一文案的清理询问，并在确定时清空。"""
        service = MagicMock()
        service.auto_capture = MagicMock()
        service.auto_capture.get_output_dir.return_value = Path(tempfile.gettempdir())
        service.auto_capture.is_any_capture_running.return_value = False
        service.auto_capture.sessions = {}
        service.auto_capture.boot_status = None

        settings = {"auto_capture_generate_cap": False}
        page = AutoCapturePage(service, settings)

        dialog_instance = MagicMock()
        dialog_instance.exec.return_value = 1  # Accepted
        mock_confirm.return_value = dialog_instance
        mock_confirm.Accepted = 1

        # 触发前台拉取完成
        page._on_foreground_capture_pulled("/tmp/test.pcapng")

        mock_confirm.assert_called_once()
        self.assertEqual(mock_confirm.call_args[0][1], "握手包已经拉取到本地，是否清空路由器上的文件？")
        service.auto_capture.clear_remote_cap_artifacts_async.assert_called_once()

    @patch("modules.main_pages.packet_capture.openwrt.auto_capture_page.ConfirmDialog")
    def test_foreground_pull_skips_cleanup_if_other_radio_running(self, mock_confirm):
        """验证前台若仍有其它射频正在抓包（第二道防线），跳过清理询问。"""
        service = MagicMock()
        service.auto_capture = MagicMock()
        service.auto_capture.get_output_dir.return_value = Path(tempfile.gettempdir())
        service.auto_capture.is_any_capture_running.return_value = True
        service.auto_capture.sessions = {}
        service.auto_capture.boot_status = None

        settings = {"auto_capture_generate_cap": False}
        page = AutoCapturePage(service, settings)

        # 触发前台拉取完成，但此时另一个射频还在抓包
        page._on_foreground_capture_pulled("/tmp/test.pcapng")

        # 绝不弹窗询问，也绝不调用清空
        mock_confirm.assert_not_called()
        service.auto_capture.clear_remote_cap_artifacts_async.assert_not_called()

    @patch("modules.main_pages.packet_capture.openwrt.auto_capture_page.ConfirmDialog")
    def test_background_pull_offline_prompts_unified_cleanup(self, mock_confirm):
        """验证后台离线成果拉取完成，同样触发统一文案的清理询问。"""
        service = MagicMock()
        service.auto_capture = MagicMock()
        service.auto_capture.get_output_dir.return_value = Path(tempfile.gettempdir())
        service.auto_capture.is_any_capture_running.return_value = False
        service.auto_capture.sessions = {}
        service.auto_capture.boot_status = None

        settings = {"auto_capture_generate_cap": False}
        page = AutoCapturePage(service, settings)

        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"valid_packet_data")
            test_file = f.name

        try:
            dialog_instance = MagicMock()
            dialog_instance.exec.return_value = 1  # Accepted
            mock_confirm.return_value = dialog_instance
            mock_confirm.Accepted = 1

            page._on_pull_offline_success([test_file], tempfile.gettempdir())

            mock_confirm.assert_called_once()
            self.assertEqual(mock_confirm.call_args[0][1], "握手包已经拉取到本地，是否清空路由器上的文件？")
            service.auto_capture.clear_remote_cap_artifacts_async.assert_called_once()
        finally:
            if os.path.exists(test_file):
                os.remove(test_file)


    def test_stop_buttons_publish_shared_theme_state(self):
        """验证所有停止抓包按钮（前台合并按钮、后台收尾按钮、射频卡片停止按钮）在停止态均显示警示红色前景。"""
        from modules.main_pages.packet_capture.openwrt.auto_capture_radio_card import AutoCaptureRadioCard

        service = MagicMock()
        service.auto_capture = MagicMock()
        service.auto_capture.get_output_dir.return_value = Path(tempfile.gettempdir())
        service.auto_capture.is_busy = False
        service.auto_capture.sessions = {}
        service.auto_capture.boot_status = None

        r0 = OpenWrtRadioInfo(name="radio0", interface="phy0", bands=("5G",), free=True)
        device = OpenWrtDeviceInfo(model="Test", architecture="arm", radios=(r0,))
        snapshot = OpenWrtSessionSnapshot(
            connection_state=OpenWrtConnectionState.CONNECTED,
            device=device,
        )
        service.openwrt_snapshot = snapshot

        page = AutoCapturePage(service, {})
        card = AutoCaptureRadioCard(r0)

        # 1. 验证前台单按钮切换至停止态时的警示红色前景
        service.auto_capture.is_busy = True
        page._update_toggle_all_btn_state(snapshot)
        self.assertTrue(page.toggle_all_btn.property("stopAction"))

        # 切换浅色主题
        page.set_light_theme(True)
        self.assertTrue(page.toggle_all_btn.property("stopAction"))

        # 恢复空闲状态
        service.auto_capture.is_busy = False
        page._update_toggle_all_btn_state(snapshot)
        self.assertEqual(page.toggle_all_btn.styleSheet(), "")

        # 2. 验证后台收尾按钮处于运行/收尾态时的警示红色前景
        page.set_light_theme(False)
        status_running = AutoCaptureBootStatus(is_running=True)
        service.auto_capture.boot_status = status_running
        page._update_start_script_btn_state(snapshot, status_running)
        self.assertTrue(page.start_script_btn.property("stopAction"))

        page.set_light_theme(True)
        self.assertTrue(page.start_script_btn.property("stopAction"))

        # 后台空闲时恢复常规
        status_idle = AutoCaptureBootStatus(is_running=False)
        service.auto_capture.boot_status = status_idle
        page._update_start_script_btn_state(snapshot, status_idle)
        self.assertEqual(page.start_script_btn.styleSheet(), "")

        # 3. 验证射频卡片抓包/停止态时 start_btn 与 stop_btn 的警示红色前景
        card.set_light_theme(False)
        card.apply_state("capturing")
        self.assertTrue(card.start_btn.property("stopAction"))
        self.assertTrue(card.stop_btn.property("stopAction"))

        card.apply_state("stopping")
        self.assertTrue(card.start_btn.property("stopAction"))

        card.set_light_theme(True)
        self.assertTrue(card.start_btn.property("stopAction"))
        self.assertTrue(card.stop_btn.property("stopAction"))

        # 卡片空闲时恢复常规
        card.apply_state("idle")
        self.assertEqual(card.start_btn.styleSheet(), "")

    def test_foreground_and_background_mutual_exclusion_disable_page(self):
        """验证前后台互斥：启动前台抓包时后台整页禁用；启动后台抓包时前台整页禁用；均未启动时双页可用。"""
        service = MagicMock()
        service.auto_capture = MagicMock()
        service.auto_capture.get_output_dir.return_value = Path(tempfile.gettempdir())
        service.auto_capture.is_busy = False
        service.auto_capture.sessions = {}
        service.auto_capture.boot_status = None

        r0 = OpenWrtRadioInfo(name="radio0", interface="phy0", bands=("5G",), free=True)
        device = OpenWrtDeviceInfo(model="Test", architecture="arm", radios=(r0,))
        snapshot = OpenWrtSessionSnapshot(
            connection_state=OpenWrtConnectionState.CONNECTED,
            device=device,
        )
        service.openwrt_snapshot = snapshot

        page = AutoCapturePage(service, {})
        page._on_snapshot_changed(snapshot)

        # 初始均未运行：前后台页面均可用
        self.assertTrue(page.foreground_tab.isEnabled())
        self.assertTrue(page.background_tab.isEnabled())

        # 场景 A: 前台抓包运行中 -> 后台整页禁用
        service.auto_capture.is_busy = True
        service.auto_capture.boot_status = AutoCaptureBootStatus(is_running=False)
        page._update_mutual_exclusion_state(snapshot)
        self.assertTrue(page.foreground_tab.isEnabled())
        self.assertFalse(page.background_tab.isEnabled())
        self.assertIn("前台抓包正在运行中，后台抓包已禁用", page.sub_tabs.tabToolTip(1))

        # 前台抓包停止 -> 双页恢复可用
        service.auto_capture.is_busy = False
        page._update_mutual_exclusion_state(snapshot)
        self.assertTrue(page.foreground_tab.isEnabled())
        self.assertTrue(page.background_tab.isEnabled())

        # 场景 B: 后台抓包运行中 -> 前台整页禁用
        service.auto_capture.boot_status = AutoCaptureBootStatus(is_running=True)
        page._update_mutual_exclusion_state(snapshot)
        self.assertFalse(page.foreground_tab.isEnabled())
        self.assertTrue(page.background_tab.isEnabled())
        self.assertIn("后台抓包正在运行中，前台抓包已禁用", page.sub_tabs.tabToolTip(0))

        # 后台抓包停止 -> 双页恢复可用
        service.auto_capture.boot_status = AutoCaptureBootStatus(is_running=False)
        page._update_mutual_exclusion_state(snapshot)
        self.assertTrue(page.foreground_tab.isEnabled())
        self.assertTrue(page.background_tab.isEnabled())

    @patch("modules.main_pages.packet_capture.openwrt.auto_capture_page.MessageDialog")
    def test_mutual_exclusion_blocks_cross_start_actions(self, mock_msg_dialog):
        """验证互斥防护：当前台/后台某一方运行中时，另一方的启动入口被弹窗拦截，杜绝并发启动。"""
        service = MagicMock()
        service.auto_capture = MagicMock()
        service.auto_capture.get_output_dir.return_value = Path(tempfile.gettempdir())
        service.auto_capture.is_busy = False
        service.auto_capture.sessions = {}
        service.auto_capture.boot_status = None

        r0 = OpenWrtRadioInfo(name="radio0", interface="phy0", bands=("5G",), free=True)
        device = OpenWrtDeviceInfo(model="Test", architecture="arm", radios=(r0,))
        snapshot = OpenWrtSessionSnapshot(
            connection_state=OpenWrtConnectionState.CONNECTED,
            device=device,
        )
        service.openwrt_snapshot = snapshot

        page = AutoCapturePage(service, {})
        page._on_snapshot_changed(snapshot)

        # 1. 后台在运行，尝试启动前台抓包被拦截
        service.auto_capture.boot_status = AutoCaptureBootStatus(is_running=True)
        page._on_start_all_clicked()
        mock_msg_dialog.assert_called()
        self.assertIn("互斥功能", mock_msg_dialog.call_args[0][1])
        service.auto_capture.start_all.assert_not_called()

        mock_msg_dialog.reset_mock()
        page._on_card_start_requested(r0, "", False)
        mock_msg_dialog.assert_called()
        self.assertIn("互斥功能", mock_msg_dialog.call_args[0][1])

        # 2. 前台在运行，尝试手动启动后台抓包被拦截
        service.auto_capture.boot_status = AutoCaptureBootStatus(is_running=False)
        service.auto_capture.is_busy = True
        mock_msg_dialog.reset_mock()
        page._on_start_or_stop_script_clicked()
        mock_msg_dialog.assert_called()
        self.assertIn("互斥功能", mock_msg_dialog.call_args[0][1])
        service.auto_capture.start_boot_script.assert_not_called()

if __name__ == "__main__":
    unittest.main()
