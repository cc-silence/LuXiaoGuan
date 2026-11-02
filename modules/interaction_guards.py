"""Route wheel input to its scroll owner without changing form parameters."""
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPointF
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import (
    QApplication, QAbstractScrollArea, QAbstractSpinBox, QComboBox,
    QScrollBar, QSlider, QWidget,
)


class ParameterWheelGuard(QObject):
    """One widget policy for existing and lazily created pages and dialogs."""

    def eventFilter(self, watched, event):
        if event.type() != QEvent.Wheel or not isinstance(watched, QWidget):
            return False
        # Popup lists and native scroll viewports retain their own wheel handling.
        if QApplication.activePopupWidget() is not None:
            return False
        if isinstance(watched, (QAbstractScrollArea, QScrollBar)):
            return False
        owner = watched.parentWidget()
        while owner is not None:
            if isinstance(owner, QAbstractScrollArea):
                viewport = owner.viewport()
                if watched is viewport:
                    return False
                # Send a mapped copy straight to the nearest viewport. Ignoring an
                # event on a disabled child or consuming it in a filter is not a
                # reliable handoff to Qt's native parent-propagation path.
                forwarded = QWheelEvent(
                    QPointF(viewport.mapFromGlobal(event.globalPosition().toPoint())),
                    event.globalPosition(), event.pixelDelta(), event.angleDelta(),
                    event.buttons(), event.modifiers(), event.phase(),
                    event.inverted(), event.source(),
                )
                QCoreApplication.sendEvent(viewport, forwarded)
                event.setAccepted(forwarded.isAccepted())
                return True
            if owner.isWindow():
                break
            owner = owner.parentWidget()
        if isinstance(watched, (QAbstractSpinBox, QComboBox, QSlider)):
            event.ignore()
            return True
        return False


def install_interaction_guards(app):
    """Share the native-style widget registry; no polling or widget cache."""
    guard = getattr(app, "_winbox_parameter_wheel_guard", None)
    if guard is not None:
        return guard
    guard = ParameterWheelGuard(app)
    from modules.common_widgets.widget_event_filters import install_widget_event_filter
    install_widget_event_filter(app, guard, lambda widget: isinstance(widget, QWidget))
    app._winbox_parameter_wheel_guard = guard
    return guard
