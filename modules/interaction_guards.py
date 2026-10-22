from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QAbstractSpinBox, QComboBox, QSlider


class ParameterWheelGuard(QObject):
    # 全局参数控件滚轮保护器。
    # QComboBox、QSpinBox、QSlider 默认会响应鼠标滚轮，容易在滚动设置页时误改参数。
    # 这里统一拦截未展开下拉框、数字输入框和滑杆的滚轮事件，让滚轮优先交给页面滚动。
    def eventFilter(self, watched, event):
        if event.type() != QEvent.Wheel:
            return False
        if isinstance(watched, QComboBox):
            if watched.view().isVisible():
                return False
            event.ignore()
            return True
        if isinstance(watched, (QAbstractSpinBox, QSlider)):
            event.ignore()
            return True
        return False


def install_interaction_guards(app):
    # 在 QApplication 创建后调用一次。
    # 把过滤器对象挂到 app 上，避免 Python 垃圾回收后过滤器失效。
    guard = ParameterWheelGuard(app)
    from modules.common_widgets.widget_event_filters import install_widget_event_filter
    install_widget_event_filter(app, guard, lambda widget: isinstance(widget, (QAbstractSpinBox, QComboBox, QSlider)))
    app._winbox_parameter_wheel_guard = guard
    return guard
