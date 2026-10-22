"""跨页面复用的主题图标按钮。

主要作用：创建下载页、更新页和常用设置页共用的 QToolButton，统一尺寸、图标、
tooltip、主题图标和样式对象名。
输入参数：parent 是父控件，icon_path 是 Qt 资源图标路径，tooltip 是悬停提示。
返回值：配置完成的 QToolButton。
副作用：只创建控件并保存 iconPath 属性，不执行点击动作、不修改配置。
调用限制：调用方负责连接 clicked 信号；父控件应提供可选的 light_theme 属性。
"""

from PySide6.QtCore import QSize, Qt, QEvent
from PySide6.QtWidgets import QToolButton, QPushButton

from modules.dialog_theme import detect_light_theme
from modules.icon_utils import get_theme_icon
from modules.ui_scale_manager import UiScaleManager


class WindowCloseButton(QPushButton):
    """标题栏关闭按钮使用矢量图标，随主题调色板与界面尺寸更新。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("settingsCloseBtn")
        UiScaleManager.for_widget(parent).bind_metrics(self, fixed_size=(28, 28), icon_size=20)
        self._refresh_icon()

    def _refresh_icon(self):
        from PySide6.QtGui import QColor
        light = detect_light_theme(self)
        color = QColor("#343a40" if light else "#f4f6f8")
        self.setIcon(get_theme_icon(":/icons/images/icons/icon_close.png", True, light_color=color))

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.PaletteChange, QEvent.StyleChange):
            self._refresh_icon()


def create_themed_icon_button(parent, icon_path, tooltip):
    """创建跨页面统一样式的图标按钮。"""
    button = QToolButton(parent)
    button.setObjectName("downloadToolButton")
    button.setAttribute(Qt.WA_StyledBackground, True)
    button.setCursor(Qt.PointingHandCursor)
    button.setToolTip(tooltip)
    button.setProperty("iconPath", icon_path)
    button.setIcon(get_theme_icon(icon_path, detect_light_theme(parent)))
    UiScaleManager.for_widget(parent).bind_metrics(button, icon_size=18)
    return button


def refresh_tool_button_icons(root, is_light):
    """按主题刷新 root 下所有带 iconPath 的图标按钮。

    主题切换时调用；构造时的主题猜测可能不准，这里是确定性刷新。
    无 iconPath 的按钮不动。
    """

    for button in root.findChildren(QToolButton):
        icon_path = button.property("iconPath")
        if icon_path:
            button.setIcon(get_theme_icon(str(icon_path), bool(is_light)))
