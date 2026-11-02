"""Opaque native main-window backing with the existing themed frame outline."""
from PySide6.QtCore import QEvent, QObject, QRectF
from PySide6.QtGui import QColor, QPalette, QPainterPath, QRegion
from modules.dialog_theme import extract_selector_property


class WindowSurface(QObject):
    """Keep alpha out of the native main window; clip only its outer corners."""

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self._shape_key = None
        window.setAutoFillBackground(True)
        window.installEventFilter(self)
        window.window_frame.installEventFilter(self)
        self.sync()

    def sync(self):
        window = self.window
        style = window.styleSheet()
        color = QColor(window.window_frame.palette().color(QPalette.Window))
        color.setAlpha(255)
        palette = window.palette()
        if palette.color(QPalette.Window) != color:
            palette.setColor(QPalette.Window, color)
            window.setPalette(palette)
        value = extract_selector_property(style, "#settingsWindowFrame", "border-radius", "0px")
        try:
            radius = max(0.0, float(value.removesuffix("px").strip()))
        except ValueError:
            radius = 0.0
        rect = window.window_frame.geometry()
        key = (rect.x(), rect.y(), rect.width(), rect.height(), radius)
        if key == self._shape_key or rect.isEmpty():
            return
        self._shape_key = key
        if radius:
            path = QPainterPath()
            path.addRoundedRect(QRectF(rect), radius, radius)
            window.setMask(QRegion(path.toFillPolygon().toPolygon()))
        else:
            window.setMask(QRegion(rect))

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Resize, QEvent.Show, QEvent.WindowStateChange):
            self.sync()
        return False
