"""统一原生输入框的提示文字；兼容 Qt 6.3 的 QSS 调色板行为。"""

import re
import logging
from functools import lru_cache

from PySide6.QtCore import QObject, QEvent
from PySide6.QtGui import QPalette, QColor, QBrush
from PySide6.QtWidgets import QLineEdit, QTextEdit, QPlainTextEdit


@lru_cache(maxsize=2)
def _placeholder_brush(light_theme):
    """读取公共主题已有的灰色；只缓存两种画刷，不依赖业务页的选择器优先级。"""
    from modules.dialog_theme import extract_selector_property, fallback_settings_stylesheet
    qss = fallback_settings_stylesheet(light_theme=light_theme)
    value = extract_selector_property(qss, "QTextEdit:disabled", "color", "")
    rgb = re.fullmatch(r"rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", value)
    color = QColor(*(int(part) for part in rgb.groups())) if rgb else QColor(value)
    if not color.isValid():
        logging.getLogger(__name__).warning("公共输入框提示文字主题颜色缺失或无效：%r", value)
        return None
    return QBrush(color)


class InputAppearance(QObject):
    """随主题调色板变化维护提示色，不改正文、字体、内容或输入行为。"""

    _EVENTS = (QEvent.Polish, QEvent.StyleChange, QEvent.PaletteChange)
    _INPUTS = (QLineEdit, QTextEdit, QPlainTextEdit)

    def __init__(self, app):
        super().__init__(app)
        self._applying = False

    def refresh(self, widget):
        """从主题的弱化文字色更新提示色；重入保护只覆盖本次同步调色板更新。"""
        if self._applying or not isinstance(widget, self._INPUTS):
            return
        palette = widget.palette()
        # Qt 6.3 的 QSS color 会同时覆盖 PlaceholderText，不能只设置应用调色板。
        # 页面可能用高优先级正文规则覆盖 Disabled.Text；从公共 QSS 取色而不是复制该角色。
        brush = _placeholder_brush(palette.color(QPalette.Base).lightness() >= 128)
        if brush is None:
            return
        groups = (QPalette.Active, QPalette.Inactive, QPalette.Disabled)
        if all(palette.brush(group, QPalette.PlaceholderText) == brush for group in groups):
            return
        self._applying = True
        try:
            for group in groups:
                palette.setBrush(group, QPalette.PlaceholderText, brush)
            widget.setPalette(palette)
        finally:
            self._applying = False

    def eventFilter(self, watched, event):
        if event.type() in self._EVENTS:
            self.refresh(watched)
        return False


def install_input_appearance(app):
    """应用启动时安装一次，自动覆盖后建输入框；不持有控件列表或启动定时器。"""
    appearance = getattr(app, "_winbox_input_appearance", None)
    if appearance is None:
        appearance = InputAppearance(app)
        app._winbox_input_appearance = appearance
        from .widget_event_filters import install_widget_event_filter
        install_widget_event_filter(app, appearance, lambda widget: isinstance(widget, appearance._INPUTS))
        for widget in app.allWidgets():
            appearance.refresh(widget)
    return appearance
