"""抓包页面内部复用的轻量表单和状态卡片。"""

from PySide6.QtCore import Qt
from modules.ui_scale_manager import UiScaleManager
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout


from PySide6.QtWidgets import QPushButton


class VerticalResizeHandle(QFrame):
    """可垂直拖拽调整目标组件高度的手柄条。"""

    def __init__(self, target_widget, min_height=80, max_height=800, parent=None):
        super().__init__(parent)
        self.target_widget = target_widget
        self.min_height = min_height
        self.max_height = max_height
        self.setCursor(Qt.SizeVerCursor)
        self.setFixedHeight(12)
        self._drag_start_y = None
        self._start_height = None
        self.setToolTip("按住上下拖拽调整终端高度")
        self.setObjectName("verticalResizeHandle")
        self.setStyleSheet(
            "QFrame#verticalResizeHandle { background: transparent; } "
            "QFrame#verticalResizeHandle:hover { background: rgba(56, 189, 248, 0.12); border-radius: 4px; }"
        )

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_start_y = event.globalPosition().toPoint().y()
            self._start_height = self.target_widget.height()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_start_y is not None:
            delta = event.globalPosition().toPoint().y() - self._drag_start_y
            new_h = max(self.min_height, min(self.max_height, self._start_height + delta))
            self.target_widget.setFixedHeight(new_h)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_start_y = None
        event.accept()

    def paintEvent(self, event):
        from PySide6.QtGui import QPainter, QColor
        super().paintEvent(event)
        painter = QPainter(self)
        w = self.width()
        h = self.height()
        bar_w = min(44, max(24, w // 4))
        x = (w - bar_w) // 2
        painter.setPen(QColor(148, 163, 184, 150))
        painter.drawLine(x, h // 2 - 1, x + bar_w, h // 2 - 1)
        painter.drawLine(x, h // 2 + 2, x + bar_w, h // 2 + 2)


def create_step_card(parent, title, tooltip=""):
    """创建后台步骤卡片：大号加粗标题，标题后带 (?) 悬浮提示小按钮，无多余副标题文本。"""
    card = QFrame(parent)
    card.setObjectName("captureCard")
    card.setAttribute(Qt.WA_StyledBackground, True)
    layout = QVBoxLayout(card)
    layout.setContentsMargins(14, 12, 14, 12)
    layout.setSpacing(8)

    title_bar = QHBoxLayout()
    title_bar.setContentsMargins(0, 0, 0, 0)
    title_bar.setSpacing(6)

    title_label = QLabel(title, card)
    title_label.setObjectName("captureGroupTitle")
    title_label.setStyleSheet("font-size: 12pt; font-weight: bold;")
    title_bar.addWidget(title_label)

    if tooltip:
        help_btn = QPushButton("?", card)
        help_btn.setFixedSize(18, 18)
        help_btn.setToolTip(tooltip)
        help_btn.setCursor(Qt.PointingHandCursor)
        help_btn.setStyleSheet(
            "QPushButton { border-radius: 9px; border: 1px solid #64748b; color: #94a3b8; font-size: 11px; font-weight: bold; background: transparent; padding: 0px; } "
            "QPushButton:hover { border-color: #38bdf8; color: #38bdf8; background: rgba(56, 189, 248, 0.12); }"
        )
        title_bar.addWidget(help_btn)

    title_bar.addStretch(1)
    layout.addLayout(title_bar)
    card.title_bar = title_bar

    content_layout = QVBoxLayout()
    content_layout.setContentsMargins(0, 4, 0, 0)
    content_layout.setSpacing(8)
    layout.addLayout(content_layout)
    return card, content_layout

def create_capture_card(parent, title, description="", *, collapsible=False, expanded=True, icon_path=None, actions_at_bottom=False):
    """创建抓包页面专用分组卡片。

    参数：parent 是父页面；title 是分组名；description 是普通用户可见说明。
    返回值：(card, content_layout)。副作用：只创建控件，不保存配置或执行命令。
    """

    # 折叠只复用公共组件，原有卡片调用保持原样。
    if collapsible:
        from modules.common_widgets.collapsible_section import CollapsibleSection
        card = CollapsibleSection(title, expanded=expanded, parent=parent)
        layout = card
    else:
        card = QFrame(parent)
        card.setObjectName("captureCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(8)
        if icon_path is not None:
            from modules.common_widgets.card_icon import CardIcon
            outer = QHBoxLayout()
            outer.setContentsMargins(0, 0, 0, 0)
            UiScaleManager.for_widget(parent).bind_layout(outer, margins=(0, 0, 0, 0), spacing=14)
            outer.addWidget(CardIcon(icon_path, card), 0, Qt.AlignVCenter)
            text_layout = QVBoxLayout()
            text_layout.setContentsMargins(0, 0, 0, 0)
            text_layout.setSpacing(8)
            outer.addLayout(text_layout, 1)
            layout.addLayout(outer)
            layout = text_layout
        if title:
            title_label = QLabel(title, card)
            title_label.setObjectName("captureGroupTitle")
            title_label.setWordWrap(icon_path is not None)
            layout.addWidget(title_label)
    card.setAttribute(Qt.WA_StyledBackground, True)
    if description:
        description_label = QLabel(description, card)
        description_label.setObjectName("captureDescription")
        description_label.setWordWrap(True)
        layout.addWidget(description_label)
    if actions_at_bottom and not collapsible:
        layout.addStretch(1)
    content_layout = QVBoxLayout()
    content_layout.setContentsMargins(0, 4, 0, 0)
    content_layout.setSpacing(8)
    layout.addLayout(content_layout)
    if icon_path is not None and not collapsible and not actions_at_bottom:
        layout.addStretch(1)
    return card, content_layout


def create_form_row(parent, label_text, control, tooltip="", label_width=96):
    """创建统一的标签和输入控件行；tooltip 同时设置到标签和控件。"""

    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(9)
    label = QLabel(label_text, parent)
    label.setObjectName("captureFormLabel")
    UiScaleManager.for_widget(parent).bind_metrics(label, min_width=label_width)
    if tooltip:
        label.setToolTip(tooltip)
        control.setToolTip(tooltip)
    row.addWidget(label)
    row.addWidget(control, 1)
    return row


def create_path_row(parent, label_text, line_edit, buttons, tooltip="", label_width=96):
    """创建“标签 + 路径输入 + 一个或多个按钮”的统一行。

    参数：buttons 可传单个按钮或按钮序列；其他参数与 create_form_row 一致。
    返回值：QHBoxLayout。副作用：只创建布局，不打开对话框、不读取或保存配置。
    """

    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(8)
    label = QLabel(label_text, parent)
    label.setObjectName("captureFormLabel")
    UiScaleManager.for_widget(parent).bind_metrics(label, min_width=label_width)
    if tooltip:
        label.setToolTip(tooltip)
        line_edit.setToolTip(tooltip)
    row.addWidget(label)
    # 标记为路径语义输入框，让全局拖拽过滤器能把拖入的路径写入输入框。
    # 使用 Qt 动态属性而非 objectName，避免与抓包页 captureLineEdit 等 QSS 名称冲突。
    line_edit.setProperty("winboxPathDrop", True)
    row.addWidget(line_edit, 1)
    controls = buttons if isinstance(buttons, (list, tuple)) else (buttons,)
    for button in controls:
        row.addWidget(button)
    return row


def set_row_visible(row, visible):
    """显示或隐藏表单行中的全部控件。

    参数：row 是 create_form_row/create_path_row 返回的布局；visible 控制可见性。
    返回值：无。副作用：仅改变控件显示状态，不读取或保存配置。
    """

    for index in range(row.count()):
        widget = row.itemAt(index).widget()
        if widget is not None:
            widget.setVisible(bool(visible))


def create_status_row(parent, label_text, initial_text="待检测"):
    """创建环境能力状态行并返回可更新的状态标签。"""

    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(9)
    label = QLabel(label_text, parent)
    label.setObjectName("captureFormLabel")
    UiScaleManager.for_widget(parent).bind_metrics(label, min_width=118)
    value = QLabel(initial_text, parent)
    value.setObjectName("captureStatusValue")
    value.setProperty("captureStatus", "unknown")
    value.setWordWrap(True)
    row.addWidget(label)
    row.addWidget(value, 1)
    return row, value


def set_status_label(label, text, status):
    """更新状态标签文字和 QSS 动态属性，并立即刷新绘制。"""

    label.setText(str(text))
    if label.property("captureStatus") != status:
        label.setProperty("captureStatus", status)
        label.style().unpolish(label)
        label.style().polish(label)
    label.update()
