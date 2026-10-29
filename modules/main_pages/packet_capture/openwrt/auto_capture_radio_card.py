"""单路射频自动抓包控制卡片。

封装：
1. 射频标识、频段、芯片驱动、空闲/正在使用状态展示；
2. 允许接管并自动恢复复选框（面向非空闲射频）；
3. 跳频信道配置与监听网卡信息；
4. 实时指标看板（运行时长、PMKID、握手包数、.pcapng 原始文件大小）；
5. 独立流式终端输出控制台（锁屏滚动、清屏、限制行数）；
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
from modules.common_widgets.common_dialogs import ConfirmDialog
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

        card_layout = QVBoxLayout(self)
        card_layout.setContentsMargins(14, 12, 14, 12)
        card_layout.setSpacing(8)

        # 1. 运行状态与硬件信息单行排布 (状态放左边，硬件信息放右边靠右对齐)
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(10)

        # 状态放左边
        is_free = bool(radio_info.free)
        self.status_badge = QLabel(self)
        if is_free:
            self.status_badge.setText("【空闲】(安全无感)")
            self.status_badge.setStyleSheet("color: #10b981; font-weight: bold;")
        else:
            self.status_badge.setText("【正在使用】(发射 Wi-Fi 中)")
            self.status_badge.setStyleSheet("color: #f59e0b; font-weight: bold;")
        header_row.addWidget(self.status_badge)

        self.takeover_check = QCheckBox("允许接管并自动恢复", self)
        self.takeover_check.setObjectName("captureCheckBox")
        self.takeover_check.setToolTip(
            "开启后，抓包期间将临时安全禁用该射频的 Wi-Fi 业务。\n"
            "抓包停止后，软件自动原子级回滚恢复原有 Wi-Fi 信号与业务配置。"
        )
        self.takeover_check.setChecked(False)
        self.takeover_check.setVisible(not is_free)
        header_row.addWidget(self.takeover_check)

        # 弹性空白：将硬件信息推至最右侧
        header_row.addStretch(1)

        # 硬件信息放右边，靠右对齐
        self.title_label = QLabel(self)
        self.title_label.setObjectName("captureGroupTitle")
        self.title_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        band_str = "/".join(radio_info.bands) if radio_info.bands else "2.4G"
        phy_name = radio_info.interface or "phy"
        drv_str = f" [{radio_info.driver}]" if radio_info.driver else ""
        self.title_label.setText(f"📻 射频: {band_str} ({radio_info.name} / {phy_name}){drv_str}")
        header_row.addWidget(self.title_label)

        card_layout.addLayout(header_row)

        # 3. 参数配置行：跳频信道输入
        channel_row = QHBoxLayout()
        channel_row.setContentsMargins(0, 0, 0, 0)
        channel_row.setSpacing(8)

        ch_label = QLabel("跳频信道:", self)
        ch_label.setObjectName("captureFormLabel")
        UiScaleManager.for_widget(self).bind_metrics(ch_label, min_width=64)
        channel_row.addWidget(ch_label)

        self.channel_edit = QLineEdit(self)
        self.channel_edit.setObjectName("captureLineEdit")
        self.channel_edit.setPlaceholderText("留空则自动选用推荐跳频信道")
        # 默认推荐信道
        if "5" in band_str:
            self.channel_edit.setText("36b,40b,44b,48b,149b,153b,157b,161b,165b")
        elif "6" in band_str:
            self.channel_edit.setText("5c,21c,37c,53c,69c,85c,101c,117c")
        else:
            self.channel_edit.setText("1a,2a,3a,4a,5a,6a,7a,8a,9a,10a,11a")
        channel_row.addWidget(self.channel_edit, 1)

        idx_m = re.search(r"\d+", radio_info.name)
        idx_str = idx_m.group(0) if idx_m else "0"
        self.iface_label = QLabel(f"网卡: hcxmon{idx_str}", self)
        self.iface_label.setObjectName("captureDescription")
        channel_row.addWidget(self.iface_label)
        card_layout.addLayout(channel_row)

        # 4. 指标看板
        self.metrics_frame = QFrame(self)
        self.metrics_frame.setObjectName("captureCard")
        self._update_metrics_style()
        metrics_layout = QHBoxLayout(self.metrics_frame)
        metrics_layout.setContentsMargins(10, 6, 10, 6)
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

        # 5. 流式终端窗口
        console_bar = QHBoxLayout()
        console_bar.setContentsMargins(0, 0, 0, 0)
        console_bar.setSpacing(8)
        console_title = QLabel("实时前台监看终端 (流式显示)", self)
        console_title.setObjectName("captureDescription")
        console_bar.addWidget(console_title)
        console_bar.addStretch(1)

        self.autoscroll_check = QCheckBox("锁屏滚动", self)
        self.autoscroll_check.setObjectName("captureCheckBox")
        self.autoscroll_check.setChecked(True)
        console_bar.addWidget(self.autoscroll_check)

        self.clear_btn = QPushButton("清屏", self)
        self.clear_btn.setObjectName("captureSecondaryButton")
        self.clear_btn.clicked.connect(self._clear_console)
        console_bar.addWidget(self.clear_btn)
        card_layout.addLayout(console_bar)

        self.console = QPlainTextEdit(self)
        self.console.setReadOnly(True)
        self.console.setMaximumBlockCount(1500)  # 有界缓存，杜绝无界增长
        font = QFont("Consolas", 9)
        font.setStyleHint(QFont.Monospace)
        self.console.setFont(font)
        self._update_console_style()
        self.console.setMinimumHeight(140)
        card_layout.addWidget(self.console, 1)

        # 6. 控制按钮栏
        actions_row = QHBoxLayout()
        actions_row.setContentsMargins(0, 0, 0, 0)
        actions_row.setSpacing(10)

        self.start_btn = create_page_text_button(self, "▶ 开始此路抓包", "启动此射频的前台流式抓包")
        self.start_btn.clicked.connect(self._on_start_clicked)
        actions_row.addWidget(self.start_btn)

        self.stop_btn = create_page_text_button(self, "⏹ 停止此路", "停止此路抓包并自动回滚恢复 Wi-Fi")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._on_stop_clicked)
        actions_row.addWidget(self.stop_btn)

        actions_row.addStretch(1)
        card_layout.addLayout(actions_row)

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
        """更新抓包状态与按钮使能。"""
        self._capturing = state in {"starting", "capturing"}
        self.start_btn.setEnabled(state in {"idle", "error"})
        self.stop_btn.setEnabled(self._capturing)
        self.channel_edit.setEnabled(not self._capturing)
        self.takeover_check.setEnabled(not self._capturing)

        if state == "starting":
            self.status_badge.setText("【正在启动...】")
            self.status_badge.setStyleSheet("color: #38bdf8; font-weight: bold;")
        elif state == "capturing":
            self.status_badge.setText("【正在前台抓包】")
            self.status_badge.setStyleSheet("color: #10b981; font-weight: bold;")
        elif state == "stopping":
            self.status_badge.setText("【正在停止并恢复...】")
            self.status_badge.setStyleSheet("color: #f59e0b; font-weight: bold;")
        elif state == "error":
            err_text = f"【启动失败】{error_message}" if error_message else "【启动失败/异常】"
            self.status_badge.setText(err_text)
            self.status_badge.setStyleSheet("color: #ef4444; font-weight: bold;")
        else:
            self.curr_channel_label.setText("当前信道: --")
            is_free = bool(self.radio_info.free)
            self.takeover_check.setVisible(not is_free)
            if is_free:
                self.status_badge.setText("【空闲】(安全无感)")
                self.status_badge.setStyleSheet("color: #10b981; font-weight: bold;")
            else:
                self.status_badge.setText("【正在使用】(业务已在线)")
                self.status_badge.setStyleSheet("color: #f59e0b; font-weight: bold;")

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

        is_free = bool(self.radio_info.free)
        if not is_free and not allow_takeover:
            bands_str = "/".join(self.radio_info.bands) if self.radio_info.bands else "Wi-Fi"
            msg = (
                f"射频 {self.radio_name} ({bands_str}) 当前正在发射 Wi-Fi 业务。\n\n"
                "抓包需要临时安全接管该射频以切换跳频信道。\n"
                "停止抓包后，软件将自动原子级回滚恢复原有 Wi-Fi 信号。\n\n"
                "是否允许接管并立即开始抓包？"
            )
            dialog = ConfirmDialog("无线射频接管确认", msg, self)
            if dialog.exec() == ConfirmDialog.Accepted:
                self.takeover_check.setChecked(True)
                allow_takeover = True
            else:
                self.append_stream_text("\n⚠ [提示] 用户取消操作：未授权接管正在发射 Wi-Fi 的无线射频。\n")
                return

        self.startRequested.emit(self.radio_info, channels, allow_takeover)

    def _on_stop_clicked(self):
        self.append_stream_text("\n⏹ [提示] 正在停止此路抓包...\n")
        self.stopRequested.emit(self.radio_name)
