"""WinBox 全局布局边距统一管理。

主要作用：集中定义页面和对话框的边距常量，避免各窗口各自硬编码导致风格不一致。
调用限制：所有页面根布局、对话框内容区和按钮栏边距都应引用这里的常量。
"""

from modules.ui_scale_manager import UiScaleManager

# ── 页面内容边距 ──────────────────────────────────────────────
# 下载页、更新页、常用设置页、设置子页面共用。
PAGE_CONTENT_MARGINS = (4, 4, 4, 4)

# ── 对话框内容边距 ────────────────────────────────────────────
# 设置窗口内容区、各弹窗内容区共用，与页面边距保持一致。
DIALOG_CONTENT_MARGINS = (4, 4, 4, 4)

# ── 对话框按钮栏边距 ──────────────────────────────────────────
DIALOG_BUTTON_MARGINS = (4, 4, 20, 4)


def apply_page_content_margins(layout):
    """为页面主要内容布局应用统一边距。

    参数：layout 是 Qt 页面根布局。
    返回值：无。
    副作用：修改页面内容区到父窗口四边的留白，不改变布局内部控件间距。
    """
    UiScaleManager.for_widget(layout.parentWidget()).bind_layout(layout, margins=PAGE_CONTENT_MARGINS)


def apply_dialog_content_margins(layout):
    """为对话框内容区应用统一边距。

    参数：layout 是 Qt 对话框内容布局。
    返回值：无。
    副作用：修改对话框内容区到边框的留白。
    """
    UiScaleManager.for_widget(layout.parentWidget()).bind_layout(layout, margins=DIALOG_CONTENT_MARGINS)


def apply_dialog_button_margins(layout):
    """为对话框底部按钮栏应用统一边距。

    参数：layout 是 Qt 对话框按钮布局。
    返回值：无。
    副作用：修改按钮栏到边框的留白。
    """
    UiScaleManager.for_widget(layout.parentWidget()).bind_layout(layout, margins=DIALOG_BUTTON_MARGINS)
