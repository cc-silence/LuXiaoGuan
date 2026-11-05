# -*- coding: utf-8 -*-
"""Unit tests for automatic packet capture missing dependency warning, one-click install, and console clearing on start."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import MagicMock, patch

from PySide6.QtWidgets import QApplication

from modules.capture_services.openwrt.auto_capture_service import AutoCaptureService
from modules.capture_services.openwrt.models import (
    DependencyInfo,
    DependencyState,
    OpenWrtConnectionState,
    OpenWrtDeviceInfo,
    OpenWrtRadioInfo,
    OpenWrtSessionSnapshot,
)
from modules.main_pages.packet_capture.openwrt.auto_capture_page import (
    AutoCapturePage,
    check_auto_capture_environment,
)
from modules.main_pages.packet_capture.openwrt.auto_capture_radio_card import (
    AutoCaptureRadioCard,
)


class AutoCaptureDepMissingAndConsoleClearTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _make_service(self, snapshot=None):
        service = MagicMock()
        service.openwrt_backend = MagicMock()
        service.openwrt_backend.command_runner = MagicMock()
        service.openwrt_backend.ssh_client = MagicMock()
        service.openwrt_backend.ssh_client.connected = True
        service.settings = {}

        auto_cap_service = AutoCaptureService(service)
        service.auto_capture = auto_cap_service
        service.openwrt_snapshot = snapshot

        # Dependency service mock
        dep_service = MagicMock()
        dep_service.last_rejection_reason = ""
        dep_service.install_async.return_value = True
        service.dependency_service = dep_service

        return service

    def test_check_auto_capture_environment_dep_missing(self):
        """测试 check_auto_capture_environment 对缺失 hcxdumptool 依赖的准确判定。"""
        r0 = OpenWrtRadioInfo(name="radio0", interface="phy0", bands=("2.4G",), free=True)
        device = OpenWrtDeviceInfo(model="Xiaomi AX3600", version="23.05.4", radios=(r0,))

        # 1. 没有 device -> (False, "", "")
        self.assertEqual(check_auto_capture_environment(None, None), (False, "", ""))

        # 2. pmkid 依赖状态为 MISSING -> (True, 'auto_capture_dep_missing', ...)
        dep_missing = DependencyInfo(
            key="pmkid",
            display_name="hcxdumptool (PMKID)",
            state=DependencyState.MISSING,
            detail="缺少命令: hcxdumptool",
            missing_packages=("hcxdumptool",),
        )
        snap_missing = OpenWrtSessionSnapshot(
            connection_state=OpenWrtConnectionState.CONNECTED,
            device=device,
            dependencies=(dep_missing,),
        )
        has_issue, issue_type, issue_detail = check_auto_capture_environment(device, snap_missing)
        self.assertTrue(has_issue)
        self.assertEqual(issue_type, "auto_capture_dep_missing")
        self.assertIn("未安装", issue_detail)

        # 3. pmkid 依赖在 dependencies 列表中缺失（只有 core），但有其他依赖已探测
        dep_core = DependencyInfo(
            key="core",
            display_name="核心系统",
            state=DependencyState.READY,
            detail="已就绪",
        )
        snap_no_pmkid = OpenWrtSessionSnapshot(
            connection_state=OpenWrtConnectionState.CONNECTED,
            device=device,
            dependencies=(dep_core,),
        )
        has_issue2, issue_type2, _ = check_auto_capture_environment(device, snap_no_pmkid)
        self.assertTrue(has_issue2)
        self.assertEqual(issue_type2, "auto_capture_dep_missing")

        # 4. pmkid 依赖正常且为现代版 6.3.4 -> (False, "", "")
        dep_modern = DependencyInfo(
            key="pmkid",
            display_name="hcxdumptool (PMKID)",
            state=DependencyState.READY,
            detail="hcxdumptool v6.3.4 (现代版)",
        )
        snap_ready = OpenWrtSessionSnapshot(
            connection_state=OpenWrtConnectionState.CONNECTED,
            device=device,
            dependencies=(dep_modern,),
        )
        has_issue3, issue_type3, _ = check_auto_capture_environment(device, snap_ready)
        self.assertFalse(has_issue3)
        self.assertEqual(issue_type3, "")

        # 5. pmkid 依赖为旧版 6.2.4 且设备支持 6.3.4 -> (True, "hcx_outdated_upgradeable", ...)
        dep_legacy = DependencyInfo(
            key="pmkid",
            display_name="hcxdumptool (PMKID)",
            state=DependencyState.UPGRADABLE,
            detail="hcxdumptool v6.2.4 (旧版，需升级)",
        )
        snap_legacy = OpenWrtSessionSnapshot(
            connection_state=OpenWrtConnectionState.CONNECTED,
            device=device,
            dependencies=(dep_legacy,),
        )
        has_issue4, issue_type4, _ = check_auto_capture_environment(device, snap_legacy)
        self.assertTrue(has_issue4)
        self.assertEqual(issue_type4, "hcx_outdated_upgradeable")

        # 6. pmkid 正常，但 sftp 缺失 -> (True, "auto_capture_dep_missing", ...)
        dep_sftp_missing = DependencyInfo(
            key="sftp",
            display_name="文件传输",
            state=DependencyState.MISSING,
            detail="文件传输支持未安装",
            missing_packages=("openssh-sftp-server",),
        )
        snap_sftp_missing = OpenWrtSessionSnapshot(
            connection_state=OpenWrtConnectionState.CONNECTED,
            device=device,
            dependencies=(dep_modern, dep_sftp_missing),
        )
        has_issue5, issue_type5, issue_detail5 = check_auto_capture_environment(device, snap_sftp_missing)
        self.assertTrue(has_issue5)
        self.assertEqual(issue_type5, "auto_capture_dep_missing")
        self.assertIn("文件传输", issue_detail5)

        # 7. pmkid 正常，但 signal 缺失 -> (True, "auto_capture_dep_missing", ...)
        dep_signal_missing = DependencyInfo(
            key="signal",
            display_name="信号管理",
            state=DependencyState.MISSING,
            detail="缺少：lua、libuci-lua",
            missing_packages=("lua", "libuci-lua"),
        )
        snap_signal_missing = OpenWrtSessionSnapshot(
            connection_state=OpenWrtConnectionState.CONNECTED,
            device=device,
            dependencies=(dep_modern, dep_signal_missing),
        )
        has_issue6, issue_type6, issue_detail6 = check_auto_capture_environment(device, snap_signal_missing)
        self.assertTrue(has_issue6)
        self.assertEqual(issue_type6, "auto_capture_dep_missing")
        self.assertIn("信号管理", issue_detail6)

        # 8. pmkid 正常，但 iw 缺失 -> (True, "auto_capture_dep_missing", ...)
        dep_iw_missing = DependencyInfo(
            key="iw",
            display_name="无线管理工具",
            state=DependencyState.MISSING,
            detail="缺少命令：iw",
            missing_packages=("iw",),
        )
        snap_iw_missing = OpenWrtSessionSnapshot(
            connection_state=OpenWrtConnectionState.CONNECTED,
            device=device,
            dependencies=(dep_modern, dep_iw_missing),
        )
        has_issue7, issue_type7, issue_detail7 = check_auto_capture_environment(device, snap_iw_missing)
        self.assertTrue(has_issue7)
        self.assertEqual(issue_type7, "auto_capture_dep_missing")
        self.assertIn("iw", issue_detail7)

    def test_warning_cards_display_on_dep_missing(self):
        """测试当缺失依赖时，前台与后台页面均展示警告卡片，并提供一键安装按钮。"""
        r0 = OpenWrtRadioInfo(name="radio0", interface="phy0", bands=("2.4G",), free=True)
        device = OpenWrtDeviceInfo(model="Xiaomi AX3600", version="23.05.4", radios=(r0,))
        dep_missing = DependencyInfo(
            key="pmkid",
            display_name="hcxdumptool (PMKID)",
            state=DependencyState.MISSING,
            detail="未安装 hcxdumptool",
            missing_packages=("hcxdumptool",),
        )
        snapshot = OpenWrtSessionSnapshot(
            connection_state=OpenWrtConnectionState.CONNECTED,
            device=device,
            dependencies=(dep_missing,),
        )

        service = self._make_service(snapshot)
        page = AutoCapturePage(service, {})
        self.addCleanup(page.deleteLater)
        self.addCleanup(page.dispose)
        page.show()

        # 验证前台警告卡片已展示，内容与按钮正确
        self.assertFalse(page.fg_warning_card.isHidden())
        self.assertTrue(page.fg_warning_card.isVisible())
        self.assertIn("未安装自动抓包依赖", page.fg_warning_title.text())
        self.assertIn("一键安装抓包依赖", page.fg_warning_btn.text())

        # 验证后台警告卡片已展示，内容与按钮正确
        self.assertFalse(page.bg_warning_card.isHidden())
        self.assertIn("未安装自动抓包依赖", page.bg_warning_title.text())
        self.assertIn("一键安装抓包依赖", page.bg_warning_btn.text())

        # 验证向后兼容引用
        self.assertIs(page.version_warning_card, page.bg_warning_card)
        self.assertIs(page.upgrade_hcx_btn, page.bg_warning_btn)

        # 验证亮暗主题切换不报错且保持正确样式
        page.set_light_theme(True)
        self.assertTrue(page._light_theme)
        page.set_light_theme(False)
        self.assertFalse(page._light_theme)

    def test_warning_card_action_click_triggers_dependency_install(self):
        """测试点击前后台警告卡片上的一键安装按钮，会触发依赖安装流程。"""
        r0 = OpenWrtRadioInfo(name="radio0", interface="phy0", bands=("2.4G",), free=True)
        device = OpenWrtDeviceInfo(model="Xiaomi AX3600", version="23.05.4", radios=(r0,))
        dep_missing = DependencyInfo(
            key="pmkid",
            display_name="hcxdumptool (PMKID)",
            state=DependencyState.MISSING,
            detail="未安装 hcxdumptool",
            missing_packages=("hcxdumptool",),
        )
        snapshot = OpenWrtSessionSnapshot(
            connection_state=OpenWrtConnectionState.CONNECTED,
            device=device,
            dependencies=(dep_missing,),
        )

        service = self._make_service(snapshot)
        page = AutoCapturePage(service, {})
        self.addCleanup(page.deleteLater)
        self.addCleanup(page.dispose)

        # 点击前台卡片一键安装按钮
        with patch.object(page, "_on_install_dependency_clicked") as mock_install:
            page.fg_warning_btn.click()
            mock_install.assert_called_once()

        # 点击后台卡片一键安装按钮
        with patch.object(page, "_on_install_dependency_clicked") as mock_install2:
            page.bg_warning_btn.click()
            mock_install2.assert_called_once()

        # 真实触发 _on_install_dependency_clicked 时调用 dep_service.install_async
        page._on_install_dependency_clicked()
        service.dependency_service.install_async.assert_called_once_with(
            {"pmkid": True, "signal": True, "iw": True}, scope="auto_capture"
        )

    def test_start_foreground_card_blocked_with_confirm_dialog_when_dep_missing(self):
        """测试当前台单射频启动抓包时若缺失依赖，弹出确认对话框引导安装并阻止启动。"""
        r0 = OpenWrtRadioInfo(name="radio0", interface="phy0", bands=("2.4G",), free=True)
        device = OpenWrtDeviceInfo(model="Xiaomi AX3600", version="23.05.4", radios=(r0,))
        dep_missing = DependencyInfo(
            key="pmkid",
            display_name="hcxdumptool (PMKID)",
            state=DependencyState.MISSING,
            detail="未安装 hcxdumptool",
            missing_packages=("hcxdumptool",),
        )
        snapshot = OpenWrtSessionSnapshot(
            connection_state=OpenWrtConnectionState.CONNECTED,
            device=device,
            dependencies=(dep_missing,),
        )

        service = self._make_service(snapshot)
        page = AutoCapturePage(service, {})
        self.addCleanup(page.deleteLater)
        self.addCleanup(page.dispose)

        with patch("modules.main_pages.packet_capture.openwrt.auto_capture_page.ConfirmDialog") as mock_dialog_cls, \
             patch.object(page, "_on_install_dependency_clicked") as mock_install, \
             patch.object(service.auto_capture, "start_radio_capture") as mock_start:

            mock_dialog = MagicMock()
            mock_dialog.exec.return_value = 1  # ConfirmDialog.Accepted
            mock_dialog_cls.Accepted = 1
            mock_dialog_cls.return_value = mock_dialog

            page._on_card_start_requested(r0, "6", False)

            # 对话框应弹出
            mock_dialog_cls.assert_called_once()
            self.assertIn("未安装抓包依赖", mock_dialog_cls.call_args.args[0])
            # 用户点击确认后触发安装
            mock_install.assert_called_once()
            # 抓包未启动
            mock_start.assert_not_called()

    def test_start_foreground_all_blocked_with_confirm_dialog_when_dep_missing(self):
        """测试当前台“全部开始”抓包时若缺失依赖，弹出确认对话框引导安装并阻止启动。"""
        r0 = OpenWrtRadioInfo(name="radio0", interface="phy0", bands=("2.4G",), free=True)
        device = OpenWrtDeviceInfo(model="Xiaomi AX3600", version="23.05.4", radios=(r0,))
        dep_missing = DependencyInfo(
            key="pmkid",
            display_name="hcxdumptool (PMKID)",
            state=DependencyState.MISSING,
            detail="未安装 hcxdumptool",
            missing_packages=("hcxdumptool",),
        )
        snapshot = OpenWrtSessionSnapshot(
            connection_state=OpenWrtConnectionState.CONNECTED,
            device=device,
            dependencies=(dep_missing,),
        )

        service = self._make_service(snapshot)
        page = AutoCapturePage(service, {})
        self.addCleanup(page.deleteLater)
        self.addCleanup(page.dispose)

        with patch("modules.main_pages.packet_capture.openwrt.auto_capture_page.ConfirmDialog") as mock_dialog_cls, \
             patch.object(page, "_on_install_dependency_clicked") as mock_install, \
             patch.object(service.auto_capture, "start_all") as mock_start_all:

            mock_dialog = MagicMock()
            mock_dialog.exec.return_value = 1  # ConfirmDialog.Accepted
            mock_dialog_cls.Accepted = 1
            mock_dialog_cls.return_value = mock_dialog

            page._on_start_all_clicked()

            # 对话框应弹出
            mock_dialog_cls.assert_called_once()
            self.assertIn("未安装抓包依赖", mock_dialog_cls.call_args.args[0])
            mock_install.assert_called_once()
            mock_start_all.assert_not_called()

    def test_start_background_script_blocked_with_confirm_dialog_when_dep_missing(self):
        """测试当后台启动抓包脚本时若缺失依赖，弹出确认对话框引导安装并阻止启动。"""
        r0 = OpenWrtRadioInfo(name="radio0", interface="phy0", bands=("2.4G",), free=True)
        device = OpenWrtDeviceInfo(model="Xiaomi AX3600", version="23.05.4", radios=(r0,))
        dep_missing = DependencyInfo(
            key="pmkid",
            display_name="hcxdumptool (PMKID)",
            state=DependencyState.MISSING,
            detail="未安装 hcxdumptool",
            missing_packages=("hcxdumptool",),
        )
        snapshot = OpenWrtSessionSnapshot(
            connection_state=OpenWrtConnectionState.CONNECTED,
            device=device,
            dependencies=(dep_missing,),
        )

        service = self._make_service(snapshot)
        page = AutoCapturePage(service, {})
        self.addCleanup(page.deleteLater)
        self.addCleanup(page.dispose)

        with patch("modules.main_pages.packet_capture.openwrt.auto_capture_page.ConfirmDialog") as mock_dialog_cls, \
             patch.object(page, "_on_install_dependency_clicked") as mock_install, \
             patch.object(service.auto_capture, "install_and_start_boot_script") as mock_start_boot:

            mock_dialog = MagicMock()
            mock_dialog.exec.return_value = 1  # ConfirmDialog.Accepted
            mock_dialog_cls.Accepted = 1
            mock_dialog_cls.return_value = mock_dialog

            page._on_start_script_clicked()

            # 对话框应弹出
            mock_dialog_cls.assert_called_once()
            self.assertIn("未安装抓包依赖", mock_dialog_cls.call_args.args[0])
            mock_install.assert_called_once()
            mock_start_boot.assert_not_called()

    def test_foreground_console_cleared_on_start(self):
        """测试前台终端在启动抓包时清空上次的历史内容。"""
        r0 = OpenWrtRadioInfo(name="radio0", interface="phy0", bands=("2.4G",), free=True)
        card = AutoCaptureRadioCard(r0)
        self.addCleanup(card.deleteLater)

        # 1. 模拟卡片终端已有上次运行的输出
        card.console.setPlainText("--- Previous capture session log line 1 ---\n--- Previous line 2 ---")
        self.assertIn("Previous", card.console.toPlainText())

        # 2. 点击开始时清空终端
        card._on_start_clicked()
        self.assertEqual(card.console.toPlainText(), "")

        # 3. 再次填充日志，测试从 idle -> starting 状态转换时清空终端
        card.console.setPlainText("Residual terminal text")
        card._capturing = False
        card.apply_state("starting")
        self.assertEqual(card.console.toPlainText(), "")

    def test_background_console_cleared_on_start(self):
        """测试后台终端在手动启动抓包时清空上次的历史内容，只保留当前运行输出。"""
        r0 = OpenWrtRadioInfo(name="radio0", interface="phy0", bands=("2.4G",), free=True)
        device = OpenWrtDeviceInfo(model="Xiaomi AX3600", version="23.05.4", radios=(r0,))
        dep_modern = DependencyInfo(
            key="pmkid",
            display_name="hcxdumptool (PMKID)",
            state=DependencyState.READY,
            detail="hcxdumptool v6.3.4 (现代版)",
        )
        snapshot = OpenWrtSessionSnapshot(
            connection_state=OpenWrtConnectionState.CONNECTED,
            device=device,
            dependencies=(dep_modern,),
        )

        service = self._make_service(snapshot)
        page = AutoCapturePage(service, {})
        self.addCleanup(page.deleteLater)
        self.addCleanup(page.dispose)

        # 预先向后台终端写入旧会话日志
        page.bg_console.setPlainText("=== OLD SESSION LOG DUMP ===\nPrevious stats: 100 packets\n")
        self.assertIn("OLD SESSION", page.bg_console.toPlainText())

        # 模拟 boot_status 为已部署脚本但未运行
        from modules.capture_services.openwrt.auto_capture_boot_manager import AutoCaptureBootStatus
        service.auto_capture._boot_status = AutoCaptureBootStatus(
            installed=True,
            autostart_enabled=False,
            remote_script_exists=True,
            remote_version="6.3.4",
            is_running=False,
        )

        with patch("modules.main_pages.packet_capture.openwrt.auto_capture_page.ConfirmDialog") as mock_dialog_cls, \
             patch.object(service.auto_capture, "start_boot_script") as mock_start:

            mock_dialog = MagicMock()
            mock_dialog.exec.return_value = 1
            mock_dialog_cls.Accepted = 1
            mock_dialog_cls.return_value = mock_dialog

            page._on_start_script_clicked()

            # 旧日志已被清空，不应再包含旧数据
            text = page.bg_console.toPlainText()
            self.assertNotIn("OLD SESSION", text)
            # 包含新会话启动提示
            self.assertIn("正在手动启动后台抓包脚本", text)

    def test_boot_checkbox_blocked_with_confirm_dialog_when_dep_missing(self):
        """测试当开启开机自启复选框时若缺失依赖，弹出确认对话框引导安装并将复选框重置。"""
        r0 = OpenWrtRadioInfo(name="radio0", interface="phy0", bands=("2.4G",), free=True)
        device = OpenWrtDeviceInfo(model="Xiaomi AX3600", version="23.05.4", radios=(r0,))
        dep_missing = DependencyInfo(
            key="pmkid",
            display_name="hcxdumptool (PMKID)",
            state=DependencyState.MISSING,
            detail="未安装 hcxdumptool",
            missing_packages=("hcxdumptool",),
        )
        snapshot = OpenWrtSessionSnapshot(
            connection_state=OpenWrtConnectionState.CONNECTED,
            device=device,
            dependencies=(dep_missing,),
        )

        service = self._make_service(snapshot)
        page = AutoCapturePage(service, {})
        self.addCleanup(page.deleteLater)
        self.addCleanup(page.dispose)

        from modules.capture_services.openwrt.auto_capture_boot_manager import AutoCaptureBootStatus
        service.auto_capture._boot_status = AutoCaptureBootStatus(
            installed=True,
            autostart_enabled=False,
            remote_script_exists=True,
            remote_version="6.3.4",
            is_running=False,
        )

        with patch("modules.main_pages.packet_capture.openwrt.auto_capture_page.ConfirmDialog") as mock_dialog_cls, \
             patch.object(page, "_on_install_dependency_clicked") as mock_install, \
             patch.object(service.auto_capture, "toggle_autostart") as mock_toggle:

            mock_dialog = MagicMock()
            mock_dialog.exec.return_value = 1  # ConfirmDialog.Accepted
            mock_dialog_cls.Accepted = 1
            mock_dialog_cls.return_value = mock_dialog

            page._on_boot_check_clicked(True)

            mock_dialog_cls.assert_called_once()
            self.assertIn("未安装抓包依赖", mock_dialog_cls.call_args.args[0])
            mock_install.assert_called_once()
            mock_toggle.assert_not_called()
            self.assertFalse(page.boot_enable_check.isChecked())


if __name__ == "__main__":
    unittest.main()
