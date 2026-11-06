"""离线抓包成果与智能健康诊断报告弹窗。

针对脱机自主抓包场景拉取的抓包会话日志与握手成果进行智能解析展示：
1. 顶部健康状态与运行诊断横幅；
2. 捕获统计核心指标（PMKID、EAPOL 握手包、识别热点数、生成的 .pcapng 原始文件）；
3. AP 热点明细表格（BSSID、SSID、信道、握手帧构成、捕获状态）；
4. 原始会话诊断文本分析日志浏览器；
5. 一键打开输出目录。
"""

import os
from pathlib import Path
import re
from typing import Optional

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QBrush, QColor, QDesktopServices
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from modules.capture_services.openwrt.auto_capture_log_analyzer import AutoCaptureDiagnosticReport
from modules.common_widgets.common_dialogs import _create_dialog_button
from modules.common_widgets.frameless_dialog import FramelessDialog
from modules.config_manager import ConfigManager
from modules.dialog_theme import build_base_stylesheet, detect_light_theme
from modules.ui_scale_manager import UiScaleManager


class AutoCaptureReportDialog(FramelessDialog):
    """离线成果拉取与智能诊断分析报告弹窗。"""

    def __init__(self, report: AutoCaptureDiagnosticReport, local_dir: str, parent=None, *, live=False):
        super().__init__("离线抓包成果与智能健康诊断报告", parent, inherit_parent_stylesheet=False)
        self._live = live
        if live:
            self.setWindowTitle("前台抓包明细")
            self.title_label.setText("前台抓包明细")
        self.light_theme = self._detect_theme(parent)
        scale = UiScaleManager.for_widget(self)
        base_stylesheet = build_base_stylesheet(ConfigManager(), light_theme=self.light_theme, scale_manager=scale)
        dialog_extra_qss = self._build_report_dialog_stylesheet(self.light_theme)
        self.setStyleSheet(f"{base_stylesheet}\n\n{dialog_extra_qss}")
        self.set_scaled_size((800, 550), (1020, 720))

        if not live:
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
            self._title_label = title_lbl
            header_layout.addWidget(title_lbl)

            status_detail = QLabel(
                f"退出原因: {report.exit_reason} (退出代码: {report.exit_code if report.exit_code is not None else '未记录'})\n"
                f"路由器固件: {report.firmware_info or '未知'} | 工具版本: {report.hcx_version or '未知'} (方言: {report.dialect_mode or 'auto'})\n"
                f"BPF 过滤: {report.bpf_status}",
                header_card,
            )
            status_detail.setObjectName("reportDetailLabel")
            status_detail.setWordWrap(True)
            self._status_detail = status_detail
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
        self._pmkid_label = pmkid_lbl
        metrics_row.addWidget(pmkid_lbl)

        eapol_color = "#16a34a" if self.light_theme else "#10b981"
        eapol_lbl = QLabel(f"捕获握手包 (EAPOL): {report.eapol_count} 个", self)
        eapol_lbl.setStyleSheet(f"color: {eapol_color}; font-weight: bold; font-size: 13px;")
        self._eapol_label = eapol_lbl
        metrics_row.addWidget(eapol_lbl)

        ap_lbl = QLabel(f"识别周边 AP 热点: {len(report.targets)} 个", self)
        ap_lbl.setObjectName("reportMetricsText")
        self._ap_label = ap_lbl
        metrics_row.addWidget(ap_lbl)

        pcap_count = len(report.captured_files)
        pcap_lbl = QLabel(f"生成的 .pcapng 原始包: {pcap_count} 个", self)
        pcap_lbl.setObjectName("reportMetricsText")
        pcap_lbl.setVisible(not live)
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
        if live:
            ap_table.setEditTriggers(QTableWidget.NoEditTriggers)
        ap_table.setColumnCount(5)
        ap_table.setHorizontalHeaderLabels(["BSSID (MAC)", "SSID (Wi-Fi名称)", "信道", "握手帧构成", "捕获状态"])
        self.ap_table = ap_table
        self._target_rows = None
        self._set_targets(report.targets)
        ap_table.setColumnWidth(0, scale.px(145))
        ap_table.setColumnWidth(1, scale.px(210))
        ap_table.setColumnWidth(2, scale.px(65))
        ap_table.setColumnWidth(3, scale.px(210))
        ap_table.horizontalHeader().setStretchLastSection(True)
        ap_layout.addWidget(ap_table)
        tabs.addTab(ap_widget, f"AP 热点列表 ({len(report.targets)})")

        if not live:
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
                    "1. 若路由器刚完成脱机抓包，请先点击【📥 一键拉取握手包】将成果文件同步到电脑；\n"
                    "2. 同步完成后再次点击【📊 智能分析日志】即可查看完整的抓包分析与热点诊断；\n"
                    f"3. 您也可以点击下方的“📂 打开输出目录”直接查看本地目录 ({local_dir})。"
                )
            log_view.setPlainText(summary_text)
            log_layout.addWidget(log_view)
            tabs.addTab(log_widget, "诊断摘要文本")

        self._tabs = tabs
        self.content_layout.addWidget(tabs, 1)

        # 底部操作栏
        dir_lbl = QLabel(f"输出目录: {local_dir}", self)
        dir_lbl.setObjectName("settingsHint")
        self.button_layout.addWidget(dir_lbl)
        self.button_layout.addStretch(1)

        open_btn = _create_dialog_button("📂 打开输出目录", self, "settingsSecondaryButton")
        open_btn.clicked.connect(lambda: self._open_dir(local_dir))
        self.button_layout.addWidget(open_btn)

        close_btn = _create_dialog_button("确定", self, "settingsPrimaryButton")
        close_btn.clicked.connect(self.accept)
        self.button_layout.addWidget(close_btn)
        if live:
            self.update_live_report(report)

    def _set_targets(self, targets):
        rows = tuple((row.bssid, row.ssid, row.channel, row.handshake_frames,
                      row.has_pmkid, row.has_eapol) for row in targets)
        if rows == self._target_rows:
            return
        previous_rows = self._target_rows or ()
        self._target_rows = rows
        ap_table = self.ap_table
        ap_table.setRowCount(len(targets))
        success_color = QColor("#16a34a") if self.light_theme else QColor("#4ade80")
        warning_color = QColor("#d97706") if self.light_theme else QColor("#fbbf24")
        muted_color = QColor("#64748b") if self.light_theme else QColor("#94a3b8")
        for row, target in enumerate(targets):
            if row < len(previous_rows) and rows[row] == previous_rows[row]:
                continue
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

            if self._live and target.has_eapol:
                frame_text = target.handshake_frames or "已检测到握手消息"
                frame_color = warning_color

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

    def update_live_report(self, report):
        if not self._live:
            return
        self._pmkid_label.setText(f"捕获 PMKID: {report.pmkid_count} 个")
        self._eapol_label.setText(f"捕获握手消息: {report.eapol_count} 个")
        self._ap_label.setText(f"已捕获热点: {len(report.targets)} 个")
        self._set_targets(report.targets)
        self._tabs.setTabText(0, f"AP 热点列表 ({len(report.targets)})")

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
            p = Path(dir_path)
            if not p.exists():
                p.mkdir(parents=True, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(p.resolve())))
        except Exception:
            pass

