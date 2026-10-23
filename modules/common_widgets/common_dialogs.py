"""跨页面通用无边框弹窗。

主要作用：提供 ConfirmDialog 和 MessageDialog，避免主页面反向依赖 settings_pages。
输入参数：各弹窗接收标题、正文和父窗口。
返回值：ConfirmDialog 通过 exec() 结果表示确认/取消；MessageDialog 用于展示消息。
副作用：只显示界面，不修改配置、文件或业务数据。
调用限制：弹窗外壳始终由本模块加载公共主题；调用方只提供父窗口、标题和正文。
"""

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QCheckBox, QLabel, QPushButton, QTextEdit

from modules.config_manager import ConfigManager
from modules.dialog_theme import build_current_base_stylesheet
from .frameless_dialog import FramelessDialog
from modules.ui_scale_manager import UiScaleManager


def _create_dialog_button(text, parent, object_name):
    """创建通用弹窗按钮，并保留设置主题使用的 objectName。

    参数：text 是按钮文字；parent 是父弹窗；object_name 是主题选择器名称。
    返回值：QPushButton。
    副作用：只创建控件，不连接业务信号。
    """
    button = QPushButton(text, parent)
    button.setObjectName(object_name)
    button.setMinimumHeight(30)
    button.setCursor(Qt.PointingHandCursor)
    UiScaleManager.for_widget(parent).bind_metrics(button, min_width=88, min_height=32)
    button.setAutoDefault(False)
    return button


def _add_message_content(dialog, message):
    """按正文长度向弹窗添加标签或可滚动只读文本框。

    参数：dialog 是 FramelessDialog 子类；message 是用户可见正文。
    返回值：无。
    副作用：会向 content_layout 添加控件；长文本会把弹窗调整为 560x340。
    """
    message_text = str(message or "")
    if "\n" in message_text or len(message_text) > 180:
        dialog.set_scaled_size((460, 220), (560, 340))
        message_box = QTextEdit(dialog)
        message_box.setObjectName("settingsMessageText")
        message_box.setReadOnly(True)
        message_box.setPlainText(message_text)
        message_box.setMinimumHeight(170)
        dialog.content_layout.addWidget(message_box, 1)
        return

    message_label = QLabel(message_text, dialog)
    message_label.setObjectName("settingsHint")
    message_label.setWordWrap(True)
    dialog.content_layout.addWidget(message_label, 1)


def apply_common_dialog_theme(dialog, parent):
    """为公共消息和确认弹窗加载完整的统一主题。

    参数：dialog 是已创建的公共无边框弹窗；parent 只用于读取可选的缩放管理器。
    返回值：无。
    副作用：覆盖弹窗从父窗口继承的 QSS，确保任意调用入口都有背景、标题栏和按钮样式。
    调用限制：调用方应在创建 FramelessDialog 后立即调用，且不要再用不完整的页面 QSS
    覆盖它。下载、更新等业务专用弹窗可以复用此函数保证外壳样式一致。
    """
    config_manager = ConfigManager()
    scale_manager = UiScaleManager.for_widget(parent)
    dialog.setStyleSheet(
        build_current_base_stylesheet(
            config_manager,
            scale_manager=scale_manager,
        )
    )


class ConfirmDialog(FramelessDialog):
    """项目统一确认弹窗。

    参数：title 是标题；message 是确认正文；parent 是父窗口；remember_option_text 非空时
    在正文下显示一个可选复选框。
    返回值：调用方通过 exec() 是否等于 Accepted 判断用户选择；需要时读取 remember_checked。
    副作用：只显示模态弹窗，不直接保存业务配置。
    """

    def __init__(self, title, message, parent=None, remember_option_text="", yes_text="确定", no_text="取消"):
        # 公共确认弹窗不能继承页面或快捷面板的局部 QSS，否则外壳会被不完整的样式覆盖。
        super().__init__(title, parent, inherit_parent_stylesheet=False, window_controls=False)
        apply_common_dialog_theme(self, parent)
        self.set_scaled_size((460, 220))
        self.remember_checked = False
        self.secondary_selected = False
        _add_message_content(self, message)
        self.remember_option = None
        if remember_option_text:
            self.remember_option = QCheckBox(str(remember_option_text), self)
            self.remember_option.setToolTip("勾选后会保存本次选择，后续不再显示相同提示。")
            self.content_layout.addWidget(self.remember_option)

        self.yes_button = _create_dialog_button(str(yes_text), self, "settingsPrimaryButton")
        self.no_button = _create_dialog_button(str(no_text), self, "settingsSecondaryButton")
        self.yes_button.clicked.connect(self._accept)
        self.no_button.clicked.connect(self._select_secondary)
        self.button_layout.addWidget(self.yes_button)
        self.button_layout.addSpacing(10)
        self.button_layout.addWidget(self.no_button)

    def _select_secondary(self):
        self.secondary_selected = True
        self.reject()

    def _accept(self):
        """确认时读取可选复选框状态，再关闭弹窗。"""

        self.remember_checked = bool(self.remember_option and self.remember_option.isChecked())
        self.accept()


class MessageDialog(FramelessDialog):
    """项目统一消息弹窗。

    参数：title 是标题；message 是正文；parent 是父窗口；button_text 是按钮文字。
    返回值：无特定业务数据。
    副作用：只显示模态弹窗。
    """

    def __init__(self, title, message, parent=None, button_text="确定"):
        # 公共消息弹窗始终只使用公共主题，调用位置不应影响背景、关闭图标或按钮外观。
        super().__init__(title, parent, inherit_parent_stylesheet=False, window_controls=False)
        apply_common_dialog_theme(self, parent)
        self.set_scaled_size((460, 220))
        _add_message_content(self, message)

        self.ok_button = _create_dialog_button(button_text, self, "settingsPrimaryButton")
        self.ok_button.clicked.connect(self.accept)
        self.button_layout.setContentsMargins(12, 4, 20, 12)
        self.button_layout.addWidget(self.ok_button)


def create_themed_confirm_dialog(title, message, parent=None, stylesheet="", yes_text="确定", no_text="取消"):
    """创建已应用公共主题的确认弹窗。

    参数：title 是标题；message 是正文；parent 是父窗口；stylesheet 为历史兼容参数，
    不再用于覆盖公共主题；yes_text、no_text 是两个操作按钮的文字。
    返回值：已创建并设置主题的 ConfirmDialog，调用方负责 exec() 和处理结果。
    副作用：创建弹窗并设置内联 QSS，不修改配置、文件或业务状态。
    """
    return ConfirmDialog(title, message, parent, yes_text=yes_text, no_text=no_text)


def exec_confirm_with_countdown(dialog, seconds=10, button=None, timeout_action="yes", base_text=""):
    """以倒计时方式显示模态确认弹窗，超时后自动触发指定按钮。

    主要作用：把"按钮显示倒计时、倒计时结束自动执行某按钮动作"的交互抽成公共函数，
    供杀进程确认（10 秒自动继续）和目录确认（10 秒自动取消）等处复用。

    参数：
        dialog: 已创建的 ConfirmDialog 实例，由本函数负责 exec()。
        seconds: 倒计时秒数；<=0 表示直接 exec() 不启动倒计时。
        button: 显示倒计时的按钮（默认 yes_button）；它会在剩余 1 秒内逐秒刷新成
            "原始文字 Ns"。
        timeout_action: 倒计时结束自动执行的按钮动作，可选 "yes"/"no"。
        base_text: 按钮原始文字；留空时读取 button.text() 当前值，结束后恢复原样。

    返回值：dialog.exec() 的对话框结果（Accepted/Rejected）。
    副作用：倒数期间每秒改写按钮文字；结束后恢复按钮文字；不修改配置、文件或业务数据。
    调用限制：必须在 Qt 主线程调用；倒计时依赖模态 exec() 的事件循环正常运行。
    """

    if seconds <= 0:
        return dialog.exec()
    target_button = button or dialog.yes_button
    base = base_text or str(target_button.text())
    remaining = [seconds]
    timer = QTimer(dialog)

    def tick():
        remaining[0] -= 1
        if remaining[0] <= 0:
            target_button.setText(base)
            if timeout_action == "yes":
                dialog._accept()
            else:
                dialog.reject()
        else:
            target_button.setText(f"{base} {remaining[0]}s")

    timer.timeout.connect(tick)
    timer.start(1000)
    try:
        return dialog.exec()
    finally:
        timer.stop()
        target_button.setText(base)
