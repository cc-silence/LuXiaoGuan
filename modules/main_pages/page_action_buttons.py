"""主窗口页面顶部操作按钮工厂。

主要作用：为下载页、更新页等主窗口页面创建一致的文字和图标操作按钮。
输入参数：parent 是承载按钮的页面；text、icon_path 和 tooltip 描述按钮内容。
返回值：已完成基础主题初始化的按钮控件。
副作用：只创建控件并读取 parent 的当前主题状态，不修改配置或业务数据。
调用限制：调用方负责连接点击信号；parent 应提供 light_theme 属性以应用当前主题。
"""

from PySide6.QtCore import Qt

from modules.common_widgets.icon_button import create_themed_icon_button
from modules.common_widgets.sweep_button import SweepButton


def create_page_text_button(parent, text, tooltip):
    """创建主窗口页面顶部文字操作按钮。

    参数：parent 是按钮父页面；text 是按钮文字；tooltip 是悬停说明。
    返回值：应用当前亮暗主题状态的 SweepButton。
    副作用：只创建控件，不连接业务信号、不修改页面状态。
    """
    button = SweepButton(text, parent)
    button.setObjectName("downloadActionButton")
    button.setAttribute(Qt.WA_StyledBackground, True)
    button.setCursor(Qt.PointingHandCursor)
    button.setToolTip(tooltip)
    button.set_light_theme(getattr(parent, "light_theme", False))
    return button


def create_page_icon_button(parent, icon_path, tooltip):
    """创建主窗口页面顶部图标操作按钮。

    参数：parent 是按钮父页面；icon_path 是图标资源路径；tooltip 是悬停说明。
    返回值：由公共图标按钮组件创建的主题适配按钮。
    副作用：只创建控件，不连接业务信号、不修改配置或业务数据。
    """
    return create_themed_icon_button(parent, icon_path, tooltip)


def set_page_action_enabled(button, enabled):
    """Release focus before disabling, so Qt does not scroll through sibling actions."""
    if not enabled and button.hasFocus():
        button.clearFocus()
    button.setEnabled(enabled)
