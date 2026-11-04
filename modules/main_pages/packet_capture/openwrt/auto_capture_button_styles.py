"""自动抓包按钮样式与状态辅助函数。"""

from PySide6.QtWidgets import QPushButton


def apply_stop_button_style(btn: QPushButton, is_stop: bool, is_light: bool = False):
    """设置或清除『停止抓包』操作按钮的警示红色前景样式。

    遵守明暗主题要求：
    - 深色模式：警示红色前景 #ef4444，悬停 #f87171；
    - 浅色模式：高对比警示红色前景 #dc2626，悬停 #b91c1c；
    - 禁用状态：保留红色调半透明前景，确保收尾等待时仍保持警示红色认知；
    - 非停止状态：清空内联样式，完整回归主题标准样式。
    """
    if btn is None:
        return
    if is_stop:
        if is_light:
            btn.setStyleSheet(
                "QPushButton#downloadActionButton, #downloadActionButton, QPushButton { "
                "color: #dc2626; font-weight: bold; border-color: rgba(220, 38, 38, 0.45); } "
                "QPushButton#downloadActionButton:hover, #downloadActionButton:hover, QPushButton:hover { "
                "color: #b91c1c; border-color: #dc2626; background: rgba(220, 38, 38, 0.08); } "
                "QPushButton#downloadActionButton:disabled, #downloadActionButton:disabled, QPushButton:disabled { "
                "color: rgba(220, 38, 38, 0.5); border-color: rgba(220, 38, 38, 0.25); }"
            )
        else:
            btn.setStyleSheet(
                "QPushButton#downloadActionButton, #downloadActionButton, QPushButton { "
                "color: #ef4444; font-weight: bold; border-color: rgba(239, 68, 68, 0.45); } "
                "QPushButton#downloadActionButton:hover, #downloadActionButton:hover, QPushButton:hover { "
                "color: #f87171; border-color: #ef4444; background: rgba(239, 68, 68, 0.12); } "
                "QPushButton#downloadActionButton:disabled, #downloadActionButton:disabled, QPushButton:disabled { "
                "color: rgba(239, 68, 68, 0.5); border-color: rgba(239, 68, 68, 0.25); }"
            )
    else:
        btn.setStyleSheet("")

