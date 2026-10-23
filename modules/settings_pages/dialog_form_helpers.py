"""弹窗表单辅助函数

主要作用：提供弹窗中常用的表单布局帮助函数，
让 SoftwareItemDialog 等弹窗关注业务逻辑而不是重复的 UI 搭建。

使用方式：传入 parent 参数即可，不依赖特定弹窗类。
这些函数创建的是纯 Qt 控件和布局，不保存状态，每次调用新建。
"""

from PySide6.QtCore import Qt, QEvent
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QTextEdit,
)

from ..common_widgets.path_list_editor import PathListEditor
from modules.ui_scale_manager import UiScaleManager
from .settings_widgets import create_separator, create_settings_button, setup_path_input


class _FormLabel(QLabel):
    """同一表单按实际字体共享标签列宽；字体变化后重算，避免短字段名被截断。"""

    def __init__(self, text, parent, base_width, wrap=False):
        super().__init__(text, parent)
        self._base_width = base_width
        self.setWordWrap(wrap)
        self._sync_column()

    def _sync_column(self):
        parent = self.parentWidget()
        if parent is None or getattr(parent, "_syncing_form_labels", False):
            return
        parent._syncing_form_labels = True
        try:
            labels = parent.findChildren(_FormLabel, "", Qt.FindDirectChildrenOnly)
            scale = UiScaleManager.for_widget(parent)
            width = 0
            for label in labels:
                if not hasattr(label, "_base_width"):
                    continue
                label.ensurePolished()
                width = max(width, scale.px(label._base_width))
                if not label.wordWrap():
                    width = max(width, label.fontMetrics().horizontalAdvance(label.text()) + scale.px(4))
            for label in labels:
                label.setFixedWidth(width)
        finally:
            parent._syncing_form_labels = False

    def event(self, event):
        result = super().event(event)
        if event.type() in (QEvent.FontChange, QEvent.StyleChange, QEvent.Show) and hasattr(self, "_base_width"):
            self._sync_column()
        return result


def form_row(parent, label_text, widget, label_width=68, wrap_label=False):
    """创建普通表单行。

    主要作用：左侧放固定宽度标签，右侧放输入控件或子布局，让不同字段的输入框左边缘
    保持对齐。
    输入参数：parent 是 Qt 父控件；label_text 是左侧标签文字；widget 是右侧控件或 QHBoxLayout；
    label_width 是标签固定宽度，默认 68px；wrap_label 为 True 时标签可在固定宽度内换行，为 "auto" 时按标签实际字体判断长名称。
    返回值：QHBoxLayout，调用方负责加入外层布局。
    副作用：只创建布局和标签，并把传入控件加入布局；不读写配置。
    """
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    UiScaleManager.for_widget(parent).bind_layout(row, spacing=8, margins=(0, 0, 0, 0))
    auto_wrap = wrap_label == "auto"
    label = _FormLabel(label_text, parent, label_width, False if auto_wrap else wrap_label)
    if auto_wrap:
        # Measure the styled label font, which can differ from its parent font.
        wrap_label = label.fontMetrics().horizontalAdvance(label_text) > UiScaleManager.for_widget(parent).px(160)
        if wrap_label:
            label.setWordWrap(True)
            label._sync_column()
    if wrap_label:
        # 保持所有输入框左边缘一致，长标签在自己的固定列内折成多行而不挤压输入区。
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        label.setContentsMargins(0, 7, 0, 0)
    elif isinstance(widget, (QTextEdit, PathListEditor)):
        label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        label.setContentsMargins(0, 7, 0, 0)
    else:
        label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    row.addWidget(label)
    if isinstance(widget, QHBoxLayout):
        row.addLayout(widget, 1)
    else:
        row.addWidget(widget, 1)
    return row


def path_row(parent, label_text, line_edit, button, label_width=68):
    """创建路径选择行。

    主要作用：把“标签 + 路径输入框 + 浏览按钮”组合成统一结构，供下载项编辑弹窗复用。
    输入参数：parent 是 Qt 父控件；label_text 是左侧标签文字；line_edit 是路径输入框；
    button 是右侧浏览按钮；label_width 是标签固定宽度，默认 68px。
    返回值：QHBoxLayout，调用方负责加入外层布局。
    副作用：只创建布局和标签，并把传入控件加入布局；不读写配置。
    """
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    UiScaleManager.for_widget(parent).bind_layout(row, spacing=8, margins=(0, 0, 0, 0))
    label = _FormLabel(label_text, parent, label_width)
    label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    row.addWidget(label)
    # 标记为路径语义输入框，让全局拖拽过滤器能把拖入的路径写入输入框。
    # 使用 Qt 动态属性而非 objectName，避免与已有 QSS objectName 冲突或改变外观。
    line_edit.setProperty("winboxPathDrop", True)
    row.addWidget(line_edit, 1)
    row.addWidget(button)
    return row


def action_grid():
    """创建更新动作使用的多列网格。

    主要作用：把多个小开关排成 2-3 列，避免一行一个导致更新方案区域过长。
    输入参数：无。
    返回值：已设置边距、间距和列伸缩的 QGridLayout。
    副作用：只创建布局，不读写配置。
    """
    grid = QGridLayout()
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(18)
    grid.setVerticalSpacing(8)
    grid.setColumnStretch(0, 1)
    grid.setColumnStretch(1, 1)
    grid.setColumnStretch(2, 1)
    return grid


def sub_title(parent, text):
    """创建弹窗内容区分组小标题。

    主要作用：在弹窗内部区分“基础信息”“更新方案”等小节，不和弹窗主标题争抢视觉层级。
    输入参数：parent 是 Qt 父控件；text 是标题文字。
    返回值：objectName 为 settingsSubTitle 的 QLabel。
    副作用：只创建控件，不读写配置。
    """
    label = QLabel(text, parent)
    label.setObjectName("settingsSubTitle")
    return label


def create_path_button(parent, text):
    """创建弹窗中的路径浏览按钮。

    主要作用：使用设置窗口 toolbar 按钮语义，统一“选择图标”“选择脚本”“选择EXE”等按钮外观。
    输入参数：parent 是 Qt 父控件；text 是按钮文字。
    返回值：已设置最小尺寸和 toolbar 样式的 QPushButton。
    副作用：只创建按钮，不连接信号，不读写配置。
    """
    button = create_settings_button(text, parent, "toolbar")
    UiScaleManager.for_widget(parent).bind_metrics(button, min_width=82, min_height=28)
    return button


def create_dialog_path_input(parent, button_text, placeholder="", tooltip=""):
    """创建弹窗表单里的路径输入框和浏览按钮。

    主要作用：统一“QLineEdit + setup_path_input + create_path_button”这组重复结构，调用方
    再自行用 path_row() 加标签，并自行连接按钮点击逻辑。
    输入参数：parent 是 Qt 父控件；button_text 是按钮文字；placeholder 是输入框占位提示；
    tooltip 是输入框说明。
    返回值：(line_edit, button) 元组。
    副作用：只创建控件和设置显示属性，不连接信号，不读写配置。
    """

    line_edit = QLineEdit(parent)
    setup_path_input(line_edit)
    if placeholder:
        line_edit.setPlaceholderText(placeholder)
    if tooltip:
        line_edit.setToolTip(tooltip)
    button = create_path_button(parent, button_text)
    return line_edit, button


def separator(parent):
    """创建弹窗内容区分隔线。

    主要作用：让基础信息、更新方案之间有清晰层次；保留此函数名是为了兼容旧调用方。
    输入参数：parent 是 Qt 父控件。
    返回值：objectName 为 settingsSeparator 的 QFrame。
    副作用：只创建控件，不读写配置。
    """
    return create_separator(parent)


def set_layout_visible(layout, visible):
    """递归设置布局内所有控件和子布局的可见性。

    主要作用：Qt 的 layout 本身没有 setVisible，所以需要逐个处理内部控件和子布局。
    这用于“开关未开启时隐藏对应参数区”，避免禁用控件仍然占据大块空白。
    输入参数：layout 是需要递归处理的 QLayout；visible 控制显示或隐藏。
    返回值：无。
    副作用：会修改布局内控件的可见状态，不读写配置。
    """
    for index in range(layout.count()):
        item = layout.itemAt(index)
        widget = item.widget()
        child_layout = item.layout()
        if widget is not None:
            widget.setVisible(visible)
        if child_layout is not None:
            set_layout_visible(child_layout, visible)
