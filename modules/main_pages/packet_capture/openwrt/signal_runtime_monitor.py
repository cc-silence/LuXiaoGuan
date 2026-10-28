"""Visible configured-signal monitoring through the existing background service."""
from PySide6.QtCore import QObject, QTimer, QEvent


class SignalRuntimeMonitor(QObject):
    def __init__(self, page):
        super().__init__(page)
        self.page = page
        self._attempted_revision = None
        self._manual = False
        self._paused = False
        self._disposed = False
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(lambda: self._request(periodic=True))
        page.installEventFilter(self)
        page.stack.currentChanged.connect(self._navigation_changed)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Show:
            self._paused = False
            self._update_visibility()
        elif event.type() == QEvent.Hide:
            self.timer.stop()
        return False

    def _navigation_changed(self, index):
        if index == 0:
            self._paused = False
        self._update_visibility()

    def _update_visibility(self):
        if not self._disposed and self.page.isVisible() and self.page.stack.currentIndex() == 0 and not self._paused:
            self.timer.start()
            self._request()
        else:
            self.timer.stop()

    def invalidate(self):
        self._manual = True
        self._paused = False
        if self.page.isVisible() and self.page.stack.currentIndex() == 0:
            self.timer.start()

    def pause(self):
        self._paused = True
        self.timer.stop()

    def apply_snapshot(self, snapshot):
        if not snapshot.connected:
            self._attempted_revision = None
        self._request()

    def _request(self, periodic=False):
        page = self.page
        service = page.service
        snapshot = service.snapshot
        method = getattr(service, 'refresh_status', None)
        if (self._disposed or self._paused or not page.isVisible() or page.stack.currentIndex() != 0
                or method is None or not snapshot.connected or snapshot.busy or service.active
                or snapshot.stale or not snapshot.inventory.revision):
            return
        if not any(not r.disabled and not r.radio_disabled and r.mode in ("ap", "sta") and not r.reason
                   for r in snapshot.inventory.rows):
            return
        revision = snapshot.inventory.revision
        if not (periodic or self._manual or revision != self._attempted_revision):
            return
        # Set before dispatch because the service immediately broadcasts its busy snapshot.
        self._attempted_revision = revision
        force = self._manual
        self._manual = False
        method(force_internet=force)

    def dispose(self):
        self._disposed = True
        self.timer.stop()
        self.page.removeEventFilter(self)
