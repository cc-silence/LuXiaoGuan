"""Refresh visible file panes after capture cleanup, without polling or interruption."""
from PySide6.QtCore import QObject, Slot


class CaptureFileRefresh(QObject):
    def __init__(self, page):
        super().__init__(page)
        self.page = page
        self.pending = None
        self.disposed = False
        self.connections = []
        auto = getattr(getattr(page.service, 'owner', None), 'auto_capture', None)
        if auto is not None:
            self._subscribe(auto.radioStateChanged, self.capture_finished)
        self._subscribe(page.service.completed, self.flush)
        self._subscribe(page.service.failed, self.flush)

    def _subscribe(self, signal, slot):
        signal.connect(slot)
        self.connections.append((signal,slot))

    @Slot(str, str)
    def capture_finished(self, radio, state):
        if state not in {'idle','error'} or self.disposed:
            return
        ssh = self.page.service.manager.ssh
        self.pending = ssh._client if ssh.connected else None
        self.flush()

    @Slot()
    def flush(self):
        page = self.page
        if self.disposed or self.pending is None:
            return
        ssh = page.service.manager.ssh
        if not ssh.connected or ssh._client is not self.pending:
            self.pending = None
            return
        auto = getattr(getattr(page.service, 'owner', None), 'auto_capture', None)
        if not page.isVisible() or page.pending or (auto is not None and auto.is_busy):
            return
        self.pending = None
        page._refresh(False)
        page._refresh(True)

    def dispose(self):
        self.disposed = True
        self.pending = None
        for signal,slot in self.connections:
            try:signal.disconnect(slot)
            except (TypeError,RuntimeError):pass
        self.connections.clear()
