"""Application button focus painting policy; keyboard focus stays enabled."""
from PySide6.QtWidgets import QProxyStyle, QStyle, QPushButton, QToolButton


class ButtonFocusStyle(QProxyStyle):
    """Suppress native button focus rectangles without changing other drawing."""

    def drawPrimitive(self, element, option, painter, widget=None):
        if element == QStyle.PE_FrameFocusRect and isinstance(widget, (QPushButton, QToolButton)):
            return
        super().drawPrimitive(element, option, painter, widget)


def install_button_focus_style(app):
    """Use the shared application proxy, also owned by widget event filters."""
    from .widget_event_filters import ensure_widget_filter_style
    style = ensure_widget_filter_style(app)
    app._router_button_focus_style = style
