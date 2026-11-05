"""OpenWrt 自动抓包主页面。

集成：
1. 顶部标题栏与视图模式切换（同屏左右并排显示 / 分屏多标签页切换）；
2. 开机自启服务管理面板（状态、开关同步、版本比对更新、完全卸载）；
3. 全局总控与系统资源守卫看板（内存与 Flash 剩余容量实时告警、全部开始/停止）；
4. 探测即创建的多路独立射频卡片容器；
5. 离线成果一键拉取与 PC 端智能日志诊断汇总弹窗。
"""

import os
from pathlib import Path
import re
import threading
from typing import Dict, List, Optional

from PySide6.QtCore import Qt, Signal, QTimer, QSize, QObject
from .service_page_slots import active_service_slot
from PySide6.QtGui import QBrush, QColor, QFont, QTextCursor, QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSplitter,
    QStyle,
    QStyleOptionTab,
    QStylePainter,
    QTabBar,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from modules.capture_services.openwrt.auto_capture_boot_manager import AutoCaptureBootStatus
from modules.capture_services.openwrt.dependency_manager import is_hcx_outdated
from modules.capture_services.openwrt.auto_capture_log_analyzer import AutoCaptureDiagnosticReport
from modules.capture_services.capture_logging import LOGGER
from modules.capture_services.openwrt.models import OpenWrtRadioInfo
from modules.common_widgets.bounce_scroll_area import BounceScrollArea
from modules.common_widgets.common_dialogs import ConfirmDialog, MessageDialog, _create_dialog_button
from modules.common_widgets.frameless_dialog import FramelessDialog
from modules.config_manager import ConfigManager
from modules.dialog_theme import build_base_stylesheet, detect_light_theme
from modules.main_pages.packet_capture.ui_helpers import create_capture_card, create_step_card, VerticalResizeHandle
from modules.main_pages.page_action_buttons import create_page_text_button
from .auto_capture_button_styles import apply_stop_button_style
from modules.ui_scale_manager import UiScaleManager
from .auto_capture_radio_card import AutoCaptureRadioCard


class RadioCardsSideTabBar(QTabBar):
    """侧边 (East/West) 水平文字标签栏。

    在 QTabWidget.East/West 布局下，Qt 默认会将标签文字旋转 90 度竖立显示，影响阅读体验。
    本组件在侧边布局下委托基础样式绘制背景与边框后，以水平左对齐方式绘制文字。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setShape(QTabBar.RoundedEast)
        self.setDrawBase(False)
        self.setElideMode(Qt.ElideNone)
        self._light_theme = False

    def set_light_theme(self, enabled: bool):
        self._light_theme = bool(enabled)
        self.update()

    def tabSizeHint(self, index: int) -> QSize:
        scale = UiScaleManager.for_widget(self)
        fm = self.fontMetrics()
        text = self.tabText(index)
        text_width = fm.horizontalAdvance(text) if hasattr(fm, "horizontalAdvance") else fm.width(text)
        w = max(scale.px(135), text_width + scale.px(28))
        h = max(scale.px(38), fm.height() + scale.px(16))
        return QSize(w, h)

    def paintEvent(self, event):
        painter = QStylePainter(self)
        scale = UiScaleManager.for_widget(self)
        pad_left = scale.px(12)
        for index in range(self.count()):
            opt = QStyleOptionTab()
            self.initStyleOption(opt, index)
            text = opt.text
            opt.text = ""  # 清空文本，让 QStyle 只绘制 tab 外观与交互背景
            painter.drawControl(QStyle.CE_TabBarTab, opt)

            rect = self.tabRect(index)
            text_rect = rect.adjusted(pad_left, 0, -scale.px(4), 0)
            is_selected = bool(opt.state & QStyle.State_Selected)
            color = (
                QColor("#344258" if is_selected else "#52627a")
                if self._light_theme
                else QColor("#ffffff" if is_selected else "#aeb8c6")
            )
            painter.save()
            painter.setPen(color)
            font = painter.font()
            if is_selected:
                font.setBold(True)
            painter.setFont(font)
            painter.drawText(text_rect, Qt.AlignLeft | Qt.AlignVCenter, text)
            painter.restore()


RadioCardsWestTabBar = RadioCardsSideTabBar


from .auto_capture_report_dialog import AutoCaptureReportDialog


class AutoCaptureGuideDialog(FramelessDialog):
    """现场脱机抓包工作流程指南弹窗。"""

    def __init__(self, output_dir: str, parent=None):
        super().__init__("现场脱机抓包工作流程指南", parent, inherit_parent_stylesheet=False)
        self.light_theme = detect_light_theme(parent, fallback=False)
        scale = UiScaleManager.for_widget(self)
        base_stylesheet = build_base_stylesheet(ConfigManager(), light_theme=self.light_theme, scale_manager=scale)
        self.setStyleSheet(base_stylesheet)
        self.set_scaled_size((680, 480), (840, 580))

        card, layout = create_capture_card(
            self,
            "现场脱机自主抓包核心工作流 (无需电脑连线)",
            "利用 OpenWrt 系统的守护服务，让路由器在现场自主全自动抓包，适合外出采集与多场地移动巡检。",
            collapsible=False,
        )
        steps_text = (
            "【步骤 1 · 部署自启守护服务】\n"
            "在【后台抓包】页面确认系统守护服务已就绪；如提示未安装或需升级，点击“上传/同步脚本与服务”。\n\n"
            "【步骤 2 · 开启自启或手动启动】\n"
            "勾选“开机自启抓包”开关。抓包脚本只会使用未开启 Wi-Fi 的空闲无线频段，正在正常上网的 Wi-Fi 业务不受任何影响。\n\n"
            "【步骤 3 · 现场断网插电自主抓包】\n"
            "拔下网线并将路由器放置在目标现场，接通电源开机即可自动启动抓包。\n"
            "捕获到的握手包与 PMKID 每 30 秒自动安全写入存储 (/root/cap/)，测试完毕直接拔掉电源也不会丢失数据。\n\n"
            "【步骤 4 · 带回连线一键拉取与智能诊断】\n"
            f"现场测试完成后，带回路由器插上网线连上电脑，点击“一键拉取握手包”同步到本地电脑 ({output_dir})。\n"
            "随后点击“智能分析日志”，系统将自动评估握手包有效性、周边 AP 热点分布与设备运行健康度报告。"
        )
        steps_lbl = QLabel(steps_text, card)
        steps_lbl.setObjectName("captureDescription")
        steps_lbl.setWordWrap(True)
        steps_lbl.setStyleSheet("line-height: 160%; font-size: 13px; padding: 6px;")
        layout.addWidget(steps_lbl)
        self.content_layout.addWidget(card)

        self.button_layout.addStretch(1)
        close_btn = _create_dialog_button("我知道了", self, "settingsPrimaryButton")
        close_btn.clicked.connect(self.accept)
        self.button_layout.addWidget(close_btn)


def is_modern_hcx_supported_on_device(device) -> bool:
    """Use the same upgrade policy as dependency installation."""
    from modules.capture_services.openwrt.hcx_policy import supports_modern_hcx
    return supports_modern_hcx(device)


def check_auto_capture_environment(device, snapshot=None) -> tuple[bool, str, str]:
    """判定自动抓包运行环境与工具版本兼容性。
    只有系统真正支持现代版 6.3.4+，却依然使用旧版 6.2.x/6.0.x 时，才给出升级警告。
    当系统不支持 6.3.4 时，使用官方适配的旧版工具（WEXT 兼容模式）属于正常兼容状态，不误报警告。
    返回 (has_issue, issue_type, issue_detail)。
    issue_type: 'hcx_outdated_upgradeable' | ''
    """
    if not device:
        return False, "", ""

    # 优先检查实机上的 hcxdumptool 版本（动态提取实际版本，不硬编码 6.2.4）
    if snapshot is not None and hasattr(snapshot, "dependencies"):
        pmkid_dep = next((d for d in snapshot.dependencies if getattr(d, "key", "") == "pmkid"), None)
        if pmkid_dep:
            detail = getattr(pmkid_dep, "detail", "") or ""
            dep_state_val = getattr(pmkid_dep.state, "value", pmkid_dep.state)
            vm = re.search(r"hcxdumptool\s+v?(\d+\.\d+(?:\.\d+)?)", detail) or re.search(r"v?(\d+\.\d+(?:\.\d+)?)", detail)
            detected_ver = vm.group(1) if vm else ""

            is_outdated = False
            if detected_ver:
                is_outdated = is_hcx_outdated(detected_ver)
            else:
                is_outdated = (dep_state_val in ("upgradable", "outdated")
                               or any(k in detail for k in ("旧版", "需升级", "不支持自动抓包")))

            if is_outdated:
                # 只有系统真正支持 6.3.4，却使用的旧版本，才给出升级警告
                if is_modern_hcx_supported_on_device(device):
                    ver_label = f"hcxdumptool {detected_ver}" if detected_ver else "旧版 hcxdumptool"
                    return True, "hcx_outdated_upgradeable", f"检测到旧版抓包工具 ({ver_label}，需升级)"

    return False, "", ""


class AutoCapturePage(QWidget):
    """自动抓包主页面控件。"""

    settingsChanged = Signal()

    def __init__(self, service, settings, parent=None):
        super().__init__(parent)
        self.service = service
        self.auto_capture_service = service.auto_capture
        self.settings = settings
        self._light_theme = False
        self._view_mode = "split_screen"  # "split_screen" (同屏并排) | "tabbed" (分屏多标签)
        self.radio_cards: Dict[str, AutoCaptureRadioCard] = {}

        self.setObjectName("openwrtAutoCapturePage")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 4, 0)
        root.setSpacing(6)

        # 顶层子标签页：将前台抓包与后台抓包分开放入两个独立子页面中
        self.sub_tabs = QTabWidget(self)
        self.sub_tabs.setObjectName("openwrtCaptureTabs")
        root.addWidget(self.sub_tabs)

        # ----------------- 子标签页 1：前台抓包 -----------------
        self.foreground_tab = QWidget(self.sub_tabs)
        self.foreground_tab.setObjectName("autoCaptureForegroundTab")
        fg_root = QVBoxLayout(self.foreground_tab)
        fg_root.setContentsMargins(0, 0, 0, 0)
        fg_root.setSpacing(0)

        self.fg_scroll = BounceScrollArea(self.foreground_tab)
        self.fg_scroll.setObjectName("captureScrollArea")
        self.fg_scroll.setWidgetResizable(True)
        self.fg_scroll.setFrameShape(BounceScrollArea.NoFrame)
        fg_root.addWidget(self.fg_scroll)

        self.fg_content = QWidget(self.fg_scroll)
        self.fg_content.setObjectName("openwrtAutoCaptureFgContent")
        self.fg_scroll.setWidget(self.fg_content)

        self.fg_layout = QVBoxLayout(self.fg_content)
        self.fg_layout.setContentsMargins(0, 4, 0, 0)
        self.fg_layout.setSpacing(8)

        # 保持对外部属性访问的向后兼容
        self.scroll_area = self.fg_scroll
        self.content_widget = self.fg_content
        self.main_layout = self.fg_layout

        # 1. 射频卡片容器区 (置于前台视图上方主要区域)
        self._build_cards_container()

        # 2. 全局总控与系统资源守卫控制栏 (置于前台视图最下方)
        self._build_guard_section()

        # ----------------- 子标签页 2：后台抓包 -----------------
        self.background_tab = QWidget(self.sub_tabs)
        self.background_tab.setObjectName("autoCaptureBackgroundTab")
        bg_root = QVBoxLayout(self.background_tab)
        bg_root.setContentsMargins(0, 0, 0, 0)
        bg_root.setSpacing(0)

        self.bg_scroll = BounceScrollArea(self.background_tab)
        self.bg_scroll.setObjectName("captureScrollArea")
        self.bg_scroll.setWidgetResizable(True)
        self.bg_scroll.setFrameShape(BounceScrollArea.NoFrame)
        bg_root.addWidget(self.bg_scroll)

        self.bg_content = QWidget(self.bg_scroll)
        self.bg_content.setObjectName("openwrtAutoCaptureBgContent")
        self.bg_scroll.setWidget(self.bg_content)

        self.bg_layout = QVBoxLayout(self.bg_content)
        self.bg_layout.setContentsMargins(0, 4, 0, 0)
        self.bg_layout.setSpacing(8)

        # 0. 顶部状态提示横幅
        self._build_status_banner()

        # 1. 开机自启管理面板
        self._build_boot_section()

        # 2. 现场脱机抓包工作流程指南
        self._build_offline_workflow_section()

        # 装配子标签页
        self.sub_tabs.addTab(self.foreground_tab, "前台抓包")
        self.sub_tabs.addTab(self.background_tab, "后台抓包")
        self.sub_tabs.currentChanged.connect(self._on_sub_tab_changed)

        # 连接服务信号
        self._service_disposed = False
        self._service_connections = []
        from modules.capture_services.openwrt.capture_start_preflight import CaptureStartPreflight
        self._start_preflight = CaptureStartPreflight(self.auto_capture_service, self)
        self._connect_service_signal(self._start_preflight.finished, self._on_start_inspected)
        self._connect_service_signal(self.auto_capture_service.bootStatusChecking, self._on_boot_status_checking)
        self._connect_service_signal(self.auto_capture_service.bootStatusChanged, self._on_boot_status_changed)
        self._connect_service_signal(self.auto_capture_service.bootScriptStarted, self._on_boot_script_started)
        self._connect_service_signal(self.auto_capture_service.resourceStatsUpdated, self._on_resource_stats_updated)
        self._connect_service_signal(self.auto_capture_service.radioStatsUpdated, self._on_radio_stats_updated)
        self._connect_service_signal(self.auto_capture_service.radioStreamOutput, self._on_radio_stream_output)
        self._connect_service_signal(self.auto_capture_service.radioStateChanged, self._on_radio_state_changed)
        self._connect_service_signal(self.auto_capture_service.messageGenerated, self._on_message_generated)
        self._connect_service_signal(self.auto_capture_service.offlineArtifactsPulled, self._on_pull_offline_success)
        self._connect_service_signal(self.auto_capture_service.offlinePullFailed, self._on_pull_offline_error)
        self._connect_service_signal(self.auto_capture_service.radioIdleStatusUpdated, self._on_radio_idle_status_updated)
        self._connect_service_signal(self.auto_capture_service.bootLogUpdated, self._on_boot_log_updated)
        self._connect_service_signal(self.auto_capture_service.remoteCapDirCleared, self._on_remote_cap_cleared)
        self._connect_service_signal(self.auto_capture_service.foregroundCapturePulled, self._on_foreground_capture_pulled)
        self._cleanup_prompt_showing = False
        self._pending_foreground_cleanup = False
        self._is_pulling_offline = False
        self._is_starting_foreground = False
        self._bg_console_manually_hidden = False

        signals_service = getattr(self.service, "router_signals", None)
        if signals_service is not None and hasattr(signals_service, "snapshotChanged"):
            self._connect_service_signal(signals_service.snapshotChanged, self._on_signals_snapshot_changed)

        # 连接建立时检测运行中抓包进程的弹窗控制标记
        self._connected_running_prompted = False
        self._initial_loaded = False
        self._last_loading_state_key = None
        self._last_rendered_boot_status = None

        # 后台抓包状态自动巡检定时器 (仅在切换到后台抓包且已连接时运行)
        self._bg_status_timer = QTimer(self)
        self._bg_status_timer.setInterval(15000)  # 放宽至 15 秒巡检间隔，降低设备负荷并消除高频刷新打扰
        self._bg_status_timer.timeout.connect(self._poll_bg_boot_status)

        # 终端实时日志监看采用原生 SSH tail -f 流式实时管道（事件驱动，零定时器）

        # 若服务层已有自启状态快照，立即回显展示
        if self.auto_capture_service.boot_status is not None:
            self._on_boot_status_changed(self.auto_capture_service.boot_status)

        self._connect_service_signal(service.openwrtSnapshotChanged, self._on_snapshot_changed)
        self._on_snapshot_changed(service.openwrt_snapshot)

    def _connect_service_signal(self, signal, slot):
        self._service_connections.append(signal.connect(slot))

    def _poll_bg_console_log(self):
        snapshot = getattr(self, "_last_snapshot", None) or getattr(self.service, "openwrt_snapshot", None)
        conn_state = getattr(snapshot, "connection_state", None)
        connected = bool(snapshot and getattr(conn_state, "value", conn_state) == "connected")
        if connected and hasattr(self, "bg_console_card") and self.bg_console_card.isVisible():
            if not getattr(self.auto_capture_service, "is_boot_log_streaming", lambda: False)():
                self.auto_capture_service.poll_boot_log()

    def _poll_bg_boot_status(self):
        """周期性轮询刷新后台自启与脚本状态。"""
        snapshot = getattr(self, "_last_snapshot", None) or getattr(self.service, "openwrt_snapshot", None)
        conn_state = getattr(snapshot, "connection_state", None)
        connected = bool(snapshot and getattr(conn_state, "value", conn_state) == "connected")
        if connected and self.sub_tabs.currentIndex() == 1:
            self.auto_capture_service.refresh_boot_status()
            if hasattr(self, "bg_console_card") and self.bg_console_card.isVisible():
                if not getattr(self.auto_capture_service, "is_boot_log_streaming", lambda: False)():
                    self.auto_capture_service.poll_boot_log()

    @active_service_slot(object)
    def _on_signals_snapshot_changed(self, snapshot):
        """当在信号管理页面修改了信号状态后，自动触发前台射频业务状态异步刷新。"""
        conn_state = getattr(self.service.openwrt_snapshot, "connection_state", None)
        if getattr(conn_state, "value", conn_state) != "connected":
            self._last_signal_inventory = None
            return
        inventory = getattr(snapshot, "inventory", None)
        if (inventory is None or getattr(snapshot, "busy", False) or getattr(snapshot, "stale", False)
                or inventory is getattr(self, "_last_signal_inventory", None)):
            return
        self._last_signal_inventory = inventory
        self.auto_capture_service.refresh_radio_idle_status()

    @active_service_slot(dict)
    def _on_radio_idle_status_updated(self, free_map: dict):
        """远端射频业务状态异步返回后，立即精准刷新射频卡片的空闲/使用中徽章与接管复选框。"""
        for radio_name, is_free in free_map.items():
            card = self.radio_cards.get(radio_name)
            if card is not None:
                from dataclasses import replace
                from modules.capture_services.openwrt.radio_usage_state import radio_usage_fields
                card.radio_info = replace(card.radio_info, **radio_usage_fields(is_free))
                if not card._capturing:
                    from .radio_usage_view import apply_usage_status
                    apply_usage_status(card)

    def _on_output_cap_toggled(self, checked: bool):
        """同步前后台的「同时输出cap格式」勾选状态并保存至配置。"""
        if hasattr(self, "output_cap_check"):
            self.output_cap_check.blockSignals(True)
            self.output_cap_check.setChecked(checked)
            self.output_cap_check.blockSignals(False)
        if hasattr(self, "bg_output_cap_check"):
            self.bg_output_cap_check.blockSignals(True)
            self.bg_output_cap_check.setChecked(checked)
            self.bg_output_cap_check.blockSignals(False)
        self.settings["auto_capture_generate_cap"] = checked
        if hasattr(self.service, "settings") and isinstance(self.service.settings, dict):
            self.service.settings["auto_capture_generate_cap"] = checked
        try:
            from modules.config_services.capture_settings_manager import CaptureSettingsManager
            mgr = CaptureSettingsManager()
            all_cfg = mgr.load()
            all_cfg.setdefault("openwrt", {})["auto_capture_generate_cap"] = checked
            mgr.save(all_cfg)
        except Exception as exc:
            LOGGER.warning("Save auto_capture_generate_cap setting failed: %s", exc)

    def _update_pull_buttons_state(self, snapshot=None):
        """统一管理前后台一键拉取按钮的可用性与 ToolTip（第一道防线：运行中禁用拉取）。"""
        if snapshot is None:
            if hasattr(self, "_last_snapshot"):
                snapshot = self._last_snapshot
            else:
                snapshot = getattr(self.service, "openwrt_snapshot", None)
        conn_state = getattr(snapshot, "connection_state", None)
        connected = bool(snapshot and getattr(conn_state, "value", conn_state) == "connected")
        running = getattr(self.auto_capture_service, "is_any_capture_running", lambda: False)()
        is_pulling = getattr(self, "_is_pulling_offline", False)
        can_pull = connected and not running and not is_pulling

        btn = getattr(self, "pull_offline_btn", None)
        if btn is not None:
            btn.setEnabled(can_pull)
            if running:
                btn.setToolTip("当前有抓包任务正在运行中，为避免影响抓包或误删数据已禁用拉取。\n请先停止抓包后再执行拉取。")
            elif not connected:
                btn.setToolTip("请先连接路由器")
            elif is_pulling:
                btn.setToolTip("正在拉取抓包成果中，请稍候...")
            else:
                out_dir = self.auto_capture_service.get_output_dir()
                btn.setToolTip(f"从路由器 /root/cap/ 下载全部抓包文件与日志到本地电脑：\n【本地保存位置】：{out_dir}")

    def _update_toggle_all_btn_state(self, snapshot=None):
        """统一管理前台『全部开始 / 全部停止』单按钮切换状态。"""
        if not hasattr(self, "toggle_all_btn"):
            return
        if snapshot is None:
            if hasattr(self, "_last_snapshot"):
                snapshot = self._last_snapshot
            else:
                snapshot = getattr(self.service, "openwrt_snapshot", None)
        conn_state = getattr(snapshot, "connection_state", None)
        connected = bool(snapshot and getattr(conn_state, "value", conn_state) == "connected")
        device = getattr(snapshot, "device", None)
        radios = list(device.radios) if (connected and device and hasattr(device, "radios")) else []

        bg_status = getattr(self.auto_capture_service, "boot_status", None)
        bg_running = bool(bg_status and bg_status.is_running) or getattr(self, "_manual_boot_started", False)

        is_busy = getattr(self.auto_capture_service, "is_busy", False)
        if is_busy:
            self.toggle_all_btn.setText("⏹ 全部停止")
            self.toggle_all_btn.setToolTip("正在抓包中\n停止全部正在进行的抓包并恢复 Wi-Fi 正常状态。")
            self.toggle_all_btn.setEnabled(connected)
            apply_stop_button_style(self.toggle_all_btn, True, self._light_theme)
        elif bg_running:
            self.toggle_all_btn.setText("▶ 全部开始抓包")
            self.toggle_all_btn.setToolTip("后台抓包运行中，前台已禁用（前后台互斥）")
            self.toggle_all_btn.setEnabled(False)
            apply_stop_button_style(self.toggle_all_btn, False, self._light_theme)
        else:
            self.toggle_all_btn.setText("▶ 全部开始抓包")
            self.toggle_all_btn.setToolTip("启动所有可用射频并发抓包")
            self.toggle_all_btn.setEnabled(connected and bool(radios))
            apply_stop_button_style(self.toggle_all_btn, False, self._light_theme)

    def _update_start_script_btn_state(self, snapshot=None, status=None):
        """统一管理后台抓包手动启动/收尾按钮文本、使能与警示红前景样式。"""
        if not hasattr(self, "start_script_btn"):
            return
        if snapshot is None:
            if hasattr(self, "_last_snapshot"):
                snapshot = self._last_snapshot
            else:
                snapshot = getattr(self.service, "openwrt_snapshot", None)
        conn_state = getattr(snapshot, "connection_state", None)
        connected = bool(snapshot and getattr(conn_state, "value", conn_state) == "connected")

        if status is None:
            status = getattr(self.auto_capture_service, "boot_status", None)

        fg_running = getattr(self.auto_capture_service, "is_busy", False) or getattr(self, "_is_starting_foreground", False)
        boot_running = bool(status and status.is_running)

        if not connected:
            self.start_script_btn.setText("▶ 手动启动脚本")
            self.start_script_btn.setEnabled(False)
            self.start_script_btn.setToolTip("请先连接路由器")
            apply_stop_button_style(self.start_script_btn, False, self._light_theme)
        elif fg_running:
            self.start_script_btn.setText("▶ 手动启动脚本")
            self.start_script_btn.setEnabled(False)
            self.start_script_btn.setToolTip("前台抓包运行中，后台已禁用（前后台互斥）")
            apply_stop_button_style(self.start_script_btn, False, self._light_theme)
        elif boot_running or getattr(self, "_manual_boot_started", False):
            self.start_script_btn.setText("⏹ 结束并收尾抓包")
            self.start_script_btn.setEnabled(True)
            self.start_script_btn.setToolTip("后台脚本正在运行\n停止后台抓包并安全保存数据，自动恢复无线网络正常状态。")
            apply_stop_button_style(self.start_script_btn, True, self._light_theme)
        else:
            self.start_script_btn.setText("▶ 手动启动脚本")
            self.start_script_btn.setEnabled(True)
            self.start_script_btn.setToolTip("立即在后台启动抓包\n【说明】：只会使用未开启 Wi-Fi 的空闲无线频段，正在正常上网的 Wi-Fi 不受任何影响；抓包数据实时保存，断电不丢失")
            apply_stop_button_style(self.start_script_btn, False, self._light_theme)

    def _update_mutual_exclusion_state(self, snapshot=None):
        """前后台抓包互斥管理：同一时刻只允许启动其中一种，启动时将另一方整页禁用。"""
        if not hasattr(self, "foreground_tab") or not hasattr(self, "background_tab"):
            return

        fg_running = getattr(self.auto_capture_service, "is_busy", False) or getattr(self, "_is_starting_foreground", False)
        bg_status = getattr(self.auto_capture_service, "boot_status", None)
        bg_running = bool(bg_status and bg_status.is_running) or getattr(self, "_manual_boot_started", False)

        if fg_running:
            self.foreground_tab.setEnabled(True)
            self.background_tab.setEnabled(False)
            self.sub_tabs.setTabToolTip(0, "正在进行前台并发抓包")
            self.sub_tabs.setTabToolTip(1, "当前前台抓包正在运行中，后台抓包已禁用（前后台互斥）")
        elif bg_running:
            self.foreground_tab.setEnabled(False)
            self.background_tab.setEnabled(True)
            self.sub_tabs.setTabToolTip(0, "当前后台抓包正在运行中，前台抓包已禁用（前后台互斥）")
            self.sub_tabs.setTabToolTip(1, "正在进行后台自主抓包")
        else:
            self.foreground_tab.setEnabled(True)
            self.background_tab.setEnabled(True)
            self.sub_tabs.setTabToolTip(0, "")
            self.sub_tabs.setTabToolTip(1, "")

    def _on_toggle_all_clicked(self):
        """响应前台全部启停单按钮点击：繁忙时停止，空闲时开始。"""
        if getattr(self.auto_capture_service, "is_busy", False):
            self.toggle_all_btn.setText("⏳ 正在停止抓包...")
            self.toggle_all_btn.setEnabled(False)
            apply_stop_button_style(self.toggle_all_btn, True, self._light_theme)
            self._on_stop_all_clicked()
        else:
            self.toggle_all_btn.setText("⏳ 正在启动抓包...")
            self.toggle_all_btn.setEnabled(False)
            apply_stop_button_style(self.toggle_all_btn, False, self._light_theme)
            self._is_starting_foreground = True
            self._update_mutual_exclusion_state()
            self._on_start_all_clicked()

    def _on_sub_tab_changed(self, index: int):
        """子标签页切换时响应：每当切换到前台抓包或后台抓包时，均刷新对应页面上的状态。"""
        if index == 0:
            self._bg_status_timer.stop()
            self.auto_capture_service.stop_boot_log_stream()
            self.refresh_foreground_status()
        elif index == 1:
            self.refresh_background_status()
            if hasattr(self, "bg_console_card") and self.bg_console_card.isVisible():
                self.auto_capture_service.start_boot_log_stream()

    def refresh_foreground_status(self):
        """刷新前台抓包页面上的所有状态（快照同步、射频卡片状态与回显、系统资源）。"""
        snapshot = getattr(self.service, "openwrt_snapshot", None)
        self._on_snapshot_changed(snapshot)

        conn_state = getattr(snapshot, "connection_state", None)
        connected = bool(snapshot and getattr(conn_state, "value", conn_state) == "connected")

        device = getattr(snapshot, "device", None)
        radios = list(device.radios) if (connected and device and hasattr(device, "radios")) else []

        for radio_name, card in self.radio_cards.items():
            session = self.auto_capture_service.sessions.get(radio_name)
            latest_info = next((r for r in radios if r.name == radio_name), None)
            card.refresh_status(session=session, latest_radio_info=latest_info)

        if connected:
            self.auto_capture_service.poll_resources(force=True)
            self.auto_capture_service.refresh_radio_idle_status()
        else:
            self.mem_health_label.setText("可用内存: --")
            self.mem_health_label.setStyleSheet("")
            self.disk_health_label.setText("Flash剩余: --")
            self.disk_health_label.setStyleSheet("")

    def refresh_background_status(self):
        """刷新后台抓包页面上的所有状态（快照同步、环境横幅、自启与运行状态）。"""
        snapshot = getattr(self.service, "openwrt_snapshot", None)
        self._on_snapshot_changed(snapshot)

        conn_state = getattr(snapshot, "connection_state", None)
        connected = bool(snapshot and getattr(conn_state, "value", conn_state) == "connected")

        if connected:
            if self.auto_capture_service.boot_status is not None:
                self._on_boot_status_changed(self.auto_capture_service.boot_status, force=True)
            else:
                self._update_loading_status("checking_env")
            self.auto_capture_service.refresh_boot_status()
            if hasattr(self, "bg_console_card") and self.bg_console_card.isVisible():
                self.auto_capture_service.start_boot_log_stream()
            if not self._bg_status_timer.isActive():
                self._bg_status_timer.start()
        else:
            self._bg_status_timer.stop()
            self._update_loading_status("disconnected")
            self.running_alert_widget.setVisible(False)
            self.start_script_btn.setEnabled(False)
            self.boot_enable_check.setEnabled(False)
            self.boot_enable_check.setChecked(False)
            self.boot_enable_check.setToolTip("请先连接路由器")

    def refresh_current_tab(self):
        """刷新当前激活的子标签页（前台抓包或后台抓包）的状态。"""
        if self.sub_tabs.currentIndex() == 1:
            self.refresh_background_status()
        else:
            self.refresh_foreground_status()

    def enter(self):
        """当外部切换到自动抓包主页面时，刷新当前激活标签页的状态。"""
        self.refresh_current_tab()

    def _build_header_row(self):
        """兼容保留。视图切换功能已直接整合至顶部紧凑控制栏。"""
        pass


    def _on_guide_clicked(self):
        """弹出现场脱机自主抓包工作流使用指南。"""
        out_dir = self.auto_capture_service.get_output_dir()
        dlg = AutoCaptureGuideDialog(str(out_dir), self)
        dlg.exec()
    def _build_status_banner(self):
        """构建后台抓包顶部连接与环境加载状态提示横幅。"""
        self.status_banner = QFrame(self.bg_content)
        self.status_banner.setObjectName("captureCard")
        banner_layout = QVBoxLayout(self.status_banner)
        banner_layout.setContentsMargins(14, 10, 14, 10)
        banner_layout.setSpacing(6)

        # 顶层：图标 + 主标题 + 伸缩 + 三步流程指示
        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(10)

        self.status_banner_icon = QLabel("⚪", self.status_banner)
        self.status_banner_icon.setStyleSheet("font-size: 14pt;")
        top_row.addWidget(self.status_banner_icon)

        self.status_banner_title = QLabel("未连接路由器", self.status_banner)
        self.status_banner_title.setObjectName("captureGroupTitle")
        self.status_banner_title.setStyleSheet("font-size: 11pt; font-weight: bold; color: #94a3b8;")
        top_row.addWidget(self.status_banner_title)

        top_row.addStretch(1)

        # 步骤指示器
        steps_row = QHBoxLayout()
        steps_row.setContentsMargins(0, 0, 0, 0)
        steps_row.setSpacing(6)

        self.step1_label = QLabel("○ 1.登录认证", self.status_banner)
        self.step1_label.setObjectName("captureDescription")
        self.step1_label.setStyleSheet("color: #94a3b8;")
        steps_row.addWidget(self.step1_label)

        self.step_arrow1 = QLabel("→", self.status_banner)
        self.step_arrow1.setObjectName("captureDescription")
        self.step_arrow1.setStyleSheet("color: #64748b;")
        steps_row.addWidget(self.step_arrow1)

        self.step2_label = QLabel("○ 2.读取信息", self.status_banner)
        self.step2_label.setObjectName("captureDescription")
        self.step2_label.setStyleSheet("color: #94a3b8;")
        steps_row.addWidget(self.step2_label)

        self.step_arrow2 = QLabel("→", self.status_banner)
        self.step_arrow2.setObjectName("captureDescription")
        self.step_arrow2.setStyleSheet("color: #64748b;")
        steps_row.addWidget(self.step_arrow2)

        self.step3_label = QLabel("○ 3.环境检测", self.status_banner)
        self.step3_label.setObjectName("captureDescription")
        self.step3_label.setStyleSheet("color: #94a3b8;")
        steps_row.addWidget(self.step3_label)

        # ❓ 使用指南按钮
        steps_row.addSpacing(8)
        self.guide_btn = QPushButton("❓ 使用指南", self.status_banner)
        self.guide_btn.setObjectName("captureSecondaryButton")
        self.guide_btn.setToolTip("查看现场脱机自主抓包的完整流程与使用步骤")
        self.guide_btn.clicked.connect(self._on_guide_clicked)
        steps_row.addWidget(self.guide_btn)

        top_row.addLayout(steps_row)
        banner_layout.addLayout(top_row)

        # 底层：详细状态说明
        self.status_banner_detail = QLabel("请在【路由器登录】页面连接 OpenWrt 路由器设备", self.status_banner)
        self.status_banner_detail.setObjectName("captureDescription")
        self.status_banner_detail.setWordWrap(True)
        banner_layout.addWidget(self.status_banner_detail)

        self._update_status_banner_style()
        self._update_bg_console_style()
        self.bg_layout.addWidget(self.status_banner)

        # 0.1 固件版本过低预警横幅（当 OpenWrt 版本低于 23.05 或内核低于 5.15 时醒目告警）
        self.version_warning_card = QFrame(self.bg_content)
        self.version_warning_card.setObjectName("captureCard")
        vw_layout = QHBoxLayout(self.version_warning_card)
        vw_layout.setContentsMargins(14, 10, 14, 10)
        vw_layout.setSpacing(12)

        self.version_warning_icon = QLabel("⚠️", self.version_warning_card)
        self.version_warning_icon.setStyleSheet("font-size: 16pt;")
        vw_layout.addWidget(self.version_warning_icon)

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(4)

        self.version_warning_title = QLabel("⚠️ 固件版本过低预警（需升级固件至 OpenWrt 23.05+）", self.version_warning_card)
        self.version_warning_title.setStyleSheet("font-size: 11pt; font-weight: bold; color: #f59e0b;")
        text_layout.addWidget(self.version_warning_title)

        self.version_warning_detail = QLabel(self.version_warning_card)
        self.version_warning_detail.setObjectName("captureDescription")
        self.version_warning_detail.setWordWrap(True)
        text_layout.addWidget(self.version_warning_detail)

        vw_layout.addLayout(text_layout, 1)

        # 一键升级到 6.3.4 快捷操作按钮
        self.upgrade_hcx_btn = QPushButton("⚡ 一键升级抓包工具 (6.3.4)", self.version_warning_card)
        self.upgrade_hcx_btn.setObjectName("captureSecondaryButton")
        self.upgrade_hcx_btn.setStyleSheet("color: #f59e0b; font-weight: bold; border-color: #f59e0b; padding: 6px 14px;")
        self.upgrade_hcx_btn.setToolTip("从随包离线库直接为路由器部署现代版 hcxdumptool 6.3.4，无需手动敲命令")
        self.upgrade_hcx_btn.clicked.connect(self._on_upgrade_hcx_clicked)
        vw_layout.addWidget(self.upgrade_hcx_btn)

        self.version_warning_card.setVisible(False)
        self.bg_layout.addWidget(self.version_warning_card)

    def _update_bg_console_style(self):
        """同步后台抓包控制台的明暗主题样式。"""
        if not hasattr(self, "bg_console") or not self.bg_console:
            return
        if self._light_theme:
            self.bg_console.setStyleSheet(
                "QPlainTextEdit { background-color: #f8fafc; color: #0f172a; "
                "border: 1px solid #cbd5e1; border-radius: 4px; padding: 4px; font-family: Consolas, monospace; }"
            )
        else:
            self.bg_console.setStyleSheet(
                "QPlainTextEdit { background-color: #0b0f19; color: #e2e8f0; "
                "border: 1px solid rgba(255, 255, 255, 0.12); border-radius: 4px; padding: 4px; font-family: Consolas, monospace; }"
            )

    def _clear_bg_console(self):
        """清空后台抓包控制台内容。"""
        self._bg_startup_log = ""
        if hasattr(self, "bg_console") and self.bg_console:
            self.bg_console.clear()
    def append_bg_console_text(self, text: str):
        """向后台抓包控制台追加日志。"""
        if not hasattr(self, "bg_console") or not self.bg_console or not text:
            return
        doc = self.bg_console.document()
        if doc and doc.characterCount() > 200000:
            self.bg_console.clear()
        cursor = self.bg_console.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(text)
        if hasattr(self, "bg_autoscroll_check") and self.bg_autoscroll_check.isChecked():
            self.bg_console.moveCursor(QTextCursor.End)

    @active_service_slot(str)
    def _on_boot_log_updated(self, log_text: str):
        """接收后台抓包日志流更新。对齐 SSH 终端的清屏刷新原生体验。"""
        if not hasattr(self, "bg_console_card") or self.bg_console_card.isHidden():
            return
        if not log_text or not log_text.strip():
            return

        import re

        # 检测是否包含 ANSI 终端清屏/光标复位帧 (\x1b[H 或 \x1b[2J 或仪表盘边框)
        if "\x1b[H" in log_text or "\x1b[2J" in log_text or "================================================================" in log_text:
            normalized = log_text.replace("\x1b[2J", "")
            # 按 \x1b[H 或边框切分，提取最新一帧完整仪表盘数据
            raw_chunks = [c for c in normalized.split("\x1b[H") if c.strip()]
            if not raw_chunks:
                return
            latest_chunk = raw_chunks[-1]
            clean_frame = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", latest_chunk).replace("\r", "").strip()

            # 首次接收到帧时，若此前存在启动环境信息且尚未归档，则归档头部启动环境
            if len(raw_chunks) > 1 and not getattr(self, "_bg_startup_log", ""):
                clean_startup = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", raw_chunks[0]).replace("\r", "").strip()
                if "正在并行抓包" in clean_startup or "hcx_autocap.sh" in clean_startup:
                    self._bg_startup_log = clean_startup
            elif not getattr(self, "_bg_startup_log", ""):
                cur = self.bg_console.toPlainText().strip()
                if cur and "================================================================" not in cur:
                    self._bg_startup_log = cur

            # 就地清屏刷新最新状态帧，对齐 SSH 终端交互刷新体验
            startup_header = getattr(self, "_bg_startup_log", "")
            if startup_header:
                self.bg_console.setPlainText(f"{startup_header}\n\n{clean_frame}\n")
            else:
                self.bg_console.setPlainText(f"{clean_frame}\n")

            if hasattr(self, "bg_autoscroll_check") and self.bg_autoscroll_check.isChecked():
                self.bg_console.moveCursor(QTextCursor.End)
            return

        # 普通增量行日志追加
        clean_text = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", log_text).replace("\r", "")
        cur_text = self.bg_console.toPlainText()
        if not cur_text.strip():
            self.append_bg_console_text(f"{clean_text.strip()}\n")
            return
        new_lines_to_add = []
        for line_item in clean_text.splitlines():
            s = line_item.strip()
            if s and s not in cur_text:
                new_lines_to_add.append(line_item)
        if new_lines_to_add:
            self.append_bg_console_text("\n".join(new_lines_to_add) + "\n")

    def _update_status_banner_style(self):
        """同步状态横幅明暗主题样式。"""
        if not hasattr(self, "status_banner") or not self.status_banner:
            return
        if self._light_theme:
            self.status_banner.setStyleSheet(
                "QFrame#captureCard { background: #f8fafc; border: 1px solid #cbd5e1; border-radius: 7px; }"
            )
        else:
            self.status_banner.setStyleSheet(
                "QFrame#captureCard { background: rgba(30, 41, 59, 0.45); border: 1px solid rgba(255, 255, 255, 0.12); border-radius: 7px; }"
            )
        if hasattr(self, "version_warning_card") and self.version_warning_card:
            if self._light_theme:
                self.version_warning_card.setStyleSheet(
                    "QFrame#captureCard { background: #fffbeb; border: 1px solid #f59e0b; border-radius: 7px; }"
                )
                self.version_warning_detail.setStyleSheet("color: #78350f; line-height: 1.4;")
            else:
                self.version_warning_card.setStyleSheet(
                    "QFrame#captureCard { background: rgba(245, 158, 11, 0.15); border: 1px solid #f59e0b; border-radius: 7px; }"
                )
                self.version_warning_detail.setStyleSheet("color: #fef3c7; line-height: 1.4;")

    @active_service_slot()
    def _on_boot_status_checking(self):
        """后台抓包环境探测开始槽函数（仅首次加载未就绪时提示，后续静默巡检不打扰界面）。"""
        if not getattr(self, "_initial_loaded", False):
            snapshot = getattr(self, "_last_snapshot", None) or getattr(self.service, "openwrt_snapshot", None)
            conn_state = getattr(snapshot, "connection_state", None)
            connected = bool(snapshot and getattr(conn_state, "value", conn_state) == "connected")
            if connected:
                self._update_loading_status("checking_env")

    def _update_loading_status(self, stage: str, detail_override: str = ""):
        """更新后台抓包顶部状态横幅与步骤流显示。"""
        if not hasattr(self, "status_banner"):
            return

        if stage == "ready":
            self._initial_loaded = True
        elif stage in {"disconnected", "error"}:
            self._initial_loaded = False

        # 状态阶段与副文本完全一致时直接跳过，杜绝频繁 setStyleSheet 造成多处视图重绘闪烁
        current_state_key = (stage, detail_override)
        if getattr(self, "_last_loading_state_key", None) == current_state_key:
            return
        self._last_loading_state_key = current_state_key

        active_color = "#38bdf8"
        waiting_color = "#94a3b8"
        success_color = "#10b981"
        warning_color = "#f59e0b"
        error_color = "#ef4444"

        snapshot = getattr(self, "_last_snapshot", None) or getattr(self.service, "openwrt_snapshot", None)
        device = getattr(snapshot, "device", None) if snapshot else None
        dev_model = getattr(device, "model", "") or "OpenWrt 路由器"
        dev_arch = getattr(device, "architecture", "") or ""
        dev_radios = len(getattr(device, "radios", ())) if device else 0

        if stage == "logging_in":
            self.status_banner_icon.setText("⏳")
            self.status_banner_title.setText("正在登录路由器...")
            self.status_banner_title.setStyleSheet(f"font-size: 11pt; font-weight: bold; color: {active_color};")
            self.status_banner_detail.setText(detail_override or "正在与路由器建立 SSH 安全通道并验证登录凭据与主机指纹...")
            self.step1_label.setText("⏳ 1.登录认证中...")
            self.step1_label.setStyleSheet(f"color: {active_color}; font-weight: bold;")
            self.step2_label.setText("○ 2.读取信息")
            self.step2_label.setStyleSheet(f"color: {waiting_color};")
            self.step3_label.setText("○ 3.环境检测")
            self.step3_label.setStyleSheet(f"color: {waiting_color};")
            if hasattr(self, "boot_status_badge"):
                self.boot_status_badge.setText("【正在登录路由器...】")
                self.boot_status_badge.setStyleSheet(f"color: {active_color}; font-weight: bold;")

        elif stage == "reading_info":
            self.status_banner_icon.setText("⏳")
            self.status_banner_title.setText("正在读取路由器信息...")
            self.status_banner_title.setStyleSheet(f"font-size: 11pt; font-weight: bold; color: {active_color};")
            self.status_banner_detail.setText(detail_override or "SSH 登录认证成功，正在读取硬件型号、CPU 架构与无线射频配置...")
            self.step1_label.setText("✓ 1.登录成功")
            self.step1_label.setStyleSheet(f"color: {success_color}; font-weight: bold;")
            self.step2_label.setText("⏳ 2.读取信息中...")
            self.step2_label.setStyleSheet(f"color: {active_color}; font-weight: bold;")
            self.step3_label.setText("○ 3.环境检测")
            self.step3_label.setStyleSheet(f"color: {waiting_color};")
            if hasattr(self, "boot_status_badge"):
                self.boot_status_badge.setText("【正在读取路由器信息...】")
                self.boot_status_badge.setStyleSheet(f"color: {active_color}; font-weight: bold;")

        elif stage == "checking_env":
            self.status_banner_icon.setText("⏳")
            self.status_banner_title.setText("正在检测后台抓包环境...")
            self.status_banner_title.setStyleSheet(f"font-size: 11pt; font-weight: bold; color: {warning_color};")
            info_prefix = f"已连接 {dev_model}" + (f" ({dev_arch})" if dev_arch else "")
            self.status_banner_detail.setText(detail_override or f"{info_prefix}，正在检查抓包脚本 (/root/hcx_autocap.sh) 与系统守护服务...")
            self.step1_label.setText("✓ 1.登录成功")
            self.step1_label.setStyleSheet(f"color: {success_color}; font-weight: bold;")
            self.step2_label.setText("✓ 2.信息就绪")
            self.step2_label.setStyleSheet(f"color: {success_color}; font-weight: bold;")
            self.step3_label.setText("⏳ 3.检测环境中...")
            self.step3_label.setStyleSheet(f"color: {warning_color}; font-weight: bold;")
            if hasattr(self, "boot_status_badge"):
                self.boot_status_badge.setText("【检测抓包环境中...】")
                self.boot_status_badge.setStyleSheet(f"color: {warning_color}; font-weight: bold;")

        elif stage == "ready":
            self.status_banner_icon.setText("✓")
            self.status_banner_title.setText("后台抓包环境已彻底加载完成")
            self.status_banner_title.setStyleSheet(f"font-size: 11pt; font-weight: bold; color: {success_color};")
            b_status = self.auto_capture_service.boot_status or getattr(self, "_last_boot_status", None)
            script_desc = "抓包脚本已就绪" if (b_status and b_status.remote_script_exists) else "抓包脚本未部署"
            svc_desc = "自启服务已注册" if (b_status and b_status.installed) else ("已配置自启" if (b_status and b_status.autostart_enabled) else "自启未启用")
            run_desc = "脚本运行中（抓包待核实）" if (b_status and b_status.is_running) else "待机就绪"
            detail_msg = f"已连接设备: {dev_model}"
            if dev_arch:
                detail_msg += f" · 架构: {dev_arch}"
            if dev_radios > 0:
                detail_msg += f" · 可用射频: {dev_radios} 路"
            detail_msg += f" · {script_desc} · {svc_desc} · {run_desc}"
            self.status_banner_detail.setText(detail_override or detail_msg)
            self.step1_label.setText("✓ 1.登录成功")
            self.step1_label.setStyleSheet(f"color: {success_color}; font-weight: bold;")
            self.step2_label.setText("✓ 2.信息就绪")
            self.step2_label.setStyleSheet(f"color: {success_color}; font-weight: bold;")
            self.step3_label.setText("✓ 3.环境就绪")
            self.step3_label.setStyleSheet(f"color: {success_color}; font-weight: bold;")

        elif stage == "error":
            self.status_banner_icon.setText("⚠️")
            self.status_banner_title.setText("连接或加载异常")
            self.status_banner_title.setStyleSheet(f"font-size: 11pt; font-weight: bold; color: {error_color};")
            self.status_banner_detail.setText(detail_override or "路由器连接异常或探测失败，请在【路由器登录】页面检查并重新连接。")
            self.step1_label.setText("✕ 1.连接异常")
            self.step1_label.setStyleSheet(f"color: {error_color};")
            self.step2_label.setText("○ 2.读取信息")
            self.step2_label.setStyleSheet(f"color: {waiting_color};")
            self.step3_label.setText("○ 3.环境检测")
            self.step3_label.setStyleSheet(f"color: {waiting_color};")

        else:  # disconnected
            self.status_banner_icon.setText("⚪")
            self.status_banner_title.setText("未连接路由器")
            self.status_banner_title.setStyleSheet(f"font-size: 11pt; font-weight: bold; color: {waiting_color};")
            self.status_banner_detail.setText(detail_override or "请在【路由器登录】页面连接 OpenWrt 路由器设备")
            self.step1_label.setText("○ 1.登录认证")
            self.step1_label.setStyleSheet(f"color: {waiting_color};")
            self.step2_label.setText("○ 2.读取信息")
            self.step2_label.setStyleSheet(f"color: {waiting_color};")
            self.step3_label.setText("○ 3.环境检测")
            self.step3_label.setStyleSheet(f"color: {waiting_color};")

    def _build_boot_section(self):
        """构建后台抓包面板，按清晰的功能步骤顺序组织三个卡片：
        步骤 1：自启守护服务与环境准备；
        步骤 2：抓包启动与自启控制；
        步骤 3：离线成果回收与智能诊断。
        """
        # ================= 步骤 1：自启守护服务与环境准备 =================
        self.boot_card, layout = create_step_card(
            self.bg_content,
            "步骤 1 · 自启守护服务与环境准备",
            "配置 OpenWrt 系统级服务 (/etc/init.d/luxiaoguan-autocap)，为现场脱机抓包提供系统守护支持。",
        )

        # 步骤 1 标题栏最右侧：展示系统服务路径提示
        self.service_path_label = QLabel("⚙ 系统守护服务: /etc/init.d/luxiaoguan-autocap", self.boot_card)
        self.service_path_label.setObjectName("captureDescription")
        self.service_path_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.service_path_label.setStyleSheet("color: #94a3b8; font-size: 12px;")
        if hasattr(self.boot_card, "title_bar"):
            self.boot_card.title_bar.addWidget(self.service_path_label, 0, Qt.AlignRight | Qt.AlignVCenter)

        # 核心状态指标看板：自启服务就绪状态与其他指标并排紧凑展示，字号不缩小
        self.boot_metrics_frame = QFrame(self.boot_card)
        self.boot_metrics_frame.setObjectName("captureCard")
        self._update_boot_metrics_style()
        self._update_status_banner_style()
        metrics_layout = QHBoxLayout(self.boot_metrics_frame)
        metrics_layout.setContentsMargins(14, 8, 14, 8)
        metrics_layout.setSpacing(18)

        # 自启服务主状态徽章（直接与几个状态并排排列，不缩小）
        self.boot_status_badge = QLabel("【检测中...】", self.boot_metrics_frame)
        self.boot_status_badge.setObjectName("captureFormLabel")
        self.boot_status_badge.setStyleSheet("color: #94a3b8; font-weight: bold; font-size: 13px;")
        metrics_layout.addWidget(self.boot_status_badge)
        self.boot_status_label = self.boot_status_badge

        self.metric_script_label = QLabel("📄 抓包脚本: 检测中...", self.boot_metrics_frame)
        self.metric_script_label.setObjectName("captureDescription")
        self.metric_script_label.setStyleSheet("font-size: 13px;")
        metrics_layout.addWidget(self.metric_script_label)
        self.script_loc_label = self.metric_script_label

        self.metric_version_label = QLabel("🏷 版本: --", self.boot_metrics_frame)
        self.metric_version_label.setObjectName("captureDescription")
        self.metric_version_label.setStyleSheet("font-size: 13px;")
        metrics_layout.addWidget(self.metric_version_label)

        self.metric_service_label = QLabel("🛡 系统服务: 检测中...", self.boot_metrics_frame)
        self.metric_service_label.setObjectName("captureDescription")
        self.metric_service_label.setStyleSheet("font-size: 13px;")
        metrics_layout.addWidget(self.metric_service_label)

        # 整体并排后留出弹性空白
        metrics_layout.addStretch(1)

        # 兼容性保留隐藏的 boot_status_desc
        self.boot_status_desc = QLabel("", self.boot_card)
        self.boot_status_desc.setVisible(False)

        layout.addWidget(self.boot_metrics_frame)

        # 版本比对告警行
        self.version_diff_widget = QWidget(self.boot_card)
        v_layout = QHBoxLayout(self.version_diff_widget)
        v_layout.setContentsMargins(0, 0, 0, 0)
        v_layout.setSpacing(10)
        self.version_diff_label = QLabel(self.version_diff_widget)
        self.version_diff_label.setStyleSheet("color: #f59e0b; font-weight: bold; font-size: 12px;")
        v_layout.addWidget(self.version_diff_label)

        self.update_script_btn = QPushButton("▶ 立即更新脚本", self.version_diff_widget)
        self.update_script_btn.setObjectName("downloadActionButton")
        self.update_script_btn.clicked.connect(self.auto_capture_service.install_or_update_boot_service)
        v_layout.addWidget(self.update_script_btn)
        v_layout.addStretch(1)
        self.version_diff_widget.setVisible(False)
        layout.addWidget(self.version_diff_widget)

        # 系统服务部署与卸载按钮
        service_row = QHBoxLayout()
        service_row.setContentsMargins(0, 4, 0, 0)
        service_row.setSpacing(10)

        self.install_boot_btn = create_page_text_button(self.boot_card, "📤 上传/同步脚本与服务", "将本地最新抓包脚本与自启服务部署至路由器")
        self.install_boot_btn.clicked.connect(self.auto_capture_service.install_or_update_boot_service)
        service_row.addWidget(self.install_boot_btn)

        self.uninstall_boot_btn = QPushButton("🗑 完全卸载自启服务", self.boot_card)
        self.uninstall_boot_btn.setObjectName("captureSecondaryButton")
        self.uninstall_boot_btn.setToolTip("完全卸载路由器上的自启服务与抓包脚本（已保存的抓包文件将被保留）")
        self.uninstall_boot_btn.clicked.connect(self._on_uninstall_clicked)
        service_row.addWidget(self.uninstall_boot_btn)

        service_row.addStretch(1)
        layout.addLayout(service_row)
        self.bg_layout.addWidget(self.boot_card)

        # ================= 步骤 2：抓包启动与自启控制 =================
        self.step2_card, step2_layout = create_step_card(
            self.bg_content,
            "步骤 2 · 抓包启动与自启控制",
            "开启开机自启或手动启动后台抓包。仅占用未开启 Wi-Fi 的空闲无线频段，不影响上网业务。",
        )

        # 步骤 2 标题同行右侧并排展示两个纯文本状态（无边框，只留文字，间距 24px）
        # 1. 自启配置状态
        self.metric_autostart_widget = QWidget(self.step2_card)
        self.metric_autostart_widget.setObjectName("autostartStatusBadge")
        as_layout = QHBoxLayout(self.metric_autostart_widget)
        as_layout.setContentsMargins(0, 0, 0, 0)
        as_layout.setSpacing(0)
        self.metric_autostart_widget.setStyleSheet("border: none; background: transparent;")
        self.metric_autostart_label = QLabel("🚀 自启配置: 检测中...", self.metric_autostart_widget)
        self.metric_autostart_label.setStyleSheet("color: #94a3b8; font-weight: bold; font-size: 13px; border: none; background: transparent;")
        as_layout.addWidget(self.metric_autostart_label)

        # 2. 脚本运行状态
        self.running_alert_widget = QWidget(self.step2_card)
        self.running_alert_widget.setObjectName("runningAlertWidget")
        r_layout = QHBoxLayout(self.running_alert_widget)
        r_layout.setContentsMargins(0, 0, 0, 0)
        r_layout.setSpacing(0)
        self.running_alert_widget.setStyleSheet("border: none; background: transparent;")
        self.running_alert_label = QLabel("⚡ 运行状态: 空闲待机", self.running_alert_widget)
        self.running_alert_label.setStyleSheet("color: #94a3b8; font-weight: bold; font-size: 13px; border: none; background: transparent;")
        r_layout.addWidget(self.running_alert_label)
        self.metric_running_label = self.running_alert_label

        if hasattr(self.step2_card, "title_bar"):
            # 将原来位于右上角的两个状态移到左侧标题后紧跟显示（左对齐，加大与标题的间距）
            scale = UiScaleManager.for_widget(self.step2_card)
            self.step2_card.title_bar.insertSpacing(2, scale.px(36))
            self.step2_card.title_bar.insertWidget(3, self.metric_autostart_widget)
            self.step2_card.title_bar.insertSpacing(4, scale.px(20))
            self.step2_card.title_bar.insertWidget(5, self.running_alert_widget)

        # 启动与收尾操作行
        exec_row = QHBoxLayout()
        exec_row.setContentsMargins(2, 4, 0, 0)
        exec_row.setSpacing(12)
        exec_row.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self.boot_enable_check = QCheckBox("开机自启抓包", self.step2_card)
        self.boot_enable_check.setObjectName("captureCheckBox")
        self.boot_enable_check.setEnabled(False)
        self.boot_enable_check.setToolTip("请先连接路由器")
        self.boot_enable_check.clicked.connect(self._on_boot_check_clicked)
        exec_row.addWidget(self.boot_enable_check)

        # 合并后的手动启动/收尾切换按钮
        self.start_script_btn = create_page_text_button(
            self.step2_card,
            "▶ 手动启动脚本",
            "立即在后台启动抓包\n【说明】：只会使用未开启 Wi-Fi 的空闲无线频段，正在正常上网的 Wi-Fi 不受任何影响；抓包数据实时保存，断电不丢失",
        )
        self.start_script_btn.clicked.connect(self._on_start_or_stop_script_clicked)
        exec_row.addWidget(self.start_script_btn)

        # 兼容性保留隐藏的 stop_capture_btn 对象
        self.stop_capture_btn = QPushButton("⏹ 结束并收尾抓包", self.step2_card)
        self.stop_capture_btn.setVisible(False)
        self.stop_capture_btn.clicked.connect(self._on_stop_and_cleanup_clicked)

        # 中间伸缩，将监看终端推向最右侧
        exec_row.addStretch(1)

        # 中间垂直分割线
        self.exec_divider = QFrame(self.step2_card)
        self.exec_divider.setFixedWidth(1)
        self.exec_divider.setFixedHeight(22)
        exec_row.addWidget(self.exec_divider)

        self.bg_toggle_console_btn = QPushButton("🖥 监看终端", self.step2_card)
        self.bg_toggle_console_btn.setObjectName("captureSecondaryButton")
        self.bg_toggle_console_btn.setToolTip("展开或收起后台抓包控制台与实时监看窗口")
        self.bg_toggle_console_btn.clicked.connect(self._on_toggle_console_clicked)
        exec_row.addWidget(self.bg_toggle_console_btn)

        step2_layout.addLayout(exec_row)
        self.bg_layout.addWidget(self.step2_card)
        # 后台实时终端监看卡片 (高度可调)
        self.bg_console_card = QFrame(self.bg_content)
        self.bg_console_card.setObjectName("captureCard")
        self.bg_console_card.setAttribute(Qt.WA_StyledBackground, True)
        bg_console_layout = QVBoxLayout(self.bg_console_card)
        bg_console_layout.setContentsMargins(12, 10, 12, 10)
        bg_console_layout.setSpacing(6)

        console_bar = QHBoxLayout()
        console_bar.setContentsMargins(0, 0, 0, 0)
        console_bar.setSpacing(8)

        console_title = QLabel("🖥 后台抓包实时监看终端 (会话日志)", self.bg_console_card)
        console_title.setStyleSheet("font-weight: bold; font-size: 10pt; color: #38bdf8;")
        console_bar.addWidget(console_title)

        console_bar.addStretch(1)

        self.bg_autoscroll_check = QCheckBox("自动滚动", self.bg_console_card)
        self.bg_autoscroll_check.setObjectName("captureCheckBox")
        self.bg_autoscroll_check.setChecked(True)
        console_bar.addWidget(self.bg_autoscroll_check)

        # 终端右上角小巧工具栏风格
        tool_btn_style = (
            "QPushButton { min-width: 26px; min-height: 24px; padding: 2px 10px; "
            "border-radius: 4px; border: 1px solid #475569; color: #cbd5e1; background: transparent; } "
            "QPushButton:hover { border-color: #38bdf8; color: #38bdf8; background: rgba(56, 189, 248, 0.12); }"
        )

        self.bg_clear_btn = QPushButton("清空", self.bg_console_card)
        self.bg_clear_btn.setStyleSheet(tool_btn_style)
        self.bg_clear_btn.clicked.connect(self._clear_bg_console)
        console_bar.addWidget(self.bg_clear_btn)

        # 终端高度调节按钮
        self.bg_zoom_in_btn = QPushButton("+ 放大", self.bg_console_card)
        self.bg_zoom_in_btn.setStyleSheet(tool_btn_style)
        self.bg_zoom_in_btn.setToolTip("增加终端高度")
        self.bg_zoom_in_btn.clicked.connect(lambda: self._adjust_bg_console_height(40))
        console_bar.addWidget(self.bg_zoom_in_btn)

        self.bg_zoom_out_btn = QPushButton("- 缩小", self.bg_console_card)
        self.bg_zoom_out_btn.setStyleSheet(tool_btn_style)
        self.bg_zoom_out_btn.setToolTip("减少终端高度")
        self.bg_zoom_out_btn.clicked.connect(lambda: self._adjust_bg_console_height(-40))
        console_bar.addWidget(self.bg_zoom_out_btn)

        self.bg_collapse_btn = QPushButton("收起", self.bg_console_card)
        self.bg_collapse_btn.setStyleSheet(tool_btn_style)
        self.bg_collapse_btn.setToolTip("收起实时监看终端（不影响抓包脚本在路由器后台的持续运行）")
        self.bg_collapse_btn.clicked.connect(self._collapse_bg_console)
        console_bar.addWidget(self.bg_collapse_btn)

        bg_console_layout.addLayout(console_bar)

        self.bg_console = QPlainTextEdit(self.bg_console_card)
        self.bg_console.setReadOnly(True)
        self.bg_console.setMaximumBlockCount(1500)
        font = QFont("Consolas", 9)
        font.setStyleHint(QFont.Monospace)
        self.bg_console.setFont(font)
        self.bg_console.setFixedHeight(240)
        bg_console_layout.addWidget(self.bg_console)

        # 可垂直拖拽调整高度手柄
        self.bg_resize_handle = VerticalResizeHandle(self.bg_console, min_height=100, max_height=700, parent=self.bg_console_card)
        bg_console_layout.addWidget(self.bg_resize_handle)

        self.bg_console_card.setVisible(False)
        self.bg_layout.addWidget(self.bg_console_card)

        # ================= 步骤 3：离线成果回收与智能诊断 =================
        self.step3_card, step3_layout = create_step_card(
            self.bg_content,
            "步骤 3 · 离线成果回收与智能诊断",
            "路由器在现场断网插电自主抓包完成后，连回电脑一键取回握手成果与日志并进行健康度分析。",
        )

        out_dir = self.auto_capture_service.get_output_dir()
        result_row = QHBoxLayout()
        result_row.setContentsMargins(2, 4, 0, 0)
        result_row.setSpacing(12)
        result_row.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self.pull_offline_btn = create_page_text_button(
            self.step3_card,
            "📥 一键拉取握手包",
            f"从路由器 /root/cap/ 下载全部抓包文件与日志到本地电脑：\n【本地保存位置】：{out_dir}\n（仅从路由器下载文件，如需查看健康度请点击旁边的“智能分析日志”）",
        )
        self.pull_offline_btn.clicked.connect(self._on_pull_offline_clicked)
        result_row.addWidget(self.pull_offline_btn)

        self.analyze_offline_btn = create_page_text_button(
            self.step3_card,
            "📊 智能分析日志",
            f"对本地已拉取的抓包文件与会话日志进行健康度诊断分析：\n【分析目录】：{out_dir}\n识别捕获握手包与PMKID数量、周边热点、运行退出状态与排错建议",
        )
        self.analyze_offline_btn.clicked.connect(self._on_analyze_offline_clicked)
        result_row.addWidget(self.analyze_offline_btn)

        self.bg_open_output_dir_btn = QPushButton("📂 打开输出目录", self.step3_card)
        self.bg_open_output_dir_btn.setObjectName("captureSecondaryButton")
        self.bg_open_output_dir_btn.setToolTip(f"在文件资源管理器中打开电脑上的抓包输出目录：\n{out_dir}")
        self.bg_open_output_dir_btn.clicked.connect(self._on_open_output_dir_clicked)
        result_row.addWidget(self.bg_open_output_dir_btn)

        self.bg_output_cap_check = QCheckBox("同时输出cap格式", self.step3_card)
        self.bg_output_cap_check.setObjectName("captureCheckBox")
        self.bg_output_cap_check.setToolTip("抓包成功后除原始包外，额外生成经典标准 .cap 格式文件。\n默认关闭，仅保留原始抓包；开启后将同时输出转换后的 CAP 包。")
        self.bg_output_cap_check.setChecked(bool(self.settings.get("auto_capture_generate_cap", False)))
        self.bg_output_cap_check.toggled.connect(self._on_output_cap_toggled)
        result_row.addWidget(self.bg_output_cap_check)

        result_row.addStretch(1)
        step3_layout.addLayout(result_row)
        self.bg_layout.addWidget(self.step3_card)

        self._update_boot_metrics_style()
        self._update_status_banner_style()

    def _adjust_bg_console_height(self, delta: int):
        """调整后台监看终端高度。"""
        new_h = max(100, min(700, self.bg_console.height() + delta))
        self.bg_console.setFixedHeight(new_h)

    def _collapse_bg_console(self):
        """响应收起后台终端按钮：记录手动隐藏状态，停止流式输出。"""
        self._bg_console_manually_hidden = True
        if hasattr(self, "bg_console_card"):
            self.bg_console_card.setVisible(False)
        self.auto_capture_service.stop_boot_log_stream()

    def _on_toggle_console_clicked(self):
        """响应监看终端切换按钮：开启或停止后台日志流式推送。手动隐藏时保持隐藏，手动展开时恢复。"""
        if not hasattr(self, "bg_console_card"):
            return
        is_visible = not self.bg_console_card.isVisible()
        self.bg_console_card.setVisible(is_visible)
        self._bg_console_manually_hidden = not is_visible
        if is_visible:
            self.auto_capture_service.start_boot_log_stream()
        else:
            self.auto_capture_service.stop_boot_log_stream()

    def _update_boot_metrics_style(self):
        """同步后台指标看板的明暗主题样式。"""
        if hasattr(self, "boot_metrics_frame") and self.boot_metrics_frame:
            if self._light_theme:
                self.boot_metrics_frame.setStyleSheet(
                    "background: #f1f5f9; border: 1px solid #cbd5e1; border-radius: 6px;"
                )
            else:
                self.boot_metrics_frame.setStyleSheet(
                    "background: rgba(0, 0, 0, 0.25); border: 1px solid rgba(255, 255, 255, 0.10); border-radius: 6px;"
                )
        if hasattr(self, "exec_divider") and self.exec_divider:
            if self._light_theme:
                self.exec_divider.setStyleSheet("background: #cbd5e1; border: none;")
            else:
                self.exec_divider.setStyleSheet("background: rgba(255, 255, 255, 0.18); border: none;")

    def _build_offline_workflow_section(self):
        """兼容保留。完整工作流指南已整合进顶部【❓ 使用指南】弹窗。"""
        self.bg_layout.addStretch(1)

    def _build_guard_section(self):
        """构建紧凑型【全局总控与系统资源守卫】控制栏。"""
        self.guard_card = QFrame(self.fg_content)
        self.guard_card.setObjectName("captureCard")
        self.guard_card.setAttribute(Qt.WA_StyledBackground, True)
        self.guard_card.setToolTip("实时监控远端内存与 Flash 剩余容量，支持一键操控全部可用射频并发抓包与安全停止。")

        guard_layout = QHBoxLayout(self.guard_card)
        guard_layout.setContentsMargins(12, 6, 12, 6)
        guard_layout.setSpacing(10)

        # 1. 全局启停单按钮（全部开始与全部停止合并为一个按钮）
        self.toggle_all_btn = create_page_text_button(self.guard_card, "▶ 全部开始抓包", "启动所有可用射频并发抓包")
        self.toggle_all_btn.clicked.connect(self._on_toggle_all_clicked)
        guard_layout.addWidget(self.toggle_all_btn)

        # 兼容性引用别名
        self.start_all_btn = self.toggle_all_btn
        self.stop_all_btn = self.toggle_all_btn
        self.fg_pull_btn = None

        self.open_output_dir_btn = QPushButton("📂 打开输出目录", self.guard_card)
        self.open_output_dir_btn.setObjectName("captureSecondaryButton")
        self.open_output_dir_btn.setToolTip("打开本地抓包输出目录")
        self.open_output_dir_btn.clicked.connect(self._on_open_output_dir_clicked)
        guard_layout.addWidget(self.open_output_dir_btn)

        self.export_logs_btn = QPushButton("导出日志", self.guard_card)
        self.export_logs_btn.setObjectName("captureSecondaryButton")
        guard_layout.addWidget(self.export_logs_btn)
        from .auto_capture_log_export import AutoCaptureLogExport
        self._log_export = AutoCaptureLogExport(self, self.export_logs_btn)

        self.output_cap_check = QCheckBox("同时输出cap格式", self.guard_card)
        self.output_cap_check.setObjectName("captureCheckBox")
        self.output_cap_check.setToolTip("抓包成功后除原始包外，额外生成经典标准 .cap 格式文件。\n默认关闭，仅保留原始抓包；开启后将同时输出转换后的 CAP 包。")
        self.output_cap_check.setChecked(bool(self.settings.get("auto_capture_generate_cap", False)))
        self.output_cap_check.toggled.connect(self._on_output_cap_toggled)
        guard_layout.addWidget(self.output_cap_check)

        guard_layout.addSpacing(12)

        # 2. 内存与 Flash 资源指标 (紧凑单行排布)
        self.mem_health_label = QLabel("可用内存: --", self.guard_card)
        self.mem_health_label.setObjectName("captureFormLabel")
        self.mem_health_label.setToolTip("OpenWrt 路由器实时剩余可用内存")
        guard_layout.addWidget(self.mem_health_label)

        self.disk_health_label = QLabel("Flash剩余: --", self.guard_card)
        self.disk_health_label.setObjectName("captureFormLabel")
        self.disk_health_label.setToolTip("OpenWrt /root 分区实时剩余 Flash 存储空间")
        guard_layout.addWidget(self.disk_health_label)

        guard_layout.addStretch(1)

        # 兼容保留单选框对象（隐藏不显示，默认固定为竖向列表）
        self.radio_split = QRadioButton("◉ 列表", self.guard_card)
        self.radio_split.setChecked(True)
        self.radio_split.setVisible(False)
        self.radio_split.toggled.connect(self._on_view_mode_toggled)

        self.radio_tabbed = QRadioButton("○ 标签", self.guard_card)
        self.radio_tabbed.setVisible(False)
        self.radio_tabbed.toggled.connect(self._on_view_mode_toggled)

        self.fg_layout.addWidget(self.guard_card)

    def _build_cards_container(self):
        """构建用于承载各射频卡片的容器区。"""
        self.cards_frame = QWidget(self.fg_content)
        self.cards_container_layout = QVBoxLayout(self.cards_frame)
        self.cards_container_layout.setContentsMargins(0, 4, 0, 0)
        self.cards_container_layout.setSpacing(8)

        # 占位提示标签（未连接或无射频时显示）
        self.empty_label = QLabel("未连接路由器或正在连接中... 登录成功后将自动探测射频并生成抓包卡片。", self.cards_frame)
        self.empty_label.setObjectName("captureDescription")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setStyleSheet("padding: 30px;")
        self.cards_container_layout.addWidget(self.empty_label)

        # 竖向普通列表布局容器 (原 split_container，现垂直排列卡片)
        self.split_container = QWidget(self.cards_frame)
        self.split_layout = QVBoxLayout(self.split_container)
        self.split_layout.setContentsMargins(0, 0, 0, 0)
        self.split_layout.setSpacing(10)
        self.split_layout.setAlignment(Qt.AlignTop)
        self.cards_container_layout.addWidget(self.split_container)

        # 分屏多标签容器 (标签栏居右显示，文字左对齐)
        self.tabbed_container = QTabWidget(self.cards_frame)
        self.tabbed_container.setObjectName("openwrtCaptureTabs")
        self.tab_bar = RadioCardsSideTabBar(self.tabbed_container)
        self.tabbed_container.setTabBar(self.tab_bar)
        self.tabbed_container.setTabPosition(QTabWidget.East)
        self.cards_container_layout.addWidget(self.tabbed_container)
        self.tabbed_container.setVisible(False)

        self.fg_layout.addWidget(self.cards_frame, 1)

    def set_light_theme(self, enabled: bool):
        """亮暗主题换肤。"""
        self._light_theme = bool(enabled)
        if hasattr(self, "tab_bar"):
            self.tab_bar.set_light_theme(self._light_theme)
        self.install_boot_btn.set_light_theme(self._light_theme)
        self.pull_offline_btn.set_light_theme(self._light_theme)
        if getattr(self, "fg_pull_btn", None) is not None:
            self.fg_pull_btn.set_light_theme(self._light_theme)
        self.analyze_offline_btn.set_light_theme(self._light_theme)
        self.start_script_btn.set_light_theme(self._light_theme)
        if hasattr(self, "toggle_all_btn") and hasattr(self.toggle_all_btn, "set_light_theme"):
            self.toggle_all_btn.set_light_theme(self._light_theme)
        self._update_toggle_all_btn_state()
        self._update_start_script_btn_state()
        for card in self.radio_cards.values():
            card.set_light_theme(self._light_theme)
        self._update_boot_metrics_style()
        self._update_status_banner_style()

    @active_service_slot(object)
    def _on_snapshot_changed(self, snapshot):
        """根据快照同步探测射频与卡片状态。"""
        self._last_snapshot = snapshot
        if snapshot is None or not hasattr(snapshot, "connection_state"):
            self._initial_loaded = False
            self._last_rendered_boot_status = None
            self._last_loading_state_key = None
            self.empty_label.setVisible(True)
            self.split_container.setVisible(False)
            self.tabbed_container.setVisible(False)
            self.running_alert_widget.setVisible(False)
            self._update_toggle_all_btn_state(None)
            self._update_start_script_btn_state(None)
            self._update_mutual_exclusion_state(None)
            self.install_boot_btn.setEnabled(False)
            self._update_pull_buttons_state()
            self.boot_enable_check.setEnabled(False)
            self.boot_enable_check.setChecked(False)
            self.boot_enable_check.setToolTip("请先连接路由器")
            self._update_loading_status("disconnected")
            return

        conn_state_val = getattr(snapshot.connection_state, "value", "")
        connected = conn_state_val == "connected"
        device = getattr(snapshot, "device", None)
        radios = list(device.radios) if (connected and device and hasattr(device, "radios")) else []

        self._update_toggle_all_btn_state(snapshot)
        self._update_start_script_btn_state(snapshot)
        self._update_mutual_exclusion_state(snapshot)
        self.install_boot_btn.setEnabled(connected)
        self._update_pull_buttons_state()
        self.boot_enable_check.setEnabled(connected)

        if not connected:
            self._initial_loaded = False
            self._last_rendered_boot_status = None
            self.running_alert_widget.setVisible(False)
            if hasattr(self, "metric_autostart_widget"):
                self.metric_autostart_widget.setVisible(False)
            self.start_script_btn.setText("▶ 手动启动脚本")
            self.start_script_btn.setEnabled(False)
            self._bg_status_timer.stop()
            self.boot_enable_check.setEnabled(False)
            self.boot_enable_check.setChecked(False)
            self.boot_enable_check.setToolTip("请先连接路由器")

            if conn_state_val == "connecting":
                detail = getattr(snapshot, "connection_detail", "")
                if "读取" in detail:
                    self._update_loading_status("reading_info", detail)
                else:
                    self._update_loading_status("logging_in", detail)
            elif conn_state_val in {"failed", "disconnecting"}:
                err = getattr(snapshot, "last_error", "") or getattr(snapshot, "connection_detail", "")
                self._update_loading_status("error", err)
            else:
                self._update_loading_status("disconnected")
        else:
            if self.sub_tabs.currentIndex() == 1 and not self._bg_status_timer.isActive():
                self._bg_status_timer.start()
            if self.auto_capture_service.boot_status is not None:
                self._on_boot_status_changed(self.auto_capture_service.boot_status)
            else:
                self._update_loading_status("checking_env")
                self.auto_capture_service.refresh_boot_status()

        # 校验自动抓包环境与 hcxdumptool 版本兼容性
        if hasattr(self, "version_warning_card"):
            has_issue, issue_type, issue_detail = check_auto_capture_environment(device, snapshot)
            pmkid_dep = next((d for d in getattr(snapshot, "dependencies", ()) or () if getattr(d, "key", "") == "pmkid"), None)
            detected_hcx_ver = ""
            if pmkid_dep and getattr(pmkid_dep, "detail", ""):
                vm = re.search(r"hcxdumptool\s+v?(\d+\.\d+(?:\.\d+)?)", pmkid_dep.detail) or re.search(r"v?(\d+\.\d+(?:\.\d+)?)", pmkid_dep.detail)
                detected_hcx_ver = vm.group(1) if vm else ""

            if connected and has_issue and issue_type == "hcx_outdated_upgradeable":
                dev_ver = getattr(device, "version", "") or "未知版本"
                dev_kern = getattr(device, "kernel_version", "") or "未知"
                ver_display = f"hcxdumptool {detected_hcx_ver}" if detected_hcx_ver else "旧版 hcxdumptool"

                self.version_warning_title.setText(f"⚠️ 检测到旧版抓包工具 ({ver_display}，需升级)")
                self.version_warning_detail.setText(
                    f"当前路由器系统具备现代 Netlink 运行环境（支持 6.3.4+），但当前使用的抓包工具为 {ver_display}（旧版方言）。\n"
                    f"现代 Linux 内核已废弃传统 Wireless Extensions (WEXT) ioctl，旧版工具易因缺少接口导致秒退报错。\n"
                    f"【解决办法】建议点击右侧【一键升级抓包工具 (6.3.4)】升级至现代版本，以获得基于虚拟监听口的无损并发抓包体验。"
                )
                if hasattr(self, "upgrade_hcx_btn"):
                    self.upgrade_hcx_btn.setVisible(True)
                self.version_warning_card.setVisible(True)
            else:
                self.version_warning_card.setVisible(False)

        if not radios:
            self.empty_label.setVisible(True)
            self.split_container.setVisible(False)
            self.tabbed_container.setVisible(False)
            return

        self.empty_label.setVisible(False)
        self._sync_radio_cards(radios)

    def _sync_radio_cards(self, radios: List[OpenWrtRadioInfo]):
        """根据探测到的 radios 列表更新或创建射频卡片。"""
        current_names = {r.name for r in radios}
        # 移除已失效的
        for name in list(self.radio_cards.keys()):
            if name not in current_names:
                card = self.radio_cards.pop(name)
                card.deleteLater()

        # 创建或更新存在的
        for r_info in radios:
            if r_info.name not in self.radio_cards:
                card = AutoCaptureRadioCard(r_info, self.cards_frame)
                card.set_light_theme(self._light_theme)
                card.startRequested.connect(self._on_card_start_requested)
                card.stopRequested.connect(self._on_card_stop_requested)
                self.radio_cards[r_info.name] = card
            else:
                self.radio_cards[r_info.name].radio_info = r_info

        self._reposition_cards()

    def _reposition_cards(self):
        """根据当前视图模式将卡片摆放进 split_layout 或 tabbed_container，保证切换回显正常。"""
        if self._view_mode == "split_screen":
            # 1. 切换到同屏并排显示模式
            # 先从 tabbed_container 移除 tab，防止 widget 保持隐藏状态
            while self.tabbed_container.count():
                self.tabbed_container.removeTab(0)
            self.tabbed_container.setVisible(False)

            self.split_container.setVisible(True)
            while self.split_layout.count():
                self.split_layout.takeAt(0)

            for card in self.radio_cards.values():
                card.setParent(self.split_container)
                self.split_layout.addWidget(card, 0, Qt.AlignTop)
                card.show()
        else:
            # 2. 切换到分屏多标签页模式
            while self.split_layout.count():
                self.split_layout.takeAt(0)
            self.split_container.setVisible(False)

            self.tabbed_container.setVisible(True)
            while self.tabbed_container.count():
                self.tabbed_container.removeTab(0)

            for name, card in self.radio_cards.items():
                band_str = "/".join(card.radio_info.bands) if card.radio_info.bands else "Wi-Fi"
                title = f"📻 {band_str} ({card.radio_info.name})"
                self.tabbed_container.addTab(card, title)
                card.show()

    def _on_view_mode_toggled(self):
        """切换同屏/分屏显示模式。"""
        if self.radio_split.isChecked():
            self._view_mode = "split_screen"
        else:
            self._view_mode = "tabbed"
        self._reposition_cards()

    @active_service_slot(object)
    def _on_boot_status_changed(self, status: AutoCaptureBootStatus, force: bool = False):
        """自启状态更新，结构化指标看板展示。"""
        self._last_boot_status = status
        # 相同状态避免重复重刷控件树，消除频繁重绘与闪烁；但若当前正在展示中间过渡文案（如【正在...】），必须执行渲染回显
        is_transitioning = hasattr(self, "boot_status_badge") and self.boot_status_badge.text().startswith("【正在")
        if not force and not is_transitioning and getattr(self, "_last_rendered_boot_status", None) == status:
            return
        self._last_rendered_boot_status = status
        self._update_pull_buttons_state()
        # 1. 步骤 1 自启服务状态徽章（独立展示服务就绪与部署状态，不被脚本运行状态覆盖）
        if status.installed:
            self.boot_status_badge.setText("【自启服务已就绪】")
            self.boot_status_badge.setStyleSheet("color: #38bdf8; font-weight: bold; font-size: 13px;")
        elif status.remote_script_exists:
            self.boot_status_badge.setText("【脚本就绪 · 待注册自启】")
            self.boot_status_badge.setStyleSheet("color: #f59e0b; font-weight: bold; font-size: 13px;")
        else:
            self.boot_status_badge.setText("【服务未部署】")
            self.boot_status_badge.setStyleSheet("color: #94a3b8; font-weight: bold; font-size: 13px;")

        # 1.1 步骤 2 标题同行两个状态持续显示（自启配置 + 运行状态，无边框纯文本）
        snapshot = getattr(self, "_last_snapshot", None) or getattr(self.service, "openwrt_snapshot", None)
        conn_state = getattr(snapshot, "connection_state", None)
        connected = bool(snapshot and getattr(conn_state, "value", conn_state) == "connected")
        if connected:
            self.running_alert_widget.setVisible(True)
            if hasattr(self, "metric_autostart_widget"):
                self.metric_autostart_widget.setVisible(True)
            if status.is_running:
                self.running_alert_label.setText("⚡ 脚本运行中 (后台抓包中)")
                self.running_alert_label.setStyleSheet("color: #10b981; font-weight: bold; font-size: 13px; border: none; background: transparent;")
                self.stop_capture_btn.setEnabled(True)
                if getattr(self, "_manual_boot_started", False) and hasattr(self, "bg_console_card"):
                    if not getattr(self, "_bg_console_manually_hidden", False):
                        self.bg_console_card.setVisible(True)
            else:
                self.running_alert_label.setText("⚡ 运行状态: 空闲待机")
                self.running_alert_label.setStyleSheet("color: #94a3b8; font-weight: bold; font-size: 13px; border: none; background: transparent;")
                self._manual_boot_started = False
        else:
            self.running_alert_widget.setVisible(False)
            if hasattr(self, "metric_autostart_widget"):
                self.metric_autostart_widget.setVisible(False)
            self.running_alert_label.setText("⚡ 运行状态: 空闲待机")
            self._manual_boot_started = False

        # 2. 指标看板各指标清晰排布
        # 抓包脚本
        script_path = getattr(status, "remote_script_path", "/root/hcx_autocap.sh")
        if status.remote_script_exists:
            self.metric_script_label.setText(f"📄 抓包脚本: 已就绪 ({script_path})")
            self.metric_script_label.setStyleSheet("color: #10b981; font-weight: bold;")
        else:
            self.metric_script_label.setText(f"📄 抓包脚本: 未检测到 ({script_path})")
            self.metric_script_label.setStyleSheet("color: #94a3b8;")

        # 脚本版本
        ver_str = status.remote_version or "未知"
        if status.has_update:
            self.metric_version_label.setText(f"🏷 版本: {ver_str} (有新版)")
            self.metric_version_label.setStyleSheet("color: #f59e0b; font-weight: bold;")
        elif status.remote_script_exists:
            self.metric_version_label.setText(f"🏷 版本: {ver_str} (最新)")
            self.metric_version_label.setStyleSheet("color: #38bdf8; font-weight: bold;")
        else:
            self.metric_version_label.setText("🏷 版本: --")
            self.metric_version_label.setStyleSheet("color: #94a3b8;")

        # 系统守护服务 (/etc/init.d/luxiaoguan-autocap)
        if getattr(status, "service_installed", status.installed):
            self.metric_service_label.setText("🛡 系统服务: 已注册 (/etc/init.d)")
            self.metric_service_label.setStyleSheet("color: #10b981; font-weight: bold;")
        else:
            self.metric_service_label.setText("🛡 系统服务: 未注册")
            color = "#f59e0b" if status.remote_script_exists else "#94a3b8"
            self.metric_service_label.setStyleSheet(f"color: {color};")

        # 自启配置（步骤 2 标题同行状态，无边框纯文本）
        if hasattr(self, "metric_autostart_widget"):
            if status.autostart_enabled:
                self.metric_autostart_label.setText("🚀 自启配置: 已启用")
                self.metric_autostart_label.setStyleSheet("color: #10b981; font-weight: bold; font-size: 13px; border: none; background: transparent;")
            else:
                self.metric_autostart_label.setText("🚀 自启配置: 已关闭")
                self.metric_autostart_label.setStyleSheet("color: #94a3b8; font-weight: bold; font-size: 13px; border: none; background: transparent;")

        # 3. 同步开关
        self.boot_enable_check.blockSignals(True)
        self.boot_enable_check.setChecked(status.autostart_enabled)
        self.boot_enable_check.blockSignals(False)

        snapshot = getattr(self, "_last_snapshot", None) or getattr(self.service, "openwrt_snapshot", None)
        conn_state = getattr(snapshot, "connection_state", None)
        connected = bool(snapshot and getattr(conn_state, "value", conn_state) == "connected")
        self.boot_enable_check.setEnabled(connected)

        if not connected:
            self.boot_enable_check.setToolTip("请先连接路由器")
        elif not status.installed:
            if status.remote_script_exists:
                self.boot_enable_check.setToolTip("开机自启抓包\n【状态】：抓包脚本已就绪但自启服务尚未注册。\n【说明】：勾选将自动注册服务并开启自启（插电即抓，断电保护）")
            else:
                self.boot_enable_check.setToolTip("开机自启抓包\n【状态】：当前未部署服务。\n【说明】：勾选将引导部署并开启开机自启（插电即可全自动后台自主抓包，断电不丢数据）")
        elif status.autostart_enabled:
            self.boot_enable_check.setToolTip("开机自启抓包\n【状态】：已开启。\n【说明】：路由器现场插电即可自主后台抓包，断电不丢失数据；点击可关闭")
        else:
            self.boot_enable_check.setToolTip("开机自启抓包\n【状态】：已关闭。\n【说明】：点击开启后，路由器通电即可全自动后台自主抓包，断电不丢数据")

        # 4. 版本比对告警
        if status.has_update:
            self.version_diff_label.setText(
                f"发现脚本新版本 (本地: {status.local_version} > 路由器: {status.remote_version})"
            )
            self.version_diff_widget.setVisible(True)
        else:
            self.version_diff_widget.setVisible(False)

        # 5. 手动启动/收尾脚本切换按钮状态与互斥同步
        self._update_start_script_btn_state(snapshot, status)
        self._update_mutual_exclusion_state(snapshot)
        self._update_toggle_all_btn_state(snapshot)

        if connected:
            self._update_loading_status("ready")

    @active_service_slot(int, int, int, int, str)
    def _on_resource_stats_updated(self, mem_avail: int, mem_total: int, disk_avail: int, disk_total: int, level: str):
        """更新全局资源守卫看板。"""
        if level == "danger":
            status_text = "● 危险"
            color = "#ef4444"
        elif level == "warning":
            status_text = "● 预警"
            color = "#f59e0b"
        else:
            status_text = "● 正常"
            color = "#10b981"

        self.mem_health_label.setText(f"可用内存: {mem_avail}MB / {mem_total}MB  {status_text}")
        self.mem_health_label.setStyleSheet(f"color: {color}; font-weight: bold;")

        self.disk_health_label.setText(f"Flash剩余: {disk_avail}MB / {disk_total}MB  {status_text}")
        self.disk_health_label.setStyleSheet(f"color: {color}; font-weight: bold;")

    @active_service_slot(str, dict)
    def _on_radio_stats_updated(self, radio_name: str, stats: dict):
        if radio_name in self.radio_cards:
            self.radio_cards[radio_name].apply_stats(stats)

    @active_service_slot(str, str)
    def _on_radio_stream_output(self, radio_name: str, text: str):
        if radio_name in self.radio_cards:
            self.radio_cards[radio_name].append_stream_text(text)

    @active_service_slot(str, str)
    def _on_radio_state_changed(self, radio_name: str, state: str):
        if radio_name in self.radio_cards:
            session = self.auto_capture_service.sessions.get(radio_name)
            err_msg = session.stats.error_message if session else ""
            self.radio_cards[radio_name].apply_state(state, err_msg)
        self._update_pull_buttons_state()
        self._update_toggle_all_btn_state()
        self._update_start_script_btn_state()
        self._update_mutual_exclusion_state()
        if not getattr(self.auto_capture_service, "is_any_capture_running", lambda: False)():
            if getattr(self, "_pending_foreground_cleanup", False):
                self._prompt_cleanup_if_safe()

    def _on_card_start_requested(self, radio_info: OpenWrtRadioInfo, channels: str, allow_takeover: bool):
        bg_status = getattr(self.auto_capture_service, "boot_status", None)
        if bool(bg_status and bg_status.is_running) or getattr(self, "_manual_boot_started", False):
            MessageDialog("操作受限", "当前后台抓包正在运行中。\n前后台抓包为互斥功能，无法同时启动；请先停止后台抓包后再启动前台抓包。", self).exec()
            return
        self._is_starting_foreground = True
        self._update_mutual_exclusion_state()
        self._start_preflight.request([dict(radio_info=radio_info, channels=channels,
                                           allow_takeover=allow_takeover)])

    def _on_card_stop_requested(self, radio_name: str):
        self._start_preflight.cancel()
        self.auto_capture_service.stop_radio_capture(radio_name)

    def _on_stop_all_clicked(self):
        self._start_preflight.cancel()
        self.auto_capture_service.stop_all()

    def _on_start_all_clicked(self):
        """全部开始抓包，带前置非空闲射频接管引导与前后台互斥校验。"""
        bg_status = getattr(self.auto_capture_service, "boot_status", None)
        if bool(bg_status and bg_status.is_running) or getattr(self, "_manual_boot_started", False):
            self._is_starting_foreground = False
            self._update_toggle_all_btn_state()
            self._update_mutual_exclusion_state()
            MessageDialog("操作受限", "当前后台抓包正在运行中。\n前后台抓包为互斥功能，无法同时启动；请先停止后台抓包后再启动前台抓包。", self).exec()
            return

        if not self.radio_cards:
            MessageDialog("无法开始抓包", "当前未检测到任何可用的无线射频，请确认路由器已连接成功。", self).exec()
            return

        configs = []
        for card in self.radio_cards.values():
            configs.append({
                "radio_info": card.radio_info,
                "channels": card.channel_edit.text().strip(),
                "allow_takeover": card.takeover_check.isChecked(),
            })
        self._start_preflight.request(configs)

    @active_service_slot(object)
    def _on_start_inspected(self, result):
        self._is_starting_foreground = False
        if not self._start_preflight.is_current(result):
            self._update_toggle_all_btn_state()
            self._update_mutual_exclusion_state()
            return
        if result.error:
            self._update_toggle_all_btn_state()
            self._update_mutual_exclusion_state()
            MessageDialog("无法开始抓包", "无法核实路由器当前无线状态：" + result.error, self).exec()
            return
        self.service.update_openwrt_radio_idle_status(result.usages)
        self._on_radio_idle_status_updated(result.usages)
        configs = [dict(item) for item in result.configs]
        busy_unauthorized = [item for item in configs
                             if not item["radio_info"].free and not item["allow_takeover"]]
        if busy_unauthorized:
            names = ", ".join(item["radio_info"].name for item in busy_unauthorized)
            dialog = ConfirmDialog("无线射频接管确认",
                                   "当前仍有启用的无线业务：" + names +
                                   "\n\n是否允许临时接管并开始抓包？停止后将恢复原有无线配置。", self)
            if dialog.exec() != ConfirmDialog.Accepted:
                self._update_toggle_all_btn_state()
                self._update_mutual_exclusion_state()
                return
            for item in busy_unauthorized:
                item["allow_takeover"] = True
                card = self.radio_cards.get(item["radio_info"].name)
                if card is not None:
                    card.takeover_check.setChecked(True)
        # A modal prompt can process connection-change events while it is open.
        if not self._start_preflight.is_current(result):
            return
        if len(configs) == 1:
            item = configs[0]
            self.auto_capture_service.start_radio_capture(item["radio_info"], item["channels"], item["allow_takeover"])
        else:
            self.auto_capture_service.start_all(configs)
        self._update_toggle_all_btn_state()
        self._update_mutual_exclusion_state()

    def _on_boot_check_clicked(self, checked: bool):
        """响应用户点击开机自启开关。支持未部署时引导一键部署并开启。"""
        if getattr(self.auto_capture_service, "is_busy", False) or getattr(self, "_is_starting_foreground", False):
            self.boot_enable_check.blockSignals(True)
            self.boot_enable_check.setChecked(not checked)
            self.boot_enable_check.blockSignals(False)
            MessageDialog("操作受限", "当前前台抓包正在运行中，无法修改开机自启或启动后台抓包。\n请先停止前台抓包。", self).exec()
            return
        snapshot = getattr(self, "_last_snapshot", None) or getattr(self.service, "openwrt_snapshot", None)
        conn_state = getattr(snapshot, "connection_state", None)
        connected = bool(snapshot and getattr(conn_state, "value", conn_state) == "connected")
        if not connected:
            self.boot_enable_check.setChecked(False)
            MessageDialog("提示", "路由器尚未连接，请先连接路由器后再配置开机自启。", self).exec()
            return

        status = self.auto_capture_service.boot_status
        if status is None:
            self.boot_enable_check.setChecked(not checked)
            MessageDialog("提示", "正在检测路由器开机自启服务状态，请稍后再试。", self).exec()
            return

        if not status.installed:
            if checked:
                # 弹窗前先将开关恢复为未勾选，待部署成功后再打勾
                self.boot_enable_check.setChecked(False)
                if status.remote_script_exists:
                    script_path = getattr(status, "remote_script_path", "/root/hcx_autocap.sh")
                    msg = (
                        f"检测到抓包脚本已在远端就绪 ({script_path})，\n"
                        "但系统级守护服务 (/etc/init.d/luxiaoguan-autocap) 尚未注册。\n\n"
                        "是否立即注册自启守护服务，并开启开机自启？"
                    )
                else:
                    msg = (
                        "检测到路由器尚未部署后台抓包脚本与系统自启服务。\n\n"
                        "是否立即上传并部署自启脚本与服务，并开启开机自启？"
                    )
                dialog = ConfirmDialog("部署自启服务", msg, self)
                if dialog.exec() == ConfirmDialog.Accepted:
                    self._last_rendered_boot_status = None
                    self.boot_enable_check.setEnabled(False)
                    self.boot_status_badge.setText("【正在部署服务...】")
                    self.boot_status_badge.setStyleSheet("color: #38bdf8; font-weight: bold;")
                    self.auto_capture_service.install_or_update_boot_service(enable_after=True)
            else:
                self.boot_enable_check.setChecked(False)
            return

        self._last_rendered_boot_status = None
        self.boot_enable_check.setEnabled(False)
        action_name = "开启" if checked else "关闭"
        self.boot_status_badge.setText(f"【正在{action_name}自启...】")
        self.boot_status_badge.setStyleSheet("color: #38bdf8; font-weight: bold;")
        self.auto_capture_service.toggle_autostart(checked)

    def _on_start_or_stop_script_clicked(self):
        """点击手动启动/收尾切换按钮：运行中则执行收尾，未运行则启动。"""
        b_status = getattr(self.auto_capture_service, "boot_status", None) or getattr(self, "_last_boot_status", None)
        if b_status and b_status.is_running:
            self._on_stop_and_cleanup_clicked()
        else:
            self._on_start_script_clicked()

    def _on_start_script_clicked(self):
        """响应手动启动抓包脚本操作。"""
        snapshot = getattr(self, "_last_snapshot", None) or getattr(self.service, "openwrt_snapshot", None)
        conn_state = getattr(snapshot, "connection_state", None)
        connected = bool(snapshot and getattr(conn_state, "value", conn_state) == "connected")
        if not connected:
            MessageDialog("提示", "路由器尚未连接，请先连接路由器后再启动抓包脚本。", self).exec()
            return

        if getattr(self.auto_capture_service, "is_busy", False) or getattr(self, "_is_starting_foreground", False):
            MessageDialog("操作受限", "当前前台抓包正在运行中。\n前后台抓包为互斥功能，无法同时启动；请先停止前台抓包后再启动后台抓包。", self).exec()
            return

        status = self.auto_capture_service.boot_status
        if status is None:
            MessageDialog("提示", "正在检测路由器状态，请稍后重试。", self).exec()
            return

        if status.is_running:
            MessageDialog("提示", "后台抓包脚本已在运行中，无需重复启动。\n如需重新开始，可先点击“结束并收尾抓包”。", self).exec()
            return

        # 检查路由器是否有空闲频段供后台抓包使用
        device = getattr(snapshot, "device", None) if snapshot else None
        radios = getattr(device, "radios", ()) if device else ()
        free_radios = [r for r in radios if getattr(r, "free", False)]
        if radios and not free_radios:
            msg = (
                "检测到当前路由器所有无线频段均正在发射 Wi-Fi 提供正常上网服务，未发现空闲频段。\n\n"
                "【后台抓包说明】：\n"
                "后台抓包默认遵循【不干扰现有正常业务 Wi-Fi】安全原则，只会使用空闲射频；\n"
                "若当前所有射频均在发射 Wi-Fi，后台抓包脚本将无法接管任何射频。\n\n"
                "【建议方案】：\n"
                "1. 在路由器管理后台（或无线设置页面）暂时停用一个未使用的无线频段（例如 5G 或访客 Wi-Fi）；\n"
                "2. 或切换到上方【前台抓包】标签页，勾选“临时接管”进行抓包（停止后自动恢复原 Wi-Fi）。\n\n"
                "是否仍要尝试启动后台抓包？"
            )
            dialog = ConfirmDialog("无空闲射频提醒", msg, self)
            if dialog.exec() != ConfirmDialog.Accepted:
                return

        if not status.remote_script_exists:
            msg = (
                "检测到路由器尚未准备好抓包环境。\n\n"
                "是否立即自动安装最新抓包支持，并直接启动后台抓包？\n"
                "（说明：后台抓包只会使用空闲无线频段，不影响当前正常上网的 Wi-Fi，数据实时保存）"
            )
            dialog = ConfirmDialog("安装并启动后台抓包", msg, self)
            if dialog.exec() == ConfirmDialog.Accepted:
                self._last_rendered_boot_status = None
                self._manual_boot_started = True
                if hasattr(self, "bg_console_card"):
                    self.bg_console_card.setVisible(True)
                    self.auto_capture_service.stop_boot_log_stream()
                    from datetime import datetime
                    self.append_bg_console_text(
                        f"\n[{datetime.now().strftime('%H:%M:%S')}] ▶ 正在准备并启动后台抓包脚本...\n"
                        "⏳ 正在准备抓包环境与探测空闲射频，请稍候...\n"
                    )
                self.start_script_btn.setEnabled(False)
                apply_stop_button_style(self.start_script_btn, True, self._light_theme)
                self._update_mutual_exclusion_state()
                self.running_alert_label.setText("⏳ 正在准备抓包环境并启动...")
                self.running_alert_label.setStyleSheet("color: #f59e0b; font-weight: bold; font-size: 13px;")
                self.auto_capture_service.install_and_start_boot_script()
            return

        msg = (
            "即将手动启动后台自主抓包。\n\n"
            "【重要说明】：\n"
            "1. 不影响正常上网：只会启用当前没有发射 Wi-Fi 的空闲频段，正在正常使用的 Wi-Fi 完全不受影响；\n"
            "2. 成果实时保存：抓取到的握手包文件会自动实时保存在路由器 (/root/cap/) 中，即便直接断电也不会丢失数据。\n\n"
            "确认立即启动后台抓包吗？"
        )
        dialog = ConfirmDialog("启动后台抓包", msg, self)
        if dialog.exec() == ConfirmDialog.Accepted:
            self._last_rendered_boot_status = None
            self._manual_boot_started = True
            if hasattr(self, "bg_console_card"):
                self.bg_console_card.setVisible(True)
                self.auto_capture_service.stop_boot_log_stream()
                from datetime import datetime
                self.append_bg_console_text(
                    f"\n[{datetime.now().strftime('%H:%M:%S')}] ▶ 正在手动启动后台抓包脚本...\n"
                    "⏳ 正在探测无线环境与空闲射频，请稍候...\n"
                )
            self.start_script_btn.setEnabled(False)
            apply_stop_button_style(self.start_script_btn, True, self._light_theme)
            self._update_mutual_exclusion_state()
            self.running_alert_label.setText("⏳ 正在启动抓包脚本...")
            self.running_alert_label.setStyleSheet("color: #f59e0b; font-weight: bold; font-size: 13px;")
            self.auto_capture_service.start_boot_script()

    def _on_stop_and_cleanup_clicked(self):
        """响应结束并收尾抓包操作（安全封包、清理网卡与临时文件）。"""
        self._manual_boot_started = False
        self._bg_console_manually_hidden = False
        if hasattr(self, "bg_console_card"):
            self.bg_console_card.setVisible(False)
        self.auto_capture_service.stop_boot_log_stream()
        self._last_rendered_boot_status = None
        self.stop_capture_btn.setEnabled(False)
        self.start_script_btn.setEnabled(False)
        self.start_script_btn.setText("⏳ 正在收尾抓包...")
        apply_stop_button_style(self.start_script_btn, True, self._light_theme)
        self.running_alert_label.setText("⏳ 正在收尾抓包...")
        self.running_alert_label.setStyleSheet("color: #f97316; font-weight: bold; font-size: 13px;")
        self.auto_capture_service.stop_and_cleanup_capture()

    @active_service_slot(str)
    def _on_foreground_capture_pulled(self, file_path: str):
        """前台单路抓包停止并拉取到本地后，触发统一清理询问。"""
        self._pending_foreground_cleanup = True
        self._update_toggle_all_btn_state()
        self._prompt_cleanup_if_safe()

    def _prompt_cleanup_if_safe(self):
        """前后台统一清理询问：确认所有抓包已停止后，询问用户是否清空路由器上的文件。"""
        # 第二道防线：当前仍有任何抓包正在运行，绝不执行清理，跳过询问
        if getattr(self.auto_capture_service, "is_any_capture_running", lambda: False)():
            LOGGER.info("Capture task still active, skipping remote cleanup prompt.")
            return

        if getattr(self, "_cleanup_prompt_showing", False):
            return
        self._cleanup_prompt_showing = True
        self._pending_foreground_cleanup = False
        try:
            confirm = ConfirmDialog(
                "清理路由器抓包",
                "握手包已经拉取到本地，是否清空路由器上的文件？",
                self,
            )
            if confirm.exec() == ConfirmDialog.Accepted:
                self.auto_capture_service.clear_remote_cap_artifacts_async()
            else:
                self.auto_capture_service.messageGenerated.emit("已保留路由器上的抓包文件。", "info")
        finally:
            self._cleanup_prompt_showing = False

    def _on_uninstall_clicked(self):
        if ConfirmDialog("卸载自启服务", "确定完全卸载 /etc/init.d/luxiaoguan-autocap 与 /root/hcx_autocap.sh 吗？\n(/root/cap/ 产物将被保留)", self).exec() == ConfirmDialog.Accepted:
            self._last_rendered_boot_status = None
            self.auto_capture_service.uninstall_boot_service()

    def _on_pull_offline_clicked(self):
        """异步拉取远端 /root/cap/ 的抓包成果与日志到本地电脑。"""
        snapshot = getattr(self, "_last_snapshot", None) or getattr(self.service, "openwrt_snapshot", None)
        conn_state = getattr(snapshot, "connection_state", None)
        connected = bool(snapshot and getattr(conn_state, "value", conn_state) == "connected")
        if not connected:
            MessageDialog("提示", "路由器尚未连接，请先连接路由器后再拉取握手包。", self).exec()
            return

        # 第一道防线：检查是否运行中
        if getattr(self.auto_capture_service, "is_any_capture_running", lambda: False)():
            MessageDialog("提示", "当前有抓包任务正在运行中，已禁用拉取，避免影响当前抓包或误清空数据。\n请先停止抓包后再执行拉取。", self).exec()
            return

        self._is_pulling_offline = True
        self._update_pull_buttons_state()
        out_dir = self.auto_capture_service.get_output_dir()
        self.auto_capture_service.pull_offline_artifacts_async(str(out_dir))

    @active_service_slot(list, str)
    def _on_pull_offline_success(self, pulled_files: list, local_dir: str):
        self._is_pulling_offline = False
        self._update_pull_buttons_state()
        if not pulled_files:
            MessageDialog("提示", "路由器 /root/cap/ 目录下暂无抓包成果文件 (.pcapng / .log)。", self).exec()
            return

        # 确认拉取的文件没问题（本地存在且大小正常）
        valid_files = [f for f in pulled_files if Path(f).is_file() and Path(f).stat().st_size > 0]
        if len(valid_files) != len(pulled_files):
            MessageDialog("提示", "已成功拉取文件，但部分文件校验异常，已保留路由器原文件以防数据丢失。", self).exec()
            return

        # 第二道防线：检查当前是否有抓包任务正在运行
        if getattr(self.auto_capture_service, "is_any_capture_running", lambda: False)():
            msg = (
                f"已成功拉取 {len(valid_files)} 个抓包成果与日志文件！\n\n"
                f"【保存在本地目录】：\n{local_dir}\n\n"
                "⚠️ 【注意】：检测到当前有抓包任务正在运行，已自动跳过清理路由器抓包目录，避免影响正在抓取的包。"
            )
            MessageDialog("拉取成功", msg, self).exec()
            return

        # 统一询问用户是否清空路由器上的文件
        self._prompt_cleanup_if_safe()

    @active_service_slot(str)
    def _on_pull_offline_error(self, err_msg: str):
        self._is_pulling_offline = False
        self._update_pull_buttons_state()
        MessageDialog("拉取成果失败", f"从路由器拉取离线成果时出现异常:\n{err_msg}", self).exec()

    @active_service_slot(bool, str)
    def _on_remote_cap_cleared(self, success: bool, message: str):
        if success:
            MessageDialog("清空成功", "路由器抓包目录 (/root/cap/) 已成功清空，Flash 存储空间已释放。", self).exec()
        else:
            MessageDialog("清空失败", f"清空路由器抓包目录失败:\n{message}", self).exec()

    def _on_analyze_offline_clicked(self):
        """对本地输出目录中的离线抓包与日志进行智能健康诊断并弹出报告。"""
        out_dir = self.auto_capture_service.get_output_dir()
        try:
            report = self.auto_capture_service.analyze_offline_artifacts(str(out_dir))
            dialog = AutoCaptureReportDialog(report, str(out_dir), self)
            dialog.exec()
        except Exception as exc:
            MessageDialog("分析日志失败", f"分析本地抓包成果日志时出现异常:\n{exc}", self).exec()

    def _on_open_output_dir_clicked(self):
        out_dir = self.auto_capture_service.get_output_dir()
        try:
            if not out_dir.exists():
                out_dir.mkdir(parents=True, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(out_dir.resolve())))
        except Exception:
            pass

    @active_service_slot()
    def _on_boot_script_started(self):
        """Show the console from verified state, independent of translated messages."""
        self._manual_boot_started = True
        if hasattr(self, "bg_console_card") and not getattr(self, "_bg_console_manually_hidden", False):
            self.bg_console_card.setVisible(True)
            self.auto_capture_service.start_boot_log_stream(restart=True)
        self.append_bg_console_text("\u540e\u53f0\u811a\u672c\u5df2\u8fd0\u884c\uff1b\u662f\u5426\u5df2\u5f00\u59cb\u6293\u5305\u5c1a\u672a\u786e\u8ba4\uff0c\u8bf7\u4ee5\u540e\u53f0\u4f1a\u8bdd\u65e5\u5fd7\u4e3a\u51c6\u3002\n")

    @active_service_slot(str, str)
    def _on_message_generated(self, message: str, level: str):
        """统一捕获后台服务生成的消息，出现错误时在主线程弹出醒目提示。"""
        if level == "error":
            if hasattr(self, "bg_console_card") and self.bg_console_card.isVisible():
                from datetime import datetime
                self.append_bg_console_text(
                    f"[{datetime.now().strftime('%H:%M:%S')}] ❌ {message}\n"
                )
            MessageDialog("操作失败", message, self).exec()
        elif level == "success":
            # 关键操作反馈（如部署或恢复）
            if "后台抓包脚本已成功启动" in message:
                self._manual_boot_started = True
                if hasattr(self, "bg_console_card") and not getattr(self, "_bg_console_manually_hidden", False):
                    self.bg_console_card.setVisible(True)
                    self.auto_capture_service.start_boot_log_stream(restart=True)
                    from datetime import datetime
                    self.append_bg_console_text(
                        f"[{datetime.now().strftime('%H:%M:%S')}] ▶ 后台抓包脚本已启动成功！\n"
                        f"⚡ 抓包进程正在后台自主运行，仅启用空闲无线频段，不影响正常业务 Wi-Fi\n"
                        f"📁 抓包产物实时保存在路由器 /root/cap/ 目录，随时可点击【一键拉取握手包】拉回本地\n"
                        f"----------------------------------------------------------------------\n"
                    )
                    self.auto_capture_service.poll_boot_log()
            if "后台抓包已安全停止" in message:
                if hasattr(self, "bg_console_card") and self.bg_console_card.isVisible():
                    from datetime import datetime
                    self.append_bg_console_text(
                        f"[{datetime.now().strftime('%H:%M:%S')}] ✔ {message}\n"
                    )
            if any(k in message for k in ("部署完成", "成功恢复", "已完全卸载")):
                MessageDialog("操作成功", message, self).exec()

    def dispose(self):
        """释放页面时断开信号连接与停止定时器。"""
        self._service_disposed = True
        self._start_preflight.cancel()
        if getattr(self, "_upgrade_progress", None) is not None:
            self._upgrade_progress.dispose()
        try:
            self._bg_status_timer.stop()
        except Exception:
            pass
        try:
            self.auto_capture_service.stop_boot_log_stream()
        except Exception:
            pass
        connections, self._service_connections = self._service_connections, []
        for connection in connections:
            try:
                QObject.disconnect(connection)
            except (RuntimeError, TypeError):
                # The sender may already have been deleted during app shutdown.
                pass


    def _on_upgrade_hcx_clicked(self):
        """在自动抓包页面一键触发升级 hcxdumptool 6.3.4。"""
        device = getattr(self.service, "current_device", None)
        if device and not is_modern_hcx_supported_on_device(device):
            MessageDialog(
                "不支持升级",
                f"当前路由器系统为 OpenWrt {getattr(device, 'version', '未知')}（内核 {getattr(device, 'kernel_version', '未知')}），"
                "底层不支持升级现代版 hcxdumptool。\n请将 OpenWrt 升级到 23 或更高版本以获得更好的抓包体验。",
                self,
            ).exec()
            return
        dep_service = getattr(self.service, "dependency_service", None)
        if dep_service is None:
            MessageDialog("操作失败", "抓包依赖服务未就绪，请在【路由器登录】页面重试。", self).exec()
            return
        if getattr(self, "_upgrade_progress", None) is None:
            from .capture_upgrade_progress import CaptureUpgradeProgress
            self._upgrade_progress = CaptureUpgradeProgress(self, dep_service)
        if not self._upgrade_progress.start():
            reason = dep_service.last_rejection_reason or "依赖任务未能启动，请在【路由器登录】页面查看详情。"
            MessageDialog("无法开始升级", reason, self).exec()
            return
        self.service.messageGenerated.emit("正在为当前路由器自动部署现代版 hcxdumptool 6.3.4...", "info")
