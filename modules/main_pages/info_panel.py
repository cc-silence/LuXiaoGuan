"""共享信息面板组件。

为 SoftwareDownloadPage 和 SoftwareUpdatePage 提供统一的信息展示面板，
包含只读 QPlainTextEdit 和文字缩放控件。

主要作用：消除下载页和更新页之间信息面板的重复布局和样式代码。
"""

from collections import deque

from PySide6.QtCore import Qt, QSize, QTimer, QSignalBlocker, Signal
from PySide6.QtGui import QAction, QColor, QFont, QTextCharFormat, QTextCursor, QPalette
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QPlainTextEdit,
    QToolButton,
    QVBoxLayout,
)

from modules.display_privacy import controller_for, Masker
from modules.log_services import DiagnosticReferences, MAX_INFO_BYTES
from modules.dialog_theme import create_themed_menu
from modules.ui_scale_manager import UiScaleManager
from modules.icon_utils import get_theme_icon, get_visibility_icon
from modules.common_widgets.auto_hide_scroll_bar import AutoHideScrollBar


class InfoPanel(QFrame):
    """共享信息面板，包含只读文本框 + 文字缩放按钮。

    主要作用：统一下载页和更新页的右侧信息面板布局与行为。
    调用方式：页面在 _build_ui() 中创建实例，添加到右侧布局。
    参数：
        parent: 父控件
        placeholder_text: 信息框占位文本
        default_text: 信息框初始文本
        zoom_out_tooltip: 缩小按钮的 tooltip
        zoom_in_tooltip: 放大按钮的 tooltip
        light_theme: 初始亮色主题状态
        scale_manager: 缩放管理器（可选）
    """

    fontDeltaChanged = Signal(int)

    def __init__(self, parent=None, placeholder_text="", default_text="",
                 zoom_out_tooltip="缩小文字", zoom_in_tooltip="放大文字",
                 light_theme=False, scale_manager=None,
                 initial_font_delta=0, save_font_delta_callback=None,
                 floating_zoom_controls=False, color_success_messages=False,
                 log_category="application", diagnostic_references=None, export_context_provider=None):
        super().__init__(parent)
        self.setObjectName("downloadInfoPanel")
        self._light_theme = light_theme
        self._scale_manager = scale_manager
        self._save_font_delta_callback = save_font_delta_callback
        self._info_font_delta = initial_font_delta
        self._floating_zoom_controls = bool(floating_zoom_controls)
        self._color_success_messages = bool(color_success_messages)
        self._content_hidden = False
        self._privacy = controller_for(self)
        self._local_masker = None
        self._entries = deque()
        self._entry_sizes = deque()
        self._entry_bytes = 0
        self._render_has_entry = False
        self._log_category = str(log_category)
        self._export_context_provider = export_context_provider
        self._diagnostic_provider = diagnostic_references
        self._diagnostic_references = DiagnosticReferences()
        self._export_service = None


        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 只读信息文本框，所有 append_info 写入的内容都会显示在这里
        self.info_box = QPlainTextEdit(self)
        self.info_box.setObjectName("downloadInfoBox")
        self.info_box.setAttribute(Qt.WA_StyledBackground, True)
        self.info_box.setReadOnly(True)
        self.info_box.setUndoRedoEnabled(False)
        self._info_interaction_flags = self.info_box.textInteractionFlags()
        self.info_box.setVerticalScrollBar(AutoHideScrollBar(Qt.Vertical, self.info_box, inset_track=True))
        # 抓包信息按窗口宽度自动换行，横向滚动条没有实际用途，因此不创建也不预留槽位。
        # 下载和更新页继续使用原有横向自动隐藏滚动条，避免改变既有日志布局。
        if self._floating_zoom_controls:
            self.info_box.setLineWrapMode(QPlainTextEdit.WidgetWidth)
            self.info_box.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        else:
            self.info_box.setHorizontalScrollBar(AutoHideScrollBar(Qt.Horizontal, self.info_box))
        self.info_box.setProperty("floatingZoomControls", self._floating_zoom_controls)
        self.info_box.setPlaceholderText(placeholder_text)
        self.info_box.setPlainText("")
        layout.addWidget(self.info_box, 1)

        # 抓包页可使用悬浮小面板，避免底部整行工具栏压缩信息文本；下载和更新页
        # 默认继续使用原来的底部工具行，保持既有界面不变。
        # 悬浮控件直接挂在信息框本体，而不是 viewport。QPlainTextEdit 的 viewport 会因
        # 8px 文本内边距向内收缩，挂在 viewport 上会在控件下方留下明显空隙。
        zoom_parent = self.info_box if self._floating_zoom_controls else self

        self.zoom_out_btn = _create_zoom_button(
            zoom_parent, ":/icons/images/icons/cil-zoom-out.png", zoom_out_tooltip,
            self._light_theme,
        )
        self.zoom_in_btn = _create_zoom_button(
            zoom_parent, ":/icons/images/icons/cil-zoom-in.png", zoom_in_tooltip,
            self._light_theme,
        )
        self.export_btn = _create_zoom_button(
            zoom_parent, ":/icons/images/icons/cil-data-transfer-down.png",
            "\u5bfc\u51fa\u65e5\u5fd7\uff08\u59cb\u7ec8\u8131\u654f\uff09", self._light_theme,
        )
        self.export_btn.clicked.connect(self._export_logs)
        self.visibility_btn = None

        if self._floating_zoom_controls:
            self.zoom_overlay = QFrame(self.info_box)
            self.zoom_overlay.setObjectName("downloadInfoZoomOverlay")
            self.zoom_overlay.setAttribute(Qt.WA_StyledBackground, True)
            self.zoom_out_btn.setParent(self.zoom_overlay)
            self.zoom_in_btn.setParent(self.zoom_overlay)
            zoom_row = QHBoxLayout(self.zoom_overlay)
            zoom_row.setContentsMargins(3, 1, 3, 1)
            zoom_row.setSpacing(1)
            self.zoom_out_btn.setFixedSize(26, 26)
            self.zoom_in_btn.setFixedSize(26, 26)
            self.zoom_out_btn.setObjectName("downloadInfoZoomButton")
            self.zoom_in_btn.setObjectName("downloadInfoZoomButton")
            self.zoom_out_btn.setIconSize(QSize(15, 15))
            self.zoom_in_btn.setIconSize(QSize(15, 15))
            zoom_row.addWidget(self.zoom_out_btn)
            zoom_row.addWidget(self.zoom_in_btn)
            self.visibility_btn = QToolButton(self.zoom_overlay)
            self.visibility_btn.setObjectName("downloadInfoZoomButton")
            self.visibility_btn.setFixedSize(26, 26)
            self.visibility_btn.setIconSize(QSize(18, 18))
            self.visibility_btn.setCursor(Qt.PointingHandCursor)
            self.visibility_btn.setCheckable(True)
            self.visibility_btn.toggled.connect(self.set_content_hidden)
            zoom_row.addWidget(self.visibility_btn)
            self.export_btn.setParent(self.zoom_overlay)
            self.export_btn.setObjectName("downloadInfoZoomButton")
            zoom_row.addWidget(self.export_btn)
            self._refresh_visibility_icon()
            self.zoom_overlay.adjustSize()
            self.zoom_overlay.raise_()
            QTimer.singleShot(0, self._position_zoom_overlay)
        else:
            self.zoom_overlay = None
            zoom_row = QHBoxLayout()
            zoom_row.setContentsMargins(8, 4, 8, 6)
            zoom_row.setSpacing(4)
            zoom_row.addStretch(1)
            zoom_row.addWidget(self.zoom_out_btn)
            zoom_row.addWidget(self.zoom_in_btn)
            zoom_row.addWidget(self.export_btn)
            layout.addLayout(zoom_row)

        self.zoom_out_btn.clicked.connect(lambda: self._change_font_size(-1))
        self.zoom_in_btn.clicked.connect(lambda: self._change_font_size(1))
        self.info_box.setContextMenuPolicy(Qt.CustomContextMenu)
        self.info_box.customContextMenuRequested.connect(self._show_context_menu)
        # 长任务逐目标播报会无限堆行；保留最近 2000 行，超出时自动丢弃最旧的。
        self.info_box.setMaximumBlockCount(2000)

        self._apply_font()
        if self._privacy is not None:
            self._privacy.changed.connect(self._refresh_privacy)
        if default_text:
            self.append_info(default_text)

    def display_text(self, text):
        """\u6839\u636e\u5168\u5c40\u548c\u5c40\u90e8\u5f00\u5173\u6295\u5f71\u6587\u672c\uff0c\u539f\u6587\u4fdd\u6301\u4e0d\u53d8\u3002"""
        if self._privacy and self._privacy.enabled:
            return self._privacy.text(text)
        return self._local_masker.text(text) if self._local_masker else str(text)

    def set_local_masker(self, masker):
        """\u5c40\u90e8\u663e\u793a\u5f00\u5173\u4e0d\u6539\u5199\u539f\u59cb\u65e5\u5fd7\u3002"""
        if masker == self._local_masker:
            return
        self._local_masker = masker
        if not (self._privacy and self._privacy.enabled):
            self._refresh_privacy()

    def append_info(self, text, level="info"):
        """\u6309\u884c\u4fdd\u7559\u6700\u8fd1\u65e5\u5fd7\uff0c\u884c\u6570\u548c\u5b57\u8282\u53cc\u9650\u5236\u3002"""
        text = str(text)
        self._register_session_reference(text)
        trimmed = False
        retained = []
        for line in text.split('\n'):
            raw = line.encode('utf-8')
            if len(raw) > MAX_INFO_BYTES:
                line = '[truncated] ' + raw[-(MAX_INFO_BYTES - 32):].decode('utf-8', errors='ignore')
                raw = line.encode('utf-8')
            self._entries.append((line, str(level)))
            self._entry_sizes.append(len(raw) + 1)
            self._entry_bytes += len(raw) + 1
            retained.append(line)
            while len(self._entries) > 2000 or self._entry_bytes > MAX_INFO_BYTES:
                self._entries.popleft()
                self._entry_bytes -= self._entry_sizes.popleft()
                trimmed = True
        if trimmed:
            self._refresh_privacy()
        else:
            self._append_rendered('\n'.join(retained), level)

    def export_entries(self):
        return tuple(self._entries)

    def add_diagnostic_reference(self, path, root=None, kind='text'):
        self._diagnostic_references.add(path, root, kind)

    def _register_session_reference(self, text):
        # A named failure session is an explicit reference, never a root-wide scan.
        import re
        from pathlib import Path
        for match in re.finditer(r'([A-Za-z]:[\\/][^\r\n]*?capture-work[\\/][0-9a-f-]{32,36})(?![0-9a-f-])', text, re.I):
            path = Path(match.group(1))
            self.add_diagnostic_reference(path, path.parent, 'session')

    def _refresh_privacy(self):
        bar = self.info_box.verticalScrollBar()
        position, at_end = bar.value(), bar.value() == bar.maximum()
        self.info_box.setUpdatesEnabled(False)
        try:
            self.info_box.clear()
            self._render_has_entry = False
            for text, level in self._entries:
                self._append_rendered(text, level)
            bar.setValue(bar.maximum() if at_end else position)
        finally:
            self.info_box.setUpdatesEnabled(True)

    def _export_logs(self):
        from PySide6.QtWidgets import QFileDialog
        from modules.common_widgets.common_dialogs import MessageDialog
        from modules.config_manager import user_downloads_dir
        from modules.log_services import LogExportService, build_export_filename
        owner = self.window()
        runtime = getattr(owner, 'runtime', None)
        if runtime is not None:
            if runtime._exiting:
                return
            service = runtime.log_export_service
        else:
            service = getattr(owner, 'log_export_service', None)
            if service is None:
                service = LogExportService(owner)
                owner.log_export_service = service
        if service.active:
            MessageDialog('\u5bfc\u51fa\u65e5\u5fd7', '\u5df2\u6709\u65e5\u5fd7\u6b63\u5728\u5bfc\u51fa\uff0c\u8bf7\u7b49\u5f85\u5b8c\u6210\u3002', owner).exec()
            return
        picked, _ = QFileDialog.getSaveFileName(owner, '\u5bfc\u51fa\u65e5\u5fd7',
            str(user_downloads_dir() / build_export_filename(self._log_category)), 'ZIP (*.zip)')
        if not picked:
            return
        if callable(self._diagnostic_provider):
            for ref in self._diagnostic_provider():
                self.add_diagnostic_reference(ref.path, ref.root, ref.kind)
        references, omitted = self._diagnostic_references.snapshot()
        masker=self._privacy.snapshot() if self._privacy else Masker()
        try:
            router_context=self._export_context_provider(masker) if self._export_context_provider else ""
        except Exception as exc:
            MessageDialog("导出日志", "无法获取当前路由器诊断信息："+str(exc), owner).exec()
            return
        self._export_service = service
        service.completed.connect(self._export_completed)
        service.failed.connect(self._export_failed)
        service.activityChanged.connect(self._export_activity_changed)
        self.export_btn.setEnabled(False)
        version_label = getattr(getattr(owner, 'ui', None), 'version', None)
        from PySide6.QtWidgets import QApplication
        version = QApplication.instance().applicationVersion()
        if not version and version_label is not None:
            version = version_label.text().lstrip('v')
        started = service.start(destination=picked, category=self._log_category,
                      entries=self.export_entries(),
                      masker=masker, router_context=router_context,
                      references=references, omitted=omitted, app_version=version)
        if not started:
            service.completed.disconnect(self._export_completed)
            service.failed.disconnect(self._export_failed)
            service.activityChanged.disconnect(self._export_activity_changed)
            self._export_service = None
            self.export_btn.setEnabled(True)
            MessageDialog('\u5bfc\u51fa\u65e5\u5fd7', '\u53e6\u4e00\u4e2a\u5bfc\u51fa\u4efb\u52a1\u5df2\u5f00\u59cb\uff0c\u8bf7\u7b49\u5f85\u5b8c\u6210\u540e\u91cd\u8bd5\u3002', owner).exec()

    def _export_completed(self, result):
        if getattr(getattr(self.window(), 'runtime', None), '_exiting', False) or getattr(self.window(), '_export_close_pending', False):
            return
        from modules.common_widgets.common_dialogs import MessageDialog
        detail = ('\u5df2\u5bfc\u51fa\u5230\uff1a\n' + result['zip_path'] +
                  f"\n\u4fe1\u606f {result['info_lines']} \u884c\uff0c\u8bca\u65ad {result['diagnostics_included']} \u4e2a\u3002")
        if result['skipped']:
            detail += '\n\u8df3\u8fc7\uff1a\n' + '\n'.join(f"{v['file']}\uff1a{v['reason']}" for v in result['skipped'][:20]) + ('\n\u5176\u4f59\u8bb0\u5f55\u8be6\u89c1\u538b\u7f29\u5305\u7684\u9644\u4ef6\u7d22\u5f15\u3002' if len(result['skipped']) > 20 else '')
        MessageDialog('\u5bfc\u51fa\u65e5\u5fd7\u5b8c\u6210', self.display_text(detail), self.window()).exec()

    def _export_failed(self, reason):
        if getattr(getattr(self.window(), 'runtime', None), '_exiting', False) or getattr(self.window(), '_export_close_pending', False):
            return
        from modules.common_widgets.common_dialogs import MessageDialog
        MessageDialog('\u5bfc\u51fa\u65e5\u5fd7\u5931\u8d25',
            self.display_text(reason) + '\n\u8bf7\u68c0\u67e5\u76ee\u5f55\u6743\u9650\u548c\u78c1\u76d8\u7a7a\u95f4\u3002', self.window()).exec()

    def _export_activity_changed(self):
        if self._export_service and not self._export_service.active:
            self.export_btn.setEnabled(True)
            self._export_service.completed.disconnect(self._export_completed)
            self._export_service.failed.disconnect(self._export_failed)
            self._export_service.activityChanged.disconnect(self._export_activity_changed)
            self._export_service = None

    def _show_context_menu(self, pos):
        """信息框右键菜单：保留系统复制/全选，只追加清空信息窗口。"""
        if self._content_hidden:
            return
        standard = self.info_box.createStandardContextMenu()
        menu = create_themed_menu(self.info_box, self._light_theme)
        menu.addActions(standard.actions())
        standard.deleteLater()
        menu.addSeparator()
        clear_action = QAction("清空信息窗口", menu)
        clear_action.triggered.connect(self.clear)
        menu.addAction(clear_action)
        menu.exec(self.info_box.viewport().mapToGlobal(pos))

    def _append_rendered(self, text, level="info"):
        """按信息层级写入带颜色的文本到信息框。

        level 支持：
          "info"    — 普通信息（继承主题默认色）
          "success" — 成功信息（不额外着色）
          "warning" — 需注意的设置（琥珀色）
          "error"   — 失败/错误（红色）
        """
        cursor = self.info_box.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if self._render_has_entry:
            cursor.insertText("\n")

        fmt = QTextCharFormat()
        if level in ("success", "warning", "error"):
            if self._light_theme:
                colors = {
                    "success": QColor("#18864b"),
                    "warning": QColor("#b8860b"),
                    "error": QColor("#c62828"),
                }
            else:
                colors = {
                    "success": QColor("#55d68a"),
                    "warning": QColor("#f0b429"),
                    "error": QColor("#ef5350"),
                }
            if level != "success" or self._color_success_messages:
                fmt.setForeground(colors[level])
        cursor.insertText(self.display_text(text), fmt)
        self._render_has_entry = True

        self.info_box.setTextCursor(cursor)
        self.info_box.ensureCursorVisible()

    def clear(self):
        """清空信息框内容。"""
        self._entries.clear()
        self._entry_sizes.clear()
        self._entry_bytes = 0
        self._diagnostic_references.clear()
        self._render_has_entry = False
        self.info_box.clear()

    def set_content_hidden(self, hidden):
        """仅遮住正文视口；文档继续接收消息，按钮与面板尺寸保持可用。"""
        self._content_hidden = bool(hidden)
        self.info_box.viewport().setVisible(not self._content_hidden)
        self.info_box.setTextInteractionFlags(Qt.NoTextInteraction if self._content_hidden else self._info_interaction_flags)
        if self._content_hidden:
            cursor = self.info_box.textCursor()
            cursor.clearSelection()
            self.info_box.setTextCursor(cursor)
            self.info_box.clearFocus()
        if self.visibility_btn is not None:
            with QSignalBlocker(self.visibility_btn):
                self.visibility_btn.setChecked(self._content_hidden)
        self._refresh_visibility_icon()

    def _refresh_visibility_icon(self):
        """主题及隐藏状态变化时同步图标、悬停提示与可访问名称。"""
        if self.visibility_btn is None:
            return
        self.visibility_btn.setIcon(get_visibility_icon(self._content_hidden, self.info_box.palette().color(QPalette.Text)))
        action = "显示信息窗口正文" if self._content_hidden else "临时隐藏信息窗口正文（继续记录）"
        self.visibility_btn.setToolTip(action)
        self.visibility_btn.setAccessibleName(action)

    def _change_font_size(self, delta):
        """内部处理文字缩放，由缩放按钮触发。"""
        self.set_font_delta(self._info_font_delta + delta)
        self.fontDeltaChanged.emit(self._info_font_delta)
        if self._save_font_delta_callback:
            self._save_font_delta_callback(self._info_font_delta)

    def set_font_delta(self, delta):
        """Apply a shared log zoom value without saving or emitting again."""
        self._info_font_delta = max(-3, min(8, int(delta)))
        self._apply_font()

    def _apply_font(self):
        """内部设置信息框字体大小。"""
        # 页面切档时会替换管理器，从当前父链获取新基准，局部缩放偏移始终保留。
        self._scale_manager = UiScaleManager.for_widget(self.parentWidget(), self._scale_manager)
        body_pt = self._scale_manager.font_pt()
        font_size = max(8, int(body_pt) + self._info_font_delta)
        font = QFont(self._scale_manager._resolved_font(), font_size)
        self.info_box.setFont(font)
        self.info_box.document().setDefaultFont(font)
        scale = self._scale_manager
        for button in (self.zoom_out_btn, self.zoom_in_btn, self.visibility_btn, self.export_btn):
            if button is None:
                continue
            icon_size = 18 if button is self.visibility_btn or not self._floating_zoom_controls else 15
            button.setIconSize(QSize(scale.px(icon_size), scale.px(icon_size)))
            if self._floating_zoom_controls:
                button.setFixedSize(scale.px(26), scale.px(26))
        self._position_zoom_overlay()

    def refresh_font(self):
        """页面主题切换或缩放变化后刷新字体。
        
        setStyleSheet 不会重置 setFont() 设置的字体，但缩放管理器
        可能在主题切换时更新，所以页面应该在 _apply_styles() 后调用此方法。
        """
        self._apply_font()

    def set_light_theme(self, enabled):
        """切换内部主题状态，刷新缩放按钮图标颜色。"""
        self._light_theme = bool(enabled)
        for btn in (self.zoom_out_btn, self.zoom_in_btn, self.export_btn):
            icon_path = btn.property("iconPath")
            if icon_path:
                btn.setIcon(get_theme_icon(icon_path, self._light_theme))
        self._refresh_visibility_icon()
        self._refresh_privacy()

    def _position_zoom_overlay(self):
        """将缩放小面板悬浮在信息框右下角，不占用文本布局高度。"""

        if self.zoom_overlay is None:
            return
        self.zoom_overlay.adjustSize()
        # 与信息框右、下内边缘直接对齐，不受文本 viewport 内边距影响。
        x = max(0, self.info_box.width() - self.zoom_overlay.width())
        y = max(0, self.info_box.height() - self.zoom_overlay.height())
        self.zoom_overlay.move(x, y)
        self.zoom_overlay.raise_()

    def resizeEvent(self, event):
        """信息面板尺寸变化后保持悬浮控件贴在右下角。"""

        super().resizeEvent(event)
        self._position_zoom_overlay()

def _create_zoom_button(parent, icon_path, tooltip, light_theme):
    """创建信息面板文字缩放按钮。"""
    btn = QToolButton(parent)
    btn.setObjectName("downloadToolButton")
    btn.setAttribute(Qt.WA_StyledBackground, True)
    btn.setCursor(Qt.PointingHandCursor)
    btn.setToolTip(tooltip)
    btn.setProperty("iconPath", icon_path)
    btn.setIcon(get_theme_icon(icon_path, light_theme))
    btn.setIconSize(QSize(18, 18))
    return btn
