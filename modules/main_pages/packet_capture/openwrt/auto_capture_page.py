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

from PySide6.QtCore import Qt, Signal, Slot, QTimer, QSize
from PySide6.QtGui import QBrush, QColor, QFont, QTextCursor
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
from modules.capture_services.openwrt.models import OpenWrtRadioInfo
from modules.common_widgets.bounce_scroll_area import BounceScrollArea
from modules.common_widgets.common_dialogs import ConfirmDialog, MessageDialog, _create_dialog_button
from modules.common_widgets.frameless_dialog import FramelessDialog
from modules.config_manager import ConfigManager
from modules.dialog_theme import build_base_stylesheet, detect_light_theme
from modules.main_pages.packet_capture.ui_helpers import create_capture_card
from modules.main_pages.page_action_buttons import create_page_text_button
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


class AutoCaptureReportDialog(FramelessDialog):
    """离线成果拉取与智能诊断分析报告弹窗。"""

    def __init__(self, report: AutoCaptureDiagnosticReport, local_dir: str, parent=None):
        super().__init__("离线抓包成果与智能健康诊断报告", parent, inherit_parent_stylesheet=False)
        self.light_theme = self._detect_theme(parent)
        scale = UiScaleManager.for_widget(self)
        base_stylesheet = build_base_stylesheet(ConfigManager(), light_theme=self.light_theme, scale_manager=scale)
        dialog_extra_qss = self._build_report_dialog_stylesheet(self.light_theme)
        self.setStyleSheet(f"{base_stylesheet}\n\n{dialog_extra_qss}")
        self.set_scaled_size((800, 550), (1020, 720))

        # 头部状态卡片
        header_card = QFrame(self)
        header_card.setObjectName("reportHeaderCard")
        header_layout = QVBoxLayout(header_card)
        header_layout.setContentsMargins(14, 12, 14, 12)
        header_layout.setSpacing(6)

        title_lbl = QLabel(f"健康度状态: 【{report.health_title}】", header_card)
        title_lbl.setObjectName("reportTitleLabel")
        if report.health_level == "danger":
            title_color = "#dc2626" if self.light_theme else "#ef4444"
        elif report.health_level == "warning":
            title_color = "#d97706" if self.light_theme else "#f59e0b"
        else:
            title_color = "#16a34a" if self.light_theme else "#10b981"
        title_lbl.setStyleSheet(f"color: {title_color}; font-size: 14px; font-weight: bold;")
        header_layout.addWidget(title_lbl)

        status_detail = QLabel(
            f"退出原因: {report.exit_reason} (退出代码: {report.exit_code if report.exit_code is not None else '未记录'})\n"
            f"路由器固件: {report.firmware_info or '未知'} | 工具版本: {report.hcx_version or '未知'} (方言: {report.dialect_mode or 'auto'})\n"
            f"BPF 过滤: {report.bpf_status}",
            header_card,
        )
        status_detail.setObjectName("reportDetailLabel")
        status_detail.setWordWrap(True)
        header_layout.addWidget(status_detail)

        if report.hardware_notes:
            for note in report.hardware_notes:
                nlbl = QLabel(f"● 说明: {note}", header_card)
                nlbl.setObjectName("reportNoteLabel")
                nlbl.setWordWrap(True)
                header_layout.addWidget(nlbl)

        self.content_layout.addWidget(header_card)

        # 统计看板
        metrics_row = QHBoxLayout()
        metrics_row.setSpacing(16)
        pmkid_color = "#0284c7" if self.light_theme else "#38bdf8"
        pmkid_lbl = QLabel(f"捕获 PMKID: {report.pmkid_count} 个", self)
        pmkid_lbl.setStyleSheet(f"color: {pmkid_color}; font-weight: bold; font-size: 13px;")
        metrics_row.addWidget(pmkid_lbl)

        eapol_color = "#16a34a" if self.light_theme else "#10b981"
        eapol_lbl = QLabel(f"捕获握手包 (EAPOL): {report.eapol_count} 个", self)
        eapol_lbl.setStyleSheet(f"color: {eapol_color}; font-weight: bold; font-size: 13px;")
        metrics_row.addWidget(eapol_lbl)

        ap_lbl = QLabel(f"识别周边 AP 热点: {len(report.targets)} 个", self)
        ap_lbl.setObjectName("reportMetricsText")
        metrics_row.addWidget(ap_lbl)

        pcap_count = len(report.captured_files)
        pcap_lbl = QLabel(f"生成的 .pcapng 原始包: {pcap_count} 个", self)
        pcap_lbl.setObjectName("reportMetricsText")
        metrics_row.addWidget(pcap_lbl)
        metrics_row.addStretch(1)
        self.content_layout.addLayout(metrics_row)

        # AP 列表与详细日志选项卡
        tabs = QTabWidget(self)
        tabs.setObjectName("reportTabs")

        # Tab 1: 发现的 AP 热点
        ap_widget = QWidget(tabs)
        ap_layout = QVBoxLayout(ap_widget)
        ap_layout.setContentsMargins(6, 8, 6, 6)
        ap_table = QTableWidget(ap_widget)
        ap_table.setObjectName("reportTable")
        ap_table.setAlternatingRowColors(True)
        ap_table.setColumnCount(5)
        ap_table.setHorizontalHeaderLabels(["BSSID (MAC)", "SSID (Wi-Fi名称)", "信道", "握手帧构成", "捕获状态"])
        ap_table.setRowCount(len(report.targets))
        success_color = QColor("#16a34a") if self.light_theme else QColor("#4ade80")
        warning_color = QColor("#d97706") if self.light_theme else QColor("#fbbf24")
        muted_color = QColor("#64748b") if self.light_theme else QColor("#94a3b8")
        for row, target in enumerate(report.targets):
            ap_table.setItem(row, 0, QTableWidgetItem(target.bssid))
            ap_table.setItem(row, 1, QTableWidgetItem(target.ssid or "<隐藏或未解析>"))
            ap_table.setItem(row, 2, QTableWidgetItem(target.channel or "--"))

            # 握手帧构成列
            frames = set(re.findall(r"M[1-4]", target.handshake_frames))
            if "M1" in frames and "M2" in frames:
                if len(frames) == 4:
                    frame_text = "M1+M2+M3+M4 (完整)"
                elif "M3" in frames:
                    frame_text = "M1+M2+M3 (可跑包)"
                else:
                    frame_text = "M1+M2 (可跑包)"
                frame_color = success_color
            elif "M2" in frames and "M3" in frames:
                frame_text = "M2+M3 (可跑包)"
                frame_color = success_color
            elif target.handshake_frames:
                frame_text = f"仅 {target.handshake_frames} (残缺)"
                frame_color = warning_color
            elif target.has_eapol:
                frame_text = "已检测到 EAPOL"
                frame_color = warning_color
            else:
                frame_text = "--"
                frame_color = muted_color

            frame_item = QTableWidgetItem(frame_text)
            frame_item.setForeground(QBrush(frame_color))
            ap_table.setItem(row, 3, frame_item)

            # 捕获状态列
            cap_status = []
            if target.has_pmkid:
                cap_status.append("PMKID")
            if target.has_eapol:
                cap_status.append("握手包")
            status_text = " + ".join(cap_status) if cap_status else "信标嗅探中"
            item = QTableWidgetItem(status_text)
            item.setForeground(QBrush(success_color if cap_status else muted_color))
            ap_table.setItem(row, 4, item)
        ap_table.setColumnWidth(0, scale.px(145))
        ap_table.setColumnWidth(1, scale.px(210))
        ap_table.setColumnWidth(2, scale.px(65))
        ap_table.setColumnWidth(3, scale.px(210))
        ap_table.horizontalHeader().setStretchLastSection(True)
        ap_layout.addWidget(ap_table)
        tabs.addTab(ap_widget, f"AP 热点列表 ({len(report.targets)})")

        # Tab 2: 完整分析诊断文本
        log_widget = QWidget(tabs)
        log_layout = QVBoxLayout(log_widget)
        log_layout.setContentsMargins(6, 8, 6, 6)
        log_view = QTextBrowser(log_widget)
        log_view.setObjectName("reportLogBrowser")
        summary_text = (report.raw_summary or "").strip()
        if not summary_text:
            summary_text = (
                "当前本地目录尚未检测到抓包会话日志 (script_*.log / run_*.log)。\n\n"
                "操作指引：\n"
                "1. 若路由器刚完成脱机抓包，请先点击【📥 一键拉取离线包】将成果文件同步到电脑；\n"
                "2. 同步完成后再次点击【📊 智能分析日志】即可查看完整的抓包分析与热点诊断；\n"
                f"3. 您也可以点击下方的“📂 打开本地成果文件夹”直接查看本地目录 ({local_dir})。"
            )
        log_view.setPlainText(summary_text)
        log_layout.addWidget(log_view)
        tabs.addTab(log_widget, "诊断摘要文本")

        self.content_layout.addWidget(tabs, 1)

        # 底部操作栏
        dir_lbl = QLabel(f"拉取成果目录: {local_dir}", self)
        dir_lbl.setObjectName("settingsHint")
        self.button_layout.addWidget(dir_lbl)
        self.button_layout.addStretch(1)

        open_btn = _create_dialog_button("📂 打开本地成果文件夹", self, "settingsSecondaryButton")
        open_btn.clicked.connect(lambda: self._open_dir(local_dir))
        self.button_layout.addWidget(open_btn)

        close_btn = _create_dialog_button("确定", self, "settingsPrimaryButton")
        close_btn.clicked.connect(self.accept)
        self.button_layout.addWidget(close_btn)

    @staticmethod
    def _detect_theme(widget) -> bool:
        current = widget
        while current is not None:
            if hasattr(current, "_light_theme"):
                return bool(current._light_theme)
            if hasattr(current, "light_theme"):
                return bool(current.light_theme)
            current = current.parent() if hasattr(current, "parent") else None
        return detect_light_theme(widget, fallback=False)

    @staticmethod
    def _build_report_dialog_stylesheet(is_light: bool) -> str:
        if is_light:
            return """
            QFrame#reportHeaderCard {
                background-color: #f8fafc;
                border: 1px solid #cbd5e1;
                border-radius: 8px;
            }
            QLabel#reportTitleLabel {
                font-size: 14px;
                font-weight: bold;
            }
            QLabel#reportDetailLabel {
                color: #334155;
                font-size: 12px;
            }
            QLabel#reportNoteLabel {
                color: #0284c7;
                font-size: 12px;
            }
            QLabel#reportMetricsText {
                color: #1e293b;
                font-size: 13px;
            }
            QTabWidget#reportTabs::pane {
                border: 1px solid #cbd5e1;
                background: #ffffff;
                border-radius: 6px;
                top: -1px;
            }
            QTabWidget#reportTabs QTabBar::tab {
                background: #f1f5f9;
                color: #64748b;
                border: 1px solid #cbd5e1;
                border-bottom: none;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                padding: 6px 16px;
                margin-right: 4px;
                font-weight: 500;
            }
            QTabWidget#reportTabs QTabBar::tab:selected {
                background: #ffffff;
                color: #0284c7;
                border-bottom: 1px solid #ffffff;
                font-weight: bold;
            }
            QTabWidget#reportTabs QTabBar::tab:hover:!selected {
                color: #0f172a;
                background: #e2e8f0;
            }
            QTableWidget#reportTable {
                background-color: #ffffff;
                alternate-background-color: #f8fafc;
                color: #0f172a;
                gridline-color: #e2e8f0;
                border: none;
                border-radius: 4px;
                selection-background-color: #e0f2fe;
                selection-color: #0369a1;
            }
            QTableWidget#reportTable::item {
                padding: 4px 8px;
                border: none;
            }
            QTableWidget#reportTable QHeaderView::section {
                background-color: #f1f5f9;
                color: #475569;
                padding: 6px 8px;
                border: none;
                border-right: 1px solid #e2e8f0;
                border-bottom: 1px solid #e2e8f0;
                font-weight: bold;
            }
            QTextBrowser#reportLogBrowser {
                background-color: #f8fafc;
                color: #0f172a;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                padding: 10px;
                font-family: "Consolas", "Courier New", monospace;
                font-size: 12px;
                line-height: 1.4;
            }
            """
        else:
            return """
            QFrame#reportHeaderCard {
                background-color: #1e293b;
                border: 1px solid #334155;
                border-radius: 8px;
            }
            QLabel#reportTitleLabel {
                font-size: 14px;
                font-weight: bold;
            }
            QLabel#reportDetailLabel {
                color: #94a3b8;
                font-size: 12px;
            }
            QLabel#reportNoteLabel {
                color: #38bdf8;
                font-size: 12px;
            }
            QLabel#reportMetricsText {
                color: #e2e8f0;
                font-size: 13px;
            }
            QTabWidget#reportTabs::pane {
                border: 1px solid #334155;
                background: #0f172a;
                border-radius: 6px;
                top: -1px;
            }
            QTabWidget#reportTabs QTabBar::tab {
                background: #1e293b;
                color: #94a3b8;
                border: 1px solid #334155;
                border-bottom: none;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                padding: 6px 16px;
                margin-right: 4px;
                font-weight: 500;
            }
            QTabWidget#reportTabs QTabBar::tab:selected {
                background: #0f172a;
                color: #38bdf8;
                border-bottom: 1px solid #0f172a;
                font-weight: bold;
            }
            QTabWidget#reportTabs QTabBar::tab:hover:!selected {
                color: #f1f5f9;
                background: #334155;
            }
            QTableWidget#reportTable {
                background-color: #0f172a;
                alternate-background-color: #141d2e;
                color: #f8fafc;
                gridline-color: #334155;
                border: none;
                border-radius: 4px;
                selection-background-color: #1e3a8a;
                selection-color: #ffffff;
            }
            QTableWidget#reportTable::item {
                padding: 4px 8px;
                border: none;
            }
            QTableWidget#reportTable QHeaderView::section {
                background-color: #1e293b;
                color: #cbd5e1;
                padding: 6px 8px;
                border: none;
                border-right: 1px solid #334155;
                border-bottom: 1px solid #334155;
                font-weight: bold;
            }
            QTextBrowser#reportLogBrowser {
                background-color: #0b1120;
                color: #cbd5e1;
                border: 1px solid #334155;
                border-radius: 6px;
                padding: 10px;
                font-family: "Consolas", "Courier New", monospace;
                font-size: 12px;
                line-height: 1.4;
            }
            """

    def _open_dir(self, dir_path: str):
        try:
            if os.path.exists(dir_path):
                os.startfile(dir_path)
            else:
                os.makedirs(dir_path, exist_ok=True)
                os.startfile(dir_path)
        except Exception:
            pass


def is_modern_hcx_supported_on_device(device) -> bool:
    """判定当前路由器环境是否支持升级并运行现代版 hcxdumptool (6.3.4+)。

    现代版 hcxdumptool 依赖 Netlink / nl80211 原生套接字，官方自 OpenWrt 23.05 起（Linux 内核 ≥ 5.15）
    开始提供支持。若路由器固件版本低于 23.05 或 Linux 内核低于 5.15，则不支持升级新版本。
    """
    if not device:
        return False

    kernel_str = str(getattr(device, "kernel_version", "") or "").strip()
    km = re.match(r"^(\d+)\.(\d+)", kernel_str)
    if km:
        k_major, k_minor = int(km.group(1)), int(km.group(2))
        if (k_major, k_minor) < (5, 15):
            return False

    version_str = str(getattr(device, "version", "") or "").strip()
    vm = re.match(r"^(\d+)\.(\d+)", version_str)
    if vm:
        major, minor = int(vm.group(1)), int(vm.group(2))
        if (major, minor) < (23, 5):
            return False
        return True

    # 若固件版本无法识别（如第三方固件），且内核达到 5.15+，视为支持
    if km and (int(km.group(1)), int(km.group(2))) >= (5, 15):
        return True

    return False


def check_auto_capture_environment(device, snapshot=None) -> tuple[bool, str, str]:
    """判定自动抓包运行环境与工具版本兼容性。
    返回 (has_issue, issue_type, issue_detail)。
    issue_type: 'hcx_outdated_upgradeable' | 'hcx_outdated_unsupported' | 'firmware_old' | ''
    """
    if not device:
        return False, "", ""

    # 1. 优先检查实机上的 hcxdumptool 版本（动态提取实际版本，不硬编码 6.2.4）
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
                ver_label = f"hcxdumptool {detected_ver}" if detected_ver else "旧版 hcxdumptool"
                if is_modern_hcx_supported_on_device(device):
                    return True, "hcx_outdated_upgradeable", f"检测到旧版抓包工具 ({ver_label}，支持升级)"
                else:
                    return True, "hcx_outdated_unsupported", f"检测到旧版抓包工具 ({ver_label}，不支持升级)"

    version_str = str(getattr(device, "version", "") or "").strip()
    kernel_str = str(getattr(device, "kernel_version", "") or "").strip()

    # 2. 检查固件主发行版号是否低于 23.05
    vm = re.match(r"^(\d+)\.(\d+)", version_str)
    if vm:
        major, minor = int(vm.group(1)), int(vm.group(2))
        if (major, minor) < (23, 5):
            return True, "firmware_old", f"固件版本 OpenWrt {major:02d}.{minor:02d} 低于 23.05"
        elif (major, minor) >= (23, 5):
            return False, "", ""

    # 3. 对第三方固件(如 Kiddin'、自编译或日期命名)，按 Linux 内核版本检验
    km = re.match(r"^(\d+)\.(\d+)", kernel_str)
    if km:
        k_major, k_minor = int(km.group(1)), int(km.group(2))
        if (k_major, k_minor) < (5, 15):
            return True, "firmware_old", f"Linux 内核 {kernel_str} 低于 5.15 基线 (对应 OpenWrt < 23.05)"

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

        # 1. 全局总控与系统资源守卫紧凑控制栏 (整合操作按钮、资源指标与视图切换，免除大文本占用)
        self._build_guard_section()

        # 2. 射频卡片容器区
        self._build_cards_container()

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
        self.auto_capture_service.bootStatusChecking.connect(self._on_boot_status_checking)
        self.auto_capture_service.bootStatusChanged.connect(self._on_boot_status_changed)
        self.auto_capture_service.resourceStatsUpdated.connect(self._on_resource_stats_updated)
        self.auto_capture_service.radioStatsUpdated.connect(self._on_radio_stats_updated)
        self.auto_capture_service.radioStreamOutput.connect(self._on_radio_stream_output)
        self.auto_capture_service.radioStateChanged.connect(self._on_radio_state_changed)
        self.auto_capture_service.messageGenerated.connect(self._on_message_generated)
        self.auto_capture_service.offlineArtifactsPulled.connect(self._on_pull_offline_success)
        self.auto_capture_service.offlinePullFailed.connect(self._on_pull_offline_error)
        self.auto_capture_service.radioIdleStatusUpdated.connect(self._on_radio_idle_status_updated)
        self.auto_capture_service.bootLogUpdated.connect(self._on_boot_log_updated)

        signals_service = getattr(self.service, "router_signals", None)
        if signals_service is not None and hasattr(signals_service, "snapshotChanged"):
            signals_service.snapshotChanged.connect(self._on_signals_snapshot_changed)

        # 连接建立时检测运行中抓包进程的弹窗控制标记
        self._connected_running_prompted = False
        self._initial_loaded = False
        self._last_loading_state_key = None
        self._last_rendered_boot_status = None

        # 后台抓包状态自动巡检定时器 (仅在切换到后台抓包且已连接时运行)
        self._bg_status_timer = QTimer(self)
        self._bg_status_timer.setInterval(15000)  # 放宽至 15 秒巡检间隔，降低设备负荷并消除高频刷新打扰
        self._bg_status_timer.timeout.connect(self._poll_bg_boot_status)

        # 若服务层已有自启状态快照，立即回显展示
        if self.auto_capture_service.boot_status is not None:
            self._on_boot_status_changed(self.auto_capture_service.boot_status)

        service.openwrtSnapshotChanged.connect(self._on_snapshot_changed)
        self._on_snapshot_changed(service.openwrt_snapshot)

    def _poll_bg_boot_status(self):
        """周期性轮询刷新后台自启与脚本状态。"""
        snapshot = getattr(self, "_last_snapshot", None) or getattr(self.service, "openwrt_snapshot", None)
        conn_state = getattr(snapshot, "connection_state", None)
        connected = bool(snapshot and getattr(conn_state, "value", conn_state) == "connected")
        if connected and self.sub_tabs.currentIndex() == 1:
            self.auto_capture_service.refresh_boot_status()

    def _on_signals_snapshot_changed(self, snapshot):
        """当在信号管理页面修改了信号状态后，自动触发前台射频业务状态异步刷新。"""
        conn_state = getattr(self.service.openwrt_snapshot, "connection_state", None)
        if getattr(conn_state, "value", conn_state) == "connected":
            self.auto_capture_service.refresh_radio_idle_status()

    def _on_radio_idle_status_updated(self, free_map: dict):
        """远端射频业务状态异步返回后，立即精准刷新射频卡片的空闲/使用中徽章与接管复选框。"""
        for radio_name, is_free in free_map.items():
            card = self.radio_cards.get(radio_name)
            if card is not None:
                from dataclasses import replace
                card.radio_info = replace(card.radio_info, free=is_free)
                if not card._capturing:
                    if is_free:
                        card.status_badge.setText("【空闲】(安全无感)")
                        card.status_badge.setStyleSheet("color: #10b981; font-weight: bold;")
                    else:
                        card.status_badge.setText("【正在使用】(业务已在线)")
                        card.status_badge.setStyleSheet("color: #f59e0b; font-weight: bold;")
                    card.takeover_check.setVisible(not is_free)

    def _on_sub_tab_changed(self, index: int):
        """子标签页切换时响应：每当切换到前台抓包或后台抓包时，均刷新对应页面上的状态。"""
        if index == 0:
            self._bg_status_timer.stop()
            self.refresh_foreground_status()
        elif index == 1:
            self.refresh_background_status()

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

        self.version_warning_title = QLabel("⚠️ 固件版本过低预警（建议升级固件至 OpenWrt 23.05+）", self.version_warning_card)
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
        """清空后台抓包控制台。"""
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

    def _on_boot_log_updated(self, log_text: str):
        """接收后台抓包日志流更新。"""
        if not hasattr(self, "bg_console_card") or self.bg_console_card.isHidden():
            return
        cur_text = self.bg_console.toPlainText()
        if log_text not in cur_text:
            self.append_bg_console_text(f"{log_text}\n")

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
            run_desc = "正在自主抓包" if (b_status and b_status.is_running) else "待机就绪"
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
        """构建【开机自启管理】面板，采用分块式现代指标看板设计。"""
        card, layout = create_capture_card(
            self.bg_content,
            "开机自启管理",
            "配置 OpenWrt 系统级服务 (/etc/init.d/luxiaoguan-autocap)。拔下网线拿到现场插电即可自主抓包，断电不丢数据；带回连线一键拉取成果。",
            collapsible=False,
        )
        self.boot_card = card

        # 1. 顶部状态徽章与服务路径（就绪提示与状态信息）
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(12)

        self.boot_status_badge = QLabel("【检测中...】", card)
        self.boot_status_badge.setObjectName("captureFormLabel")
        self.boot_status_badge.setStyleSheet("color: #94a3b8; font-weight: bold;")
        header_row.addWidget(self.boot_status_badge)

        self.boot_status_desc = QLabel("OpenWrt 系统自启守护服务", card)
        self.boot_status_desc.setObjectName("captureDescription")
        header_row.addWidget(self.boot_status_desc)

        # 保持别名兼容
        self.boot_status_label = self.boot_status_badge

        header_row.addStretch(1)

        self.service_path_label = QLabel("⚙ 系统守护服务: /etc/init.d/luxiaoguan-autocap", card)
        self.service_path_label.setObjectName("captureDescription")
        self.service_path_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        header_row.addWidget(self.service_path_label)
        layout.addLayout(header_row)

        # 1.1 正在抓包运行警示与收尾操作条 (仅后台抓包运行时可见)
        self.running_alert_widget = QWidget(card)
        r_layout = QHBoxLayout(self.running_alert_widget)
        r_layout.setContentsMargins(10, 6, 10, 6)
        r_layout.setSpacing(10)
        self.running_alert_widget.setStyleSheet(
            "background: rgba(16, 185, 129, 0.15); border: 1px solid #10b981; border-radius: 6px;"
        )
        self.running_alert_label = QLabel("⚡ 检测到后台自主抓包正在运行中，抓包文件实时保存在路由器 (/root/cap/)", self.running_alert_widget)
        self.running_alert_label.setStyleSheet("color: #10b981; font-weight: bold;")
        r_layout.addWidget(self.running_alert_label, 1)

        self.stop_capture_btn = QPushButton("⏹ 结束并收尾抓包", self.running_alert_widget)
        self.stop_capture_btn.setObjectName("captureSecondaryButton")
        self.stop_capture_btn.setStyleSheet("color: #ef4444; font-weight: bold; border-color: #ef4444;")
        self.stop_capture_btn.setToolTip("停止后台抓包并安全保存数据，自动恢复无线网络正常状态")
        self.stop_capture_btn.clicked.connect(self._on_stop_and_cleanup_clicked)
        r_layout.addWidget(self.stop_capture_btn)

        self.running_alert_widget.setVisible(False)
        layout.addWidget(self.running_alert_widget)

        # 2. 核心状态指标看板 (对齐前台抓包卡片设计)
        self.boot_metrics_frame = QFrame(card)
        self.boot_metrics_frame.setObjectName("captureCard")
        self._update_boot_metrics_style()
        self._update_status_banner_style()
        metrics_layout = QHBoxLayout(self.boot_metrics_frame)
        metrics_layout.setContentsMargins(12, 8, 12, 8)
        metrics_layout.setSpacing(18)

        # 指标 1：抓包脚本
        self.metric_script_label = QLabel("📄 抓包脚本: 检测中...", self.boot_metrics_frame)
        self.metric_script_label.setObjectName("captureDescription")
        metrics_layout.addWidget(self.metric_script_label)

        # 保持别名兼容
        self.script_loc_label = self.metric_script_label

        # 指标 2：脚本版本
        self.metric_version_label = QLabel("🏷 版本: --", self.boot_metrics_frame)
        self.metric_version_label.setObjectName("captureDescription")
        metrics_layout.addWidget(self.metric_version_label)

        # 指标 3：系统服务
        self.metric_service_label = QLabel("🛡 系统服务: 检测中...", self.boot_metrics_frame)
        self.metric_service_label.setObjectName("captureDescription")
        metrics_layout.addWidget(self.metric_service_label)

        # 指标 4：自启配置
        self.metric_autostart_label = QLabel("🚀 自启配置: 检测中...", self.boot_metrics_frame)
        self.metric_autostart_label.setObjectName("captureDescription")
        metrics_layout.addWidget(self.metric_autostart_label)

        # 指标 5：后台进程
        self.metric_running_label = QLabel("⚡ 运行状态: 检测中...", self.boot_metrics_frame)
        self.metric_running_label.setObjectName("captureDescription")
        metrics_layout.addWidget(self.metric_running_label)

        metrics_layout.addStretch(1)
        layout.addWidget(self.boot_metrics_frame)

        # 3. 版本比对告警行 (默认隐藏)
        self.version_diff_widget = QWidget(card)
        v_layout = QHBoxLayout(self.version_diff_widget)
        v_layout.setContentsMargins(0, 0, 0, 0)
        v_layout.setSpacing(10)
        self.version_diff_label = QLabel(self.version_diff_widget)
        self.version_diff_label.setStyleSheet("color: #f59e0b; font-weight: bold;")
        v_layout.addWidget(self.version_diff_label)

        self.update_script_btn = QPushButton("▶ 立即更新脚本", self.version_diff_widget)
        self.update_script_btn.setObjectName("downloadActionButton")
        self.update_script_btn.clicked.connect(self.auto_capture_service.install_or_update_boot_service)
        v_layout.addWidget(self.update_script_btn)
        v_layout.addStretch(1)
        self.version_diff_widget.setVisible(False)
        layout.addWidget(self.version_diff_widget)

        # 4. 系统服务安装与运维分组（安装服务的按键独立分组放置）
        service_row = QHBoxLayout()
        service_row.setContentsMargins(0, 4, 0, 0)
        service_row.setSpacing(10)

        self.install_boot_btn = create_page_text_button(card, "📤 上传/同步脚本与服务", "将本地最新抓包脚本与自启服务部署至路由器")
        self.install_boot_btn.clicked.connect(self.auto_capture_service.install_or_update_boot_service)
        service_row.addWidget(self.install_boot_btn)

        self.uninstall_boot_btn = QPushButton("🗑 完全卸载自启服务", card)
        self.uninstall_boot_btn.setObjectName("captureSecondaryButton")
        self.uninstall_boot_btn.setToolTip("完全卸载路由器上的自启服务与抓包脚本（已保存的抓包文件将被保留）")
        self.uninstall_boot_btn.clicked.connect(self._on_uninstall_clicked)
        service_row.addWidget(self.uninstall_boot_btn)

        service_row.addStretch(1)
        layout.addLayout(service_row)

        # 5. 抓包执行与成果管理分组（自启、手动启动，加分割线后放一键拉取3个按钮）
        exec_row = QHBoxLayout()
        exec_row.setContentsMargins(2, 6, 0, 0)
        exec_row.setSpacing(12)
        exec_row.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self.boot_enable_check = QCheckBox("开机自启抓包", card)
        self.boot_enable_check.setObjectName("captureCheckBox")
        self.boot_enable_check.setEnabled(False)
        self.boot_enable_check.setToolTip("请先连接路由器")
        self.boot_enable_check.clicked.connect(self._on_boot_check_clicked)
        exec_row.addWidget(self.boot_enable_check)

        self.start_script_btn = create_page_text_button(
            card,
            "▶ 手动启动脚本",
            "立即在后台启动抓包\n【说明】：只会使用未开启 Wi-Fi 的空闲无线频段，正在正常上网的 Wi-Fi 不受任何影响；抓包数据实时保存，断电不丢失",
        )
        self.start_script_btn.clicked.connect(self._on_start_script_clicked)
        exec_row.addWidget(self.start_script_btn)

        # 中间垂直分割线
        exec_row.addSpacing(4)
        self.exec_divider = QFrame(card)
        self.exec_divider.setFixedWidth(1)
        self.exec_divider.setFixedHeight(22)
        exec_row.addWidget(self.exec_divider)
        exec_row.addSpacing(4)

        # 一键拉取 3 个按钮（紧随手动启动与分割线之后）
        out_dir = self.auto_capture_service.get_output_dir()
        self.pull_offline_btn = create_page_text_button(
            card,
            "📥 一键拉取离线包",
            f"从路由器 /root/cap/ 下载全部抓包文件与日志到本地电脑：\n【本地保存位置】：{out_dir}\n（仅从路由器下载文件，如需查看健康度请点击旁边的“智能分析日志”）",
        )
        self.pull_offline_btn.clicked.connect(self._on_pull_offline_clicked)
        exec_row.addWidget(self.pull_offline_btn)

        self.analyze_offline_btn = create_page_text_button(
            card,
            "📊 智能分析日志",
            f"对本地已拉取的抓包文件与会话日志进行健康度诊断分析：\n【分析目录】：{out_dir}\n识别捕获握手包与PMKID数量、周边热点、运行退出状态与排错建议",
        )
        self.analyze_offline_btn.clicked.connect(self._on_analyze_offline_clicked)
        exec_row.addWidget(self.analyze_offline_btn)

        self.bg_open_output_dir_btn = QPushButton("📂 打开输出目录", card)
        self.bg_open_output_dir_btn.setObjectName("captureSecondaryButton")
        self.bg_open_output_dir_btn.setToolTip(f"在文件资源管理器中打开电脑上的抓包输出目录：\n{out_dir}")
        self.bg_open_output_dir_btn.clicked.connect(self._on_open_output_dir_clicked)
        exec_row.addWidget(self.bg_open_output_dir_btn)

        exec_row.addStretch(1)
        layout.addLayout(exec_row)

        self._update_boot_metrics_style()
        self._update_status_banner_style()
        self.bg_layout.addWidget(card)

        # 4. 后台抓包实时终端控制台卡片（卡片外部独立卡片，手动启动后显示，停止后隐藏）
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

        self.bg_clear_btn = QPushButton("清空", self.bg_console_card)
        self.bg_clear_btn.setObjectName("captureSecondaryButton")
        self.bg_clear_btn.clicked.connect(self._clear_bg_console)
        console_bar.addWidget(self.bg_clear_btn)

        bg_console_layout.addLayout(console_bar)

        self.bg_console = QPlainTextEdit(self.bg_console_card)
        self.bg_console.setReadOnly(True)
        self.bg_console.setMaximumBlockCount(1500)
        font = QFont("Consolas", 9)
        font.setStyleHint(QFont.Monospace)
        self.bg_console.setFont(font)
        self.bg_console.setMinimumHeight(150)
        self.bg_console.setMaximumHeight(260)
        bg_console_layout.addWidget(self.bg_console)

        self.bg_console_card.setVisible(False)
        self.bg_layout.addWidget(self.bg_console_card)

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
        """构建后台抓包现场脱机自主工作流程指南卡片。"""
        card, layout = create_capture_card(
            self.bg_content,
            "现场脱机抓包工作流程指南",
            "无需电脑长时连接，让路由器在现场自主全自动抓包，适合外出采集与多场地移动巡检。",
            collapsible=False,
        )
        out_dir = self.auto_capture_service.get_output_dir()
        steps_lbl = QLabel(
            "1. 【部署服务】：确认上方服务状态为“已安装”，并开启“开机自启开关”。\n"
            "2. 【现场断网插电】：拔下网线并将路由器放置在测试区域，接通电源即可自动启动空闲无线频段自主抓包。\n"
            "3. 【全自动实时保存】：抓到的握手包每 30 秒自动安全保存到路由器存储 (/root/cap/)，直接拔掉电源也不会丢失数据。\n"
            f"4. 【取回拉取与分析】：连上电脑后点击“一键拉取离线包”将文件保存至本地 ({out_dir})；随时点击“智能分析日志”可查看健康诊断报告，也可点击“打开本地成果文件夹”直接查看原始文件。",
            card,
        )
        steps_lbl.setObjectName("captureDescription")
        steps_lbl.setWordWrap(True)
        steps_lbl.setStyleSheet("line-height: 150%; padding: 4px 0;")
        layout.addWidget(steps_lbl)

        self.bg_layout.addWidget(card)
        self.bg_layout.addStretch(1)

    def _build_guard_section(self):
        """构建紧凑型【全局总控与系统资源守卫】控制栏。

        去除了占用大量垂直空间的卡片标题和描述文字，将全局启停、本地成果、
        内存/Flash资源指标与视图模式切换整合为单行紧凑控制栏。
        """
        self.guard_card = QFrame(self.fg_content)
        self.guard_card.setObjectName("captureCard")
        self.guard_card.setAttribute(Qt.WA_StyledBackground, True)
        self.guard_card.setToolTip("实时监控远端内存与 Flash 剩余容量，支持一键操控全部可用射频并发抓包与安全停止。")

        guard_layout = QHBoxLayout(self.guard_card)
        guard_layout.setContentsMargins(12, 6, 12, 6)
        guard_layout.setSpacing(10)

        # 1. 全局启停与本地成果按钮
        self.start_all_btn = create_page_text_button(self.guard_card, "▶ 全部开始抓包", "启动所有可用射频并发抓包")
        self.start_all_btn.clicked.connect(self._on_start_all_clicked)
        guard_layout.addWidget(self.start_all_btn)

        self.stop_all_btn = create_page_text_button(self.guard_card, "⏹ 全部停止", "停止全部抓包并恢复 Wi-Fi")
        self.stop_all_btn.clicked.connect(self.auto_capture_service.stop_all)
        guard_layout.addWidget(self.stop_all_btn)

        self.open_output_dir_btn = QPushButton("📂 本地成果", self.guard_card)
        self.open_output_dir_btn.setObjectName("captureSecondaryButton")
        self.open_output_dir_btn.setToolTip("打开本地抓包成果目录")
        self.open_output_dir_btn.clicked.connect(self._on_open_output_dir_clicked)
        guard_layout.addWidget(self.open_output_dir_btn)
        self.export_logs_btn = QPushButton("\u5bfc\u51fa\u65e5\u5fd7", self.guard_card)
        self.export_logs_btn.setObjectName("captureSecondaryButton")
        guard_layout.addWidget(self.export_logs_btn)
        from .auto_capture_log_export import AutoCaptureLogExport
        self._log_export = AutoCaptureLogExport(self, self.export_logs_btn)

        guard_layout.addSpacing(8)

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

        # 3. 视图模式切换器 (整合到右侧)
        view_label = QLabel("视图:", self.guard_card)
        view_label.setObjectName("captureDescription")
        guard_layout.addWidget(view_label)

        self.radio_split = QRadioButton("◉ 同屏并排", self.guard_card)
        self.radio_split.setToolTip("同屏并排显示所有射频卡片")
        self.radio_split.setChecked(True)
        self.radio_split.toggled.connect(self._on_view_mode_toggled)
        guard_layout.addWidget(self.radio_split)

        self.radio_tabbed = QRadioButton("○ 分屏标签", self.guard_card)
        self.radio_tabbed.setToolTip("以多标签页方式切换各射频卡片")
        self.radio_tabbed.toggled.connect(self._on_view_mode_toggled)
        guard_layout.addWidget(self.radio_tabbed)

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

        # 同屏并排布局容器
        self.split_container = QWidget(self.cards_frame)
        self.split_layout = QHBoxLayout(self.split_container)
        self.split_layout.setContentsMargins(0, 0, 0, 0)
        self.split_layout.setSpacing(10)
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
        self.analyze_offline_btn.set_light_theme(self._light_theme)
        self.start_script_btn.set_light_theme(self._light_theme)
        self.start_all_btn.set_light_theme(self._light_theme)
        self.stop_all_btn.set_light_theme(self._light_theme)
        for card in self.radio_cards.values():
            card.set_light_theme(self._light_theme)
        self._update_boot_metrics_style()
        self._update_status_banner_style()

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
            self.start_all_btn.setEnabled(False)
            self.start_script_btn.setEnabled(False)
            self.install_boot_btn.setEnabled(False)
            self.pull_offline_btn.setEnabled(False)
            self.boot_enable_check.setEnabled(False)
            self.boot_enable_check.setChecked(False)
            self.boot_enable_check.setToolTip("请先连接路由器")
            self._update_loading_status("disconnected")
            return

        conn_state_val = getattr(snapshot.connection_state, "value", "")
        connected = conn_state_val == "connected"
        device = getattr(snapshot, "device", None)
        radios = list(device.radios) if (connected and device and hasattr(device, "radios")) else []

        self.start_all_btn.setEnabled(connected and bool(radios))
        boot_running = bool(self.auto_capture_service.boot_status and self.auto_capture_service.boot_status.is_running)
        self.start_script_btn.setEnabled(connected and not boot_running)
        self.install_boot_btn.setEnabled(connected)
        self.pull_offline_btn.setEnabled(connected)
        self.boot_enable_check.setEnabled(connected)

        if not connected:
            self._initial_loaded = False
            self._last_rendered_boot_status = None
            self.running_alert_widget.setVisible(False)
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

            if connected and has_issue:
                dev_ver = getattr(device, "version", "") or "未知版本"
                dev_kern = getattr(device, "kernel_version", "") or "未知"
                ver_display = f"hcxdumptool {detected_hcx_ver}" if detected_hcx_ver else "旧版 hcxdumptool"

                if (issue_type in ("hcx_outdated_upgradeable", "hcx_outdated") and is_modern_hcx_supported_on_device(device)):
                    self.version_warning_title.setText(f"⚠️ 检测到旧版抓包工具 ({ver_display}，需升级)")
                    self.version_warning_detail.setText(
                        f"当前路由器上安装的抓包工具为 {ver_display}（旧版方言）。\n"
                        f"该版本底层依赖已废弃的 Linux Wireless Extensions (WEXT) ioctl，无法在自动抓包创建的虚拟监听口上运行（会报错 Not a tty 导致秒退退出）。\n"
                        f"【解决办法】当前路由器支持升级，请点击右侧【一键升级抓包工具 (6.3.4)】或在【路由器登录】页面更新依赖；当前旧版下可使用【常规抓包】模式（基于 airodump-ng）。"
                    )
                    if hasattr(self, "upgrade_hcx_btn"):
                        self.upgrade_hcx_btn.setVisible(True)
                elif issue_type == "hcx_outdated_unsupported" or (issue_type == "hcx_outdated" and not is_modern_hcx_supported_on_device(device)):
                    self.version_warning_title.setText(f"⚠️ 检测到旧版抓包工具 ({ver_display})")
                    self.version_warning_detail.setText(
                        f"当前路由器上安装的抓包工具为 {ver_display}。\n"
                        f"工具是旧版本，可能无法正常抓包。当前路由器系统为 OpenWrt {dev_ver}（Linux 内核 {dev_kern}），底层不支持升级新版抓包工具。\n"
                        f"【建议操作】当前可先尝试抓包（操作方法：点击下方【手动启动】按钮看是否能稳定运行 2 分钟，或直接切换到【前台抓包】执行看是否报错）。若无法正常抓包，建议将 OpenWrt 升级到 23 或更高版本以获得更好的抓包体验，或在当前固件下使用【常规抓包】模式（基于 airodump-ng）。"
                    )
                    if hasattr(self, "upgrade_hcx_btn"):
                        self.upgrade_hcx_btn.setVisible(False)
                else:
                    self.version_warning_title.setText("⚠️ 固件版本过低预警（建议升级固件至 OpenWrt 23.05+）")
                    self.version_warning_detail.setText(
                        f"检测到当前路由器系统为 OpenWrt {dev_ver}（Linux 内核 {dev_kern}，{issue_detail}）。\n"
                        f"自动抓包核心依赖现代 Netlink / nl80211 原生监听架构（需要 Linux 内核 ≥ 5.15 与 hcxdumptool 6.3.4+）。"
                        f"旧版系统官方源提供的 hcxdumptool 底层依赖已废弃的 WEXT ioctl，在自动抓包创建的虚拟监听口上运行会报错 Not a tty 导致秒退。\n"
                        f"【建议操作】强烈建议将路由器升级至 OpenWrt 23.05 或 24.10 以上固件以获得完整的自动抓包能力；在当前固件下请使用【常规抓包】模式（基于 airodump-ng）。"
                    )
                    if hasattr(self, "upgrade_hcx_btn"):
                        self.upgrade_hcx_btn.setVisible(False)
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
                self.split_layout.addWidget(card, 1)
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

    def _on_boot_status_changed(self, status: AutoCaptureBootStatus, force: bool = False):
        """自启状态更新，结构化指标看板展示。"""
        self._last_boot_status = status
        # 相同状态避免重复重刷控件树，消除频繁重绘与闪烁
        if not force and getattr(self, "_last_rendered_boot_status", None) == status:
            return
        self._last_rendered_boot_status = status
        # 1. 顶部大号状态徽章
        if status.is_running:
            self.boot_status_badge.setText("【抓包运行中】")
            self.boot_status_badge.setStyleSheet("color: #10b981; font-weight: bold;")
        elif status.installed:
            self.boot_status_badge.setText("【自启服务已就绪】")
            self.boot_status_badge.setStyleSheet("color: #38bdf8; font-weight: bold;")
        elif status.remote_script_exists:
            self.boot_status_badge.setText("【脚本就绪 · 待注册自启】")
            self.boot_status_badge.setStyleSheet("color: #f59e0b; font-weight: bold;")
        else:
            self.boot_status_badge.setText("【服务未部署】")
            self.boot_status_badge.setStyleSheet("color: #94a3b8; font-weight: bold;")

        # 1.1 正在运行状态告警横幅控制（仅在已连接且正在运行时显示，断开连接或未运行时隐藏）
        snapshot = getattr(self, "_last_snapshot", None) or getattr(self.service, "openwrt_snapshot", None)
        conn_state = getattr(snapshot, "connection_state", None)
        connected = bool(snapshot and getattr(conn_state, "value", conn_state) == "connected")
        if connected and status.is_running:
            self.running_alert_widget.setVisible(True)
            self.stop_capture_btn.setEnabled(True)
            if getattr(self, "_manual_boot_started", False) and hasattr(self, "bg_console_card"):
                self.bg_console_card.setVisible(True)
        else:
            self.running_alert_widget.setVisible(False)
            if hasattr(self, "bg_console_card"):
                self.bg_console_card.setVisible(False)
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

        # 自启配置
        if status.autostart_enabled:
            self.metric_autostart_label.setText("🚀 自启配置: 已启用")
            self.metric_autostart_label.setStyleSheet("color: #10b981; font-weight: bold;")
        else:
            self.metric_autostart_label.setText("🚀 自启配置: 已关闭")
            self.metric_autostart_label.setStyleSheet("color: #94a3b8;")

        # 进程状态
        if status.is_running:
            self.metric_running_label.setText("⚡ 运行状态: 正在抓包")
            self.metric_running_label.setStyleSheet("color: #10b981; font-weight: bold;")
        else:
            self.metric_running_label.setText("⚡ 运行状态: 空闲待机")
            self.metric_running_label.setStyleSheet("color: #94a3b8;")

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

        # 5. 手动启动脚本按钮状态同步
        self.start_script_btn.setEnabled(connected and not status.is_running)
        if not connected:
            self.start_script_btn.setToolTip("请先连接路由器")
        elif status.is_running:
            self.start_script_btn.setToolTip("后台抓包正在运行中\n【说明】：仅使用空闲无线频段在后台抓包，抓获的握手包实时保存在路由器 (/root/cap/)。如需停止请点击上方“结束并收尾抓包”。")
        else:
            self.start_script_btn.setToolTip("立即在后台启动抓包\n【说明】：只会使用未开启 Wi-Fi 的空闲无线频段，正在正常上网的 Wi-Fi 不受任何影响；抓包数据实时保存，断电不丢失")

        if connected:
            self._update_loading_status("ready")

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

    def _on_radio_stats_updated(self, radio_name: str, stats: dict):
        if radio_name in self.radio_cards:
            self.radio_cards[radio_name].apply_stats(stats)

    def _on_radio_stream_output(self, radio_name: str, text: str):
        if radio_name in self.radio_cards:
            self.radio_cards[radio_name].append_stream_text(text)

    def _on_radio_state_changed(self, radio_name: str, state: str):
        if radio_name in self.radio_cards:
            session = self.auto_capture_service.sessions.get(radio_name)
            err_msg = session.stats.error_message if session else ""
            self.radio_cards[radio_name].apply_state(state, err_msg)

    def _on_card_start_requested(self, radio_info: OpenWrtRadioInfo, channels: str, allow_takeover: bool):
        self.auto_capture_service.start_radio_capture(radio_info, channels, allow_takeover)

    def _on_card_stop_requested(self, radio_name: str):
        self.auto_capture_service.stop_radio_capture(radio_name)

    def _on_start_all_clicked(self):
        """全部开始抓包，带前置非空闲射频接管引导。"""
        if not self.radio_cards:
            MessageDialog("无法开始抓包", "当前未检测到任何可用的无线射频，请确认路由器已连接成功。", self).exec()
            return

        busy_unauthorized = [
            card for card in self.radio_cards.values()
            if not card.radio_info.free and not card.takeover_check.isChecked()
        ]
        if busy_unauthorized:
            names = [f"● 射频 {c.radio_name} ({'/'.join(c.radio_info.bands) if c.radio_info.bands else 'Wi-Fi'})" for c in busy_unauthorized]
            names_text = "\n".join(names)
            msg = (
                f"检测到以下无线射频当前正在发射 Wi-Fi 业务，且尚未勾选允许接管：\n\n{names_text}\n\n"
                "抓包必须临时安全接管射频以执行信道切换。\n"
                "停止抓包后，软件将自动原子级回滚恢复原有 Wi-Fi 业务。\n\n"
                "是否允许接管并全部开始抓包？"
            )
            dialog = ConfirmDialog("无线射频接管确认", msg, self)
            if dialog.exec() == ConfirmDialog.Accepted:
                for c in busy_unauthorized:
                    c.takeover_check.setChecked(True)
            else:
                return

        configs = []
        for card in self.radio_cards.values():
            configs.append({
                "radio_info": card.radio_info,
                "channels": card.channel_edit.text().strip(),
                "allow_takeover": card.takeover_check.isChecked(),
            })
        self.auto_capture_service.start_all(configs)

    def _on_boot_check_clicked(self, checked: bool):
        """响应用户点击开机自启开关。支持未部署时引导一键部署并开启。"""
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
                    self.boot_enable_check.setEnabled(False)
                    self.boot_status_badge.setText("【正在部署服务...】")
                    self.boot_status_badge.setStyleSheet("color: #38bdf8; font-weight: bold;")
                    self.auto_capture_service.install_or_update_boot_service(enable_after=True)
            else:
                self.boot_enable_check.setChecked(False)
            return

        self.boot_enable_check.setEnabled(False)
        action_name = "开启" if checked else "关闭"
        self.boot_status_badge.setText(f"【正在{action_name}自启...】")
        self.boot_status_badge.setStyleSheet("color: #38bdf8; font-weight: bold;")
        self.auto_capture_service.toggle_autostart(checked)

    def _on_start_script_clicked(self):
        """响应手动启动抓包脚本操作。"""
        snapshot = getattr(self, "_last_snapshot", None) or getattr(self.service, "openwrt_snapshot", None)
        conn_state = getattr(snapshot, "connection_state", None)
        connected = bool(snapshot and getattr(conn_state, "value", conn_state) == "connected")
        if not connected:
            MessageDialog("提示", "路由器尚未连接，请先连接路由器后再启动抓包脚本。", self).exec()
            return

        status = self.auto_capture_service.boot_status
        if status is None:
            MessageDialog("提示", "正在检测路由器状态，请稍后重试。", self).exec()
            return

        if status.is_running:
            MessageDialog("提示", "后台抓包脚本已在运行中，无需重复启动。\n如需重新开始，可先点击“结束并收尾抓包”。", self).exec()
            return

        if not status.remote_script_exists:
            msg = (
                "检测到路由器尚未准备好抓包环境。\n\n"
                "是否立即自动安装最新抓包支持，并直接启动后台抓包？\n"
                "（说明：后台抓包只会使用空闲无线频段，不影响当前正常上网的 Wi-Fi，数据实时保存）"
            )
            dialog = ConfirmDialog("安装并启动后台抓包", msg, self)
            if dialog.exec() == ConfirmDialog.Accepted:
                self.start_script_btn.setEnabled(False)
                self.boot_status_badge.setText("【正在准备并启动...】")
                self.boot_status_badge.setStyleSheet("color: #38bdf8; font-weight: bold;")
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
            self.start_script_btn.setEnabled(False)
            self.boot_status_badge.setText("【正在启动脚本...】")
            self.boot_status_badge.setStyleSheet("color: #38bdf8; font-weight: bold;")
            self.auto_capture_service.start_boot_script()

    def _on_stop_and_cleanup_clicked(self):
        """响应结束并收尾抓包操作（安全封包、清理网卡与临时文件）。"""
        self._manual_boot_started = False
        if hasattr(self, "bg_console_card"):
            self.bg_console_card.setVisible(False)
        self.stop_capture_btn.setEnabled(False)
        self.start_script_btn.setEnabled(False)
        self.boot_status_badge.setText("【正在收尾抓包...】")
        self.boot_status_badge.setStyleSheet("color: #f59e0b; font-weight: bold;")
        self.auto_capture_service.stop_and_cleanup_capture()

    def _on_uninstall_clicked(self):
        if ConfirmDialog("卸载自启服务", "确定完全卸载 /etc/init.d/luxiaoguan-autocap 与 /root/hcx_autocap.sh 吗？\n(/root/cap/ 产物将被保留)", self).exec() == ConfirmDialog.Accepted:
            self.auto_capture_service.uninstall_boot_service()

    def _on_pull_offline_clicked(self):
        """异步拉取远端 /root/cap/ 的离线抓包成果与日志到本地电脑。"""
        snapshot = getattr(self, "_last_snapshot", None) or getattr(self.service, "openwrt_snapshot", None)
        conn_state = getattr(snapshot, "connection_state", None)
        connected = bool(snapshot and getattr(conn_state, "value", conn_state) == "connected")
        if not connected:
            MessageDialog("提示", "路由器尚未连接，请先连接路由器后再拉取离线包。", self).exec()
            return

        self.pull_offline_btn.setEnabled(False)
        out_dir = self.auto_capture_service.get_output_dir()
        self.auto_capture_service.pull_offline_artifacts_async(str(out_dir))

    def _on_pull_offline_success(self, pulled_files: list, local_dir: str):
        self.pull_offline_btn.setEnabled(True)
        if pulled_files:
            msg = (
                f"已成功拉取 {len(pulled_files)} 个抓包成果与日志文件！\n\n"
                f"【保存在本地目录】：\n{local_dir}\n\n"
                "提示：您可以随时点击“📊 智能分析日志”查看详细诊断分析，也可点击“📂 打开输出目录”直接查看原始文件。"
            )
            MessageDialog("拉取成功", msg, self).exec()
        else:
            MessageDialog("提示", "路由器 /root/cap/ 目录下暂无抓包成果文件 (.pcapng / .log)。", self).exec()

    def _on_pull_offline_error(self, err_msg: str):
        self.pull_offline_btn.setEnabled(True)
        MessageDialog("拉取成果失败", f"从路由器拉取离线成果时出现异常:\n{err_msg}", self).exec()

    def _on_analyze_offline_clicked(self):
        """对本地成果目录中的离线抓包与日志进行智能健康诊断并弹出报告。"""
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
            if out_dir.exists():
                os.startfile(str(out_dir))
        except Exception:
            pass

    @Slot(str, str)
    def _on_message_generated(self, message: str, level: str):
        """统一捕获后台服务生成的消息，出现错误时在主线程弹出醒目提示。"""
        if level == "error":
            MessageDialog("操作失败", message, self).exec()
        elif level == "success":
            # 关键操作反馈（如部署或恢复）
            if "后台抓包脚本已成功启动" in message:
                self._manual_boot_started = True
                if hasattr(self, "bg_console_card"):
                    self.bg_console_card.setVisible(True)
                    from datetime import datetime
                    self.append_bg_console_text(
                        f"[{datetime.now().strftime('%H:%M:%S')}] ▶ 后台抓包脚本已启动成功！\n"
                        f"⚡ 抓包进程正在后台自主运行，仅启用空闲无线频段，不影响正常业务 Wi-Fi\n"
                        f"📁 抓包产物实时保存在路由器 /root/cap/ 目录，随时可点击【一键拉取离线包】拉回本地\n"
                        f"----------------------------------------------------------------------\n"
                    )
                    self.auto_capture_service.poll_boot_log()
            if any(k in message for k in ("部署完成", "成功恢复", "已完全卸载")):
                MessageDialog("操作成功", message, self).exec()

    def dispose(self):
        """释放页面时断开信号连接与停止定时器。"""
        if getattr(self, "_upgrade_progress", None) is not None:
            self._upgrade_progress.dispose()
        try:
            self._bg_status_timer.stop()
        except Exception:
            pass
        try:
            self.service.openwrtSnapshotChanged.disconnect(self._on_snapshot_changed)
        except Exception:
            pass
        try:
            self.auto_capture_service.bootStatusChanged.disconnect(self._on_boot_status_changed)
            self.auto_capture_service.resourceStatsUpdated.disconnect(self._on_resource_stats_updated)
            self.auto_capture_service.radioStatsUpdated.disconnect(self._on_radio_stats_updated)
            self.auto_capture_service.radioStreamOutput.disconnect(self._on_radio_stream_output)
            self.auto_capture_service.radioStateChanged.disconnect(self._on_radio_state_changed)
            self.auto_capture_service.messageGenerated.disconnect(self._on_message_generated)
        except Exception:
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
