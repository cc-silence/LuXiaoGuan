"""Screen-bounded main-window geometry, independent from widget appearance."""
from shiboken6 import isValid
from PySide6.QtCore import QObject, QEvent, QRect, QSize, QSizeF, Qt
from PySide6.QtGui import QGuiApplication, QCursor
from PySide6.QtWidgets import QLayout


def startup_screen():
    return QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()


def fitted_geometry(area, desired, center):
    size = desired.boundedTo(area.size()).expandedTo(QSize(1, 1))
    x = max(area.left(), min(center.x()-(size.width()-1)//2, area.right()-size.width()+1))
    y = max(area.top(), min(center.y()-(size.height()-1)//2, area.bottom()-size.height()+1))
    return QRect(x, y, size.width(), size.height())


class WindowGeometry(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self._screen = None
        self._handle = None
        self._applying = False
        self._auto_maximized = False
        self._reference = QSizeF(window.size()) / (window.scale_manager.global_scale/100)
        window.layout().setSizeConstraint(QLayout.SetNoConstraint)
        window.installEventFilter(self)
        self._bind_screen()
        area = self.area()
        self._minimum(area)
        window.setGeometry(fitted_geometry(area, window.size(), window.geometry().center()))

    def area(self):
        screen = self.window.screen() or startup_screen()
        return screen.availableGeometry() if screen is not None and isValid(screen) else QRect(0, 0, 1280, 720)

    def _minimum(self, area):
        scale = self.window.scale_manager.global_scale / 100
        self.window.setMinimumSize(QSize(round(960*scale), round(600*scale)).boundedTo(area.size()))

    def before_scale(self):
        if not self.window.isMaximized():
            self._reference = QSizeF(self.window.size()) / (self.window.scale_manager.global_scale/100)
        return QSizeF(self._reference)

    def apply_scale(self, reference):
        area = self.area()
        if not area.isValid(): return
        self._applying = True
        try:
            self._minimum(area)
            self._reference = QSizeF(reference)
            factor = self.window.scale_manager.global_scale / 100
            desired = QSize(round(reference.width()*factor), round(reference.height()*factor))
            was_manual_max = self.window.isMaximized() and not self._auto_maximized
            full = desired.width() > area.width() or desired.height() > area.height()
            if full or was_manual_max:
                self._auto_maximized = full and not was_manual_max
                self.window.setWindowState(self.window.windowState() | Qt.WindowMaximized)
            else:
                if self._auto_maximized:
                    self.window.setWindowState(self.window.windowState() & ~Qt.WindowMaximized)
                self._auto_maximized = False
                self.window.setGeometry(fitted_geometry(area, desired, self.window.geometry().center()))
        finally:
            self._applying = False

    def _screen_changed(self, *_):
        self._bind_screen()
        if self._applying: return
        area = self.area()
        self._minimum(area)
        if self._auto_maximized:
            self.apply_scale(self._reference)
        elif not self.window.isMaximized():
            self.window.setGeometry(fitted_geometry(area, self.window.size(), self.window.geometry().center()))

    def _release_screen(self):
        screen, self._screen = self._screen, None
        if screen is not None and isValid(screen):
            screen.availableGeometryChanged.disconnect(self._screen_changed)
            screen.destroyed.disconnect(self._screen_destroyed)

    def _release_handle(self):
        handle, self._handle = self._handle, None
        if handle is not None and isValid(handle):
            handle.screenChanged.disconnect(self._screen_changed)
            handle.destroyed.disconnect(self._handle_destroyed)

    def _screen_destroyed(self, *_):
        self._screen = None

    def _handle_destroyed(self, *_):
        self._handle = None

    def _bind_screen(self):
        handle = self.window.windowHandle()
        if handle is not None and not isValid(handle):
            handle = None
        if handle is not self._handle:
            self._release_handle()
            self._handle = handle
            if handle is not None:
                handle.screenChanged.connect(self._screen_changed)
                handle.destroyed.connect(self._handle_destroyed)
        screen = self.window.screen()
        if screen is not None and not isValid(screen):
            screen = None
        if screen is self._screen:
            return
        self._release_screen()
        self._screen = screen
        if screen is not None:
            screen.availableGeometryChanged.connect(self._screen_changed)
            screen.destroyed.connect(self._screen_destroyed)

    def eventFilter(self, watched, event):
        if watched is self.window:
            if event.type() == QEvent.Show:
                self._bind_screen()
            elif event.type() == QEvent.WindowStateChange and not self._applying and not self.window.isMaximized():
                self._auto_maximized = False
        return False

    def dispose(self):
        self.window.removeEventFilter(self)
        self._release_handle()
        self._release_screen()
