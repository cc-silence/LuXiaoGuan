"""单路射频自动抓包控制卡片。

封装：
1. 射频标识、频段、芯片驱动、空闲/正在使用状态展示；
2. 允许接管并自动恢复复选框（面向非空闲射频）；
3. 跳频信道配置与监听网卡信息；
4. 实时指标看板（运行时长、PMKID、握手包数、.pcapng 原始文件大小）；
5. 独立流式终端输出控制台（默认折叠隐藏，按需展开）；
6. 专属启停控制按钮。
"""

import re

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from modules.capture_services.openwrt.models import OpenWrtRadioInfo
from modules.main_pages.page_action_buttons import create_page_text_button
from modules.ui_scale_manager import UiScaleManager


class AutoCaptureRadioCard(QFrame):
    """独立的单路射频控制卡片。"""

    startRequested = Signal(object, str, bool)  # radio_info, channels, allow_takeover
    stopRequested = Signal(str)                 # radio_name

    def __init__(self, radio_info: OpenWrtRadioInfo, parent=None):
        super().__init__(parent)
        self.radio_info = radio_info
        self.radio_name = radio_info.name
        self._light_theme = False
        self._capturing = False

        self.setObjectName("captureCard")
        self.setAttribute(Qt.WA_StyledBackground, True)
        from PySide6.QtWidgets import QSizePolicy
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)

        card_layout = QVBoxLayout(self)
        card_layout.setContentsMargins(14, 10, 14, 10)
        card_layout.setSpacing(6)

        scale = UiScaleManager.for_widget(self)

        # 1. 运行状态与硬件信息单行排布 (itemAt(0).layout())
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(8)
        header_row.setAlignment(Qt.AlignVCenter)

        # 状态放左边，标题字变大至 12pt 加粗
        self.status_badge = QLabel(self)
        self.status_badge.setFixedHeight(scale.px(26))
        badge_font = self.status_badge.font()
        badge_font.setPointSize(12)
        badge_font.setBold(True)
        self.status_badge.setFont(badge_font)
        header_row.addWidget(self.status_badge, 0, Qt.AlignVCenter)

        self.takeover_check = QCheckBox("允许接管并自动恢复", self)
        self.takeover_check.setObjectName("captureCheckBox")
        self.takeover_check.setFixedHeight(scale.px(26))
        self.takeover_check.setToolTip(
            "开启后，抓包期间将临时安全禁用该射频的 Wi-Fi 业务。\n"
            "抓包停止后，软件自动原子级回滚恢复原有 Wi-Fi 信号与业务配置。"
        )
        self.takeover_check.setChecked(False)
        header_row.addWidget(self.takeover_check, 0, Qt.AlignVCenter)

        from .radio_usage_view import apply_usage_status
        apply_usage_status(self)

        # 弹性空白：将右侧区域推至最右侧
        header_row.addStretch(1)

        # 跳频信道放到标题栏右面的文字左边，分割符割离，并与文字垂直居中对齐
        self.toggle_channel_btn = QPushButton("⚙ 跳频信道", self)
        self.toggle_channel_btn.setObjectName("captureSecondaryButton")
        self.toggle_channel_btn.setFixedHeight(scale.px(24))
        self.toggle_channel_btn.setStyleSheet("font-size: 12px; padding: 2px 8px; margin: 0px;")
        self.toggle_channel_btn.setToolTip("展开或收起自定义跳频信道输入框")
        self.toggle_channel_btn.clicked.connect(self._toggle_channel_config)
        header_row.addWidget(self.toggle_channel_btn, 0, Qt.AlignVCenter)

        self.ch_divider = QLabel("|", self)
        self.ch_divider.setStyleSheet("color: #64748b; font-weight: bold; padding: 0 2px;")
        self.ch_divider.setFixedHeight(scale.px(24))
        self.ch_divider.setAlignment(Qt.AlignCenter)
        header_row.addWidget(self.ch_divider, 0, Qt.AlignVCenter)

        # 硬件信息放右边，靠右对齐
        self.title_label = QLabel(self)
        self.title_label.setObjectName("captureGroupTitle")
        self.title_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.title_label.setFixedHeight(scale.px(24))
        band_str = "/".join(radio_info.bands) if radio_info.bands else "2.4G"
        phy_name = radio_info.interface or "phy"
        drv_str = f" [{radio_info.driver}]" if radio_info.driver else ""
        self.title_label.setText(f"📻 射频: {band_str} ({radio_info.name} / {phy_name}){drv_str}")
        header_row.addWidget(self.title_label, 0, Qt.AlignVCenter)

        # 分割符
        self.title_divider = QLabel("|", self)
        self.title_divider.setStyleSheet("color: #64748b; font-weight: bold; padding: 0 2px;")
        self.title_divider.setFixedHeight(scale.px(24))
        self.title_divider.setAlignment(Qt.AlignCenter)
        header_row.addWidget(self.title_divider, 0, Qt.AlignVCenter)

        # 网卡名称移到标题后面，右对齐
        idx_m = re.search(r"\d+", radio_info.name)
        idx_str = idx_m.group(0) if idx_m else "0"
        self.iface_label = QLabel(f"网卡: hcxmon{idx_str}", self)
        self.iface_label.setObjectName("captureDescription")
        self.iface_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.iface_label.setFixedHeight(scale.px(24))
        header_row.addWidget(self.iface_label, 0, Qt.AlignVCenter)

        card_layout.addLayout(header_row)

        # 2. 参数配置行：跳频信道输入（默认隐藏，高度固定 scale.px(30)）
        self.channel_row_widget = QWidget(self)
        self.channel_row_widget.setFixedHeight(scale.px(30))
        channel_row = QHBoxLayout(self.channel_row_widget)
        channel_row.setContentsMargins(0, 0, 0, 0)
        channel_row.setSpacing(8)

        ch_label = QLabel("跳频信道:", self.channel_row_widget)
        ch_label.setObjectName("captureFormLabel")
        scale.bind_metrics(ch_label, min_width=64)
        channel_row.addWidget(ch_label)

        self.channel_edit = QLineEdit(self.channel_row_widget)
        self.channel_edit.setObjectName("captureLineEdit")
        self.channel_edit.setPlaceholderText("留空则自动选用推荐跳频信道")
        if "5" in band_str:
            self.channel_edit.setText("36b,40b,44b,48b,149b,153b,157b,161b,165b")
        elif "6" in band_str:
            self.channel_edit.setText("5c,21c,37c,53c,69c,85c,101c,117c")
        else:
            self.channel_edit.setText("1a,2a,3a,4a,5a,6a,7a,8a,9a,10a,11a")
        channel_row.addWidget(self.channel_edit, 1)

        self.channel_row_widget.setVisible(False)
        card_layout.addWidget(self.channel_row_widget)

        # 3. 核心指标看板 (高度固定 scale.px(38))
        self.metrics_frame = QFrame(self)
        self.metrics_frame.setObjectName("captureCard")
        self.metrics_frame.setFixedHeight(scale.px(38))
        self._update_metrics_style()
        metrics_layout = QHBoxLayout(self.metrics_frame)
        metrics_layout.setContentsMargins(10, 4, 10, 4)
        metrics_layout.setSpacing(14)

        self.duration_label = QLabel("时长: 00:00:00", self.metrics_frame)
        self.duration_label.setObjectName("captureFormLabel")
        metrics_layout.addWidget(self.duration_label)

        self.pmkid_label = QLabel("PMKID: 0 个", self.metrics_frame)
        self.pmkid_label.setStyleSheet("color: #38bdf8; font-weight: bold;")
        metrics_layout.addWidget(self.pmkid_label)

        self.eapol_label = QLabel("握手包: 0 个", self.metrics_frame)
        self.eapol_label.setStyleSheet("color: #10b981; font-weight: bold;")
        metrics_layout.addWidget(self.eapol_label)

        self.size_label = QLabel("产物: 0 KB", self.metrics_frame)
        self.size_label.setObjectName("captureDescription")
        metrics_layout.addWidget(self.size_label)

        self.curr_channel_label = QLabel("当前信道: --", self.metrics_frame)
        self.curr_channel_label.setObjectName("captureDescription")
        metrics_layout.addWidget(self.curr_channel_label)
        metrics_layout.addStretch(1)
        card_layout.addWidget(self.metrics_frame)

        # 4. 控制按钮栏 (高度固定 scale.px(32)，监看终端最右侧右对齐，合并启停按钮)
        actions_row = QHBoxLayout()
        actions_row.setContentsMargins(0, 0, 0, 0)
        actions_row.setSpacing(10)

        # 主启停切换按钮（开始与停止合并）
        self.start_btn = create_page_text_button(self, "▶ 开始此路抓包", "启动此射频的前台流式抓包")
        self.start_btn.clicked.connect(self._on_toggle_capture_clicked)
        actions_row.addWidget(self.start_btn)

        # 兼容保留 stop_btn 引用
        self.stop_btn = create_page_text_button(self, "⏹ 停止此路", "停止此路抓包并自动回滚恢复 Wi-Fi")
        self.stop_btn.setEnabled(False)
        self.stop_btn.setVisible(False)
        self.stop_btn.clicked.connect(self._on_stop_clicked)
        actions_row.addWidget(self.stop_btn)

        # 弹性空白：将监看终端推至最右边右对齐
        actions_row.addStretch(1)

        self.toggle_console_btn = QPushButton("🖥 监看终端", self)
        self.toggle_console_btn.setObjectName("captureSecondaryButton")
        self.toggle_console_btn.setToolTip("展开或收起此前台射频抓包的实时流式终端窗口")
        self.toggle_console_btn.clicked.connect(self._toggle_console)
        actions_row.addWidget(self.toggle_console_btn)

        card_layout.addLayout(actions_row)

        # 5. 流式终端窗口容器 (默认折叠隐藏，右上角小工具按钮)
        self.console_widget = QWidget(self)
        console_widget_layout = QVBoxLayout(self.console_widget)
        console_widget_layout.setContentsMargins(0, 4, 0, 0)
        console_widget_layout.setSpacing(4)

        console_bar = QHBoxLayout()
        console_bar.setContentsMargins(0, 0, 0, 0)
        console_bar.setSpacing(6)
        console_title = QLabel("实时前台监看终端 (流式显示)", self.console_widget)
        console_title.setObjectName("captureDescription")
        console_bar.addWidget(console_title)
        console_bar.addStretch(1)

        tool_btn_style = (
            "QPushButton { padding: 2px 10px; min-height: 24px; border-radius: 4px; "
            "border: 1px solid rgba(148, 163, 184, 0.35); background: rgba(148, 163, 184, 0.08); color: #cbd5e1; } "
            "QPushButton:hover { border-color: #38bdf8; color: #38bdf8; background: rgba(56, 189, 248, 0.15); }"
        )

        self.autoscroll_check = QCheckBox("自动滚动", self.console_widget)
        self.autoscroll_check.setObjectName("captureCheckBox")
        self.autoscroll_check.setChecked(True)
        console_bar.addWidget(self.autoscroll_check)

        self.clear_btn = QPushButton("清屏", self.console_widget)
        self.clear_btn.setStyleSheet(tool_btn_style)
        self.clear_btn.clicked.connect(self._clear_console)
        console_bar.addWidget(self.clear_btn)

        # 终端高度调节小按钮
        self.zoom_in_btn = QPushButton("+ 放大", self.console_widget)
        self.zoom_in_btn.setStyleSheet(tool_btn_style)
        self.zoom_in_btn.setToolTip("增加终端高度")
        self.zoom_in_btn.clicked.connect(lambda: self._adjust_console_height(40))
        console_bar.addWidget(self.zoom_in_btn)

        self.zoom_out_btn = QPushButton("- 缩小", self.console_widget)
        self.zoom_out_btn.setStyleSheet(tool_btn_style)
        self.zoom_out_btn.setToolTip("减少终端高度")
        self.zoom_out_btn.clicked.connect(lambda: self._adjust_console_height(-40))
        console_bar.addWidget(self.zoom_out_btn)

        self.collapse_console_btn = QPushButton("收起", self.console_widget)
        self.collapse_console_btn.setStyleSheet(tool_btn_style)
        self.collapse_console_btn.setToolTip("收起实时监看终端")
        self.collapse_console_btn.clicked.connect(lambda: self.console_widget.setVisible(False))
        console_bar.addWidget(self.collapse_console_btn)

        console_widget_layout.addLayout(console_bar)

        self.console = QPlainTextEdit(self.console_widget)
        self.console.setReadOnly(True)
        self.console.setMaximumBlockCount(1500)
        font = QFont("Consolas", 9)
        font.setStyleHint(QFont.Monospace)
        self.console.setFont(font)
        self._update_console_style()
        self.console.setFixedHeight(140)
        console_widget_layout.addWidget(self.console)

        # 可垂直拖拽调整高度手柄
        from modules.main_pages.packet_capture.ui_helpers import VerticalResizeHandle
        self.resize_handle = VerticalResizeHandle(self.console, min_height=80, max_height=600, parent=self.console_widget)
        console_widget_layout.addWidget(self.resize_handle)

        self.console_widget.setVisible(False)
        card_layout.addWidget(self.console_widget)

    def _on_toggle_capture_clicked(self):
        """响应合并启停按钮点击。"""
        if self._capturing:
            self._on_stop_clicked()
        else:
            self._on_start_clicked()

    def _toggle_channel_config(self):
        """展开或收起跳频信道输入框。"""
        self.channel_row_widget.setVisible(not self.channel_row_widget.isVisible())

    def _adjust_console_height(self, delta: int):
        """调整前台监看终端高度。"""
        new_h = max(80, min(600, self.console.height() + delta))
        self.console.setFixedHeight(new_h)

    def _toggle_console(self):
        """展开或收起流式终端。"""
        self.console_widget.setVisible(not self.console_widget.isVisible())

    def set_light_theme(self, enabled: bool):
        """亮暗主题切换。"""
        self._light_theme = bool(enabled)
        self.start_btn.set_light_theme(self._light_theme)
        self.stop_btn.set_light_theme(self._light_theme)
        self._update_console_style()
        self._update_metrics_style()

    def _update_metrics_style(self):
        if hasattr(self, "metrics_frame") and self.metrics_frame:
            if self._light_theme:
                self.metrics_frame.setStyleSheet(
                    "background: #f1f5f9; border: 1px solid #cbd5e1; border-radius: 6px;"
                )
            else:
                self.metrics_frame.setStyleSheet(
                    "background: rgba(0, 0, 0, 0.25); border: 1px solid rgba(255, 255, 255, 0.10); border-radius: 6px;"
                )

    def _update_console_style(self):
        if self._light_theme:
            self.console.setStyleSheet(
                "QPlainTextEdit { background-color: #f8fafc; color: #0f172a; "
                "border: 1px solid #cbd5e1; border-radius: 5px; selection-background-color: #3b82f6; }"
            )
        else:
            self.console.setStyleSheet(
                "QPlainTextEdit { background-color: #0b0f14; color: #34d399; "
                "border: 1px solid #232b38; border-radius: 5px; selection-background-color: #1e3a8a; }"
            )

    def append_stream_text(self, text: str):
        """追加流式文本到终端，自动清洗控制字符并合并连续空行。"""
        if not text:
            return

        # 1. 清洗控制符与冗余 ANSI 转义序列
        cleaned = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", text)
        cleaned = re.sub(r"\x1b\([a-zA-Z0-9]", "", cleaned)

        # 2. 规范化换行并合并连续空行 (去除纯空白行与多个换行堆叠)
        cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
        cleaned = re.sub(r"\n[ \t]*\n+", "\n", cleaned)

        # 3. 如果当前终端末尾已有换行且新文本开头也是换行，消除拼接处产生的冗余空行
        doc = self.console.document()
        if doc.characterCount() > 1:
            last_char = doc.characterAt(doc.characterCount() - 2)
            if last_char == "\n" and cleaned.startswith("\n"):
                cleaned = cleaned.lstrip("\n")

        if not cleaned:
            return

        cursor = self.console.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(cleaned)

        if self.autoscroll_check.isChecked():
            self.console.moveCursor(QTextCursor.End)

    def apply_stats(self, stats: dict):
        """更新指标看板。"""
        secs = stats.get("duration_seconds", 0)
        h = secs // 3600
        m = (secs % 3600) // 60
        s = secs % 60
        self.duration_label.setText(f"时长: {h:02d}:{m:02d}:{s:02d}")

        pmkids = stats.get("pmkid_count", 0)
        self.pmkid_label.setText(f"PMKID: {pmkids} 个")

        eapols = stats.get("eapol_count", 0)
        self.eapol_label.setText(f"握手包: {eapols} 个")

        sz = stats.get("file_size_bytes", 0)
        if sz < 1024 * 1024:
            sz_str = f"{sz / 1024:.1f} KB"
        else:
            sz_str = f"{sz / (1024 * 1024):.2f} MB"
        self.size_label.setText(f"产物: {sz_str}")

        ch = stats.get("current_channel")
        if ch:
            self.curr_channel_label.setText(f"当前信道: {ch}")

    def apply_state(self, state: str, error_message: str = ""):
        """更新抓包状态与按钮使能，使用高区分度色彩与大号字体。"""
        self._capturing = state in {"starting", "capturing"}
        self.stop_btn.setEnabled(self._capturing)
        self.channel_edit.setEnabled(not self._capturing)
        self.takeover_check.setEnabled(not self._capturing)

        if state == "starting":
            self.start_btn.setEnabled(False)
            self.start_btn.setText("⏳ 正在启动...")
            self.status_badge.setText("【正在启动...】")
            self.status_badge.setStyleSheet("color: #f59e0b; font-weight: bold; font-size: 12pt;")
        elif state == "capturing":
            self.start_btn.setEnabled(True)
            self.start_btn.setText("⏹ 停止此路抓包")
            self.status_badge.setText("【正在前台抓包】")
            self.status_badge.setStyleSheet("color: #10b981; font-weight: bold; font-size: 12pt;")
        elif state == "stopping":
            self.start_btn.setEnabled(False)
            self.start_btn.setText("⏳ 正在停止...")
            self.status_badge.setText("【正在停止并恢复...】")
            self.status_badge.setStyleSheet("color: #f97316; font-weight: bold; font-size: 12pt;")
        elif state == "error":
            self.start_btn.setEnabled(True)
            self.start_btn.setText("▶ 开始此路抓包")
            err_text = f"【启动失败】{error_message}" if error_message else "【启动失败/异常】"
            self.status_badge.setText(err_text)
            self.status_badge.setStyleSheet("color: #ef4444; font-weight: bold; font-size: 12pt;")
        else:
            self.start_btn.setEnabled(True)
            self.start_btn.setText("▶ 开始此路抓包")
            self.curr_channel_label.setText("当前信道: --")
            from .radio_usage_view import apply_usage_status
            apply_usage_status(self)
            # 保证空闲/在用等状态依然维持 12pt 大号字体
            badge_font = self.status_badge.font()
            badge_font.setPointSize(12)
            badge_font.setBold(True)
            self.status_badge.setFont(badge_font)

    def refresh_status(self, session=None, latest_radio_info=None):
        """刷新卡片状态与回显指标。"""
        if latest_radio_info is not None:
            self.radio_info = latest_radio_info
            band_str = "/".join(latest_radio_info.bands) if latest_radio_info.bands else "2.4G"
            phy_name = latest_radio_info.interface or "phy"
            drv_str = f" [{latest_radio_info.driver}]" if latest_radio_info.driver else ""
            self.title_label.setText(f"📻 射频: {band_str} ({latest_radio_info.name} / {phy_name}){drv_str}")

        if session is not None:
            self.apply_state(session.stats.state, session.stats.error_message)
            self.apply_stats(session._build_stats_dict())
        else:
            self.apply_state("idle")

    def _clear_console(self):
        self.console.clear()

    def _on_start_clicked(self):
        channels = self.channel_edit.text().strip()
        allow_takeover = self.takeover_check.isChecked()

        self.startRequested.emit(self.radio_info, channels, allow_takeover)

    def _on_stop_clicked(self):
        self.append_stream_text("\n⏹ [提示] 正在停止此路抓包...\n")
        self.stopRequested.emit(self.radio_name)
