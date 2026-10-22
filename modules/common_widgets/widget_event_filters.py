"""通过 QWidget 原生样式初始化给控件注册过滤器，避免拦截浏览器内部对象。"""
import weakref
from shiboken6 import isValid
from PySide6.QtWidgets import QProxyStyle, QStyleFactory, QWidget


class WidgetFilterStyle(QProxyStyle):
    """保留原平台样式；弱引用过滤器，只监听存活控件。"""
    def __init__(self, base):
        super().__init__(base)
        self.registrations = []

    def polish(self, target):
        result = super().polish(target)
        if isinstance(target, QWidget):
            for reference, accepts in tuple(self.registrations):
                event_filter = reference()
                if event_filter is None or not isValid(event_filter):
                    self.registrations.remove((reference, accepts))
                elif accepts(target):
                    target.installEventFilter(event_filter)
        return result


def install_widget_event_filter(app, event_filter, accepts):
    """注册现有和后建控件； accepts 决定监听范围，不轮询、不保存控件列表。"""
    style = getattr(app, '_winbox_widget_filter_style', None)
    if style is None:
        base = QStyleFactory.create(app.style().objectName())
        if base is None:
            raise RuntimeError('Cannot preserve the current native Qt widget style')
        style = WidgetFilterStyle(base)
        app._winbox_widget_filter_style = style
        app.setStyle(style)
    if any(item[0]() is event_filter for item in style.registrations):
        return
    style.registrations.append((weakref.ref(event_filter), accepts))
    for widget in app.allWidgets():
        if accepts(widget):
            widget.installEventFilter(event_filter)
