"""无线设置弹窗的表单组件；仅处理内存中的输入和页面切换。"""
from PySide6.QtCore import Qt, Signal, QSignalBlocker
from PySide6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget, QSizePolicy
from modules.common_widgets.combo_box import CommonComboBox
from modules.common_widgets.icon_button import create_themed_icon_button
from modules.dialog_theme import detect_light_theme
from modules.icon_utils import get_theme_icon
from modules.settings_pages.dialog_form_helpers import form_row
from modules.settings_pages.settings_widgets import create_inline_stack, create_settings_navigation_tab
from modules.ui_scale_manager import UiScaleManager


def hint(text, parent):
    """创建可换行的普通说明，按纯文本显示用户提供的名称。"""
    label = QLabel(text, parent)
    label.setObjectName("settingsHint")
    label.setTextFormat(Qt.PlainText)
    label.setWordWrap(True)
    label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
    return label


def text_input(parent, value="", placeholder=""):
    """创建普通输入框；不自动保存、不改写用户输入。"""
    edit = QLineEdit(parent)
    edit.setText(value)
    edit.setPlaceholderText(placeholder)
    return edit


def add_field(layout, parent, label, control, tooltip):
    """复用公共表单列宽与布局，把作用说明同时绑定到输入框。"""
    control.setToolTip(tooltip)
    row = form_row(parent, label, control, label_width=96, wrap_label="auto")
    control._form_label = row.itemAt(0).widget()
    layout.addLayout(row)
    return control


def choice(parent, options, tooltip=""):
    """创建公共单选下拉框；首项表示当前尚未确定的预设。"""
    box = CommonComboBox(parent)
    for label, value in options:
        box.addItem(label, value)
    box.set_light_theme(detect_light_theme(parent))
    box.setToolTip(tooltip)
    return box


def clear_editor_update(owner, name):
    """Release the previous callback and its captured device data before refill."""
    bindings = getattr(owner, "_editor_updates", {})
    previous = bindings.pop(name, None)
    if previous is not None:
        callback, signals = previous
        for signal in signals:
            signal.disconnect(callback)


def bind_editor_update(owner, name, callback, *signals):
    """Keep one bounded binding for each editor responsibility."""
    clear_editor_update(owner, name)
    if not hasattr(owner, "_editor_updates"):
        owner._editor_updates = {}
    for signal in signals:
        signal.connect(callback)
    owner._editor_updates[name] = (callback, signals)


class PasswordInput(QWidget):
    """密码与眼睛按钮；内容只在弹窗内存中，关闭时由所属弹窗清空。"""
    storedPasswordRequested = Signal()

    def __init__(self, parent=None, placeholder="输入 Wi-Fi 密码"):
        super().__init__(parent)
        self._can_read_stored = False
        self._stored_password = None
        layout = QHBoxLayout(self)
        UiScaleManager.for_widget(self).bind_layout(layout, margins=(0, 0, 0, 0), spacing=6)
        self.edit = text_input(self, placeholder=placeholder)
        self.edit.setEchoMode(QLineEdit.Password)
        self.toggle = create_themed_icon_button(self, ":/icons/images/icons/cil-low-vision.png", "显示密码")
        self.toggle.setCheckable(True)
        self.toggle.setAccessibleName("显示或隐藏密码")
        self.toggle.setAutoRaise(True)
        UiScaleManager.for_widget(self).bind_metrics(self.toggle, fixed_size=(30, 30), icon_size=18)
        layout.addWidget(self.edit, 1)
        layout.addWidget(self.toggle)
        self.toggle.toggled.connect(self._toggle)

    def _toggle(self, visible):
        """切换明文显示，不读取路由器、不生成日志。"""
        if visible and self._can_read_stored and not self.edit.text():
            with QSignalBlocker(self.toggle):
                self.toggle.setChecked(False)
            self.storedPasswordRequested.emit()
            return
        self.edit.setEchoMode(QLineEdit.Normal if visible else QLineEdit.Password)
        self.toggle.setToolTip("隐藏密码" if visible else "显示密码")
        path = ":/icons/images/icons/cil-lock-locked.png" if visible else ":/icons/images/icons/cil-low-vision.png"
        self.toggle.setProperty("iconPath", path)
        self.toggle.setIcon(get_theme_icon(path, detect_light_theme(self)))

    def set_stored_available(self, available):
        self._can_read_stored = bool(available)
        if available:
            self.toggle.setToolTip("查看路由器保存的密码")

    def show_stored_password(self, password, *, visible=True):
        self._stored_password = password
        self.edit.setText(password)
        self.toggle.setChecked(bool(visible and password))
        self.edit.setEchoMode(QLineEdit.Normal if visible and password else QLineEdit.Password)

    def submitted_text(self):
        text = self.edit.text()
        return "" if self._stored_password is not None and text == self._stored_password else text

    def clear(self):
        """清理控件中的密码并恢复隐藏状态。"""
        self._stored_password = None
        self.edit.clear()
        self.toggle.setChecked(False)


class SettingsTabs(QWidget):
    """复用公共横向导航与按当前页定高的堆栈，避免隐藏页撑大弹窗。"""
    def __init__(self, titles, parent=None):
        super().__init__(parent)
        self.pages = {}
        self.buttons = []
        layout = QVBoxLayout(self)
        UiScaleManager.for_widget(self).bind_layout(layout, margins=(0, 0, 0, 0), spacing=8)
        nav = QFrame(self)
        nav.setObjectName("settingsSubpageNavigation")
        nav_layout = QHBoxLayout(nav)
        UiScaleManager.for_widget(self).bind_layout(nav_layout, margins=(0, 0, 0, 0), spacing=12)
        self.group = QButtonGroup(self)
        self.stack, self.pages = create_inline_stack(self, titles)
        for index, title in enumerate(titles):
            button = create_settings_navigation_tab(title, nav, "查看" + title + "选项；切换页面不会应用设置。")
            self.group.addButton(button, index)
            nav_layout.addWidget(button)
            self.buttons.append(button)
        nav_layout.addStretch(1)
        self.group.idClicked.connect(self.stack.setCurrentIndex)
        self.stack.currentChanged.connect(self._current_changed)
        self.buttons[0].setChecked(True)
        layout.addWidget(nav)
        layout.addWidget(self.stack)

    def _current_changed(self, index):
        """切页后重算当前内容高度，不使用定时器修补布局。"""
        self.buttons[index].setChecked(True)
        self.stack.updateGeometry()
        self.updateGeometry()
