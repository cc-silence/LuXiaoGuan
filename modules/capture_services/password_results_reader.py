"""Cancellable, page-bounded local results reads; no router or task-slot use."""
from pathlib import Path
from threading import Event
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot


class _ReadSignals(QObject):
    completed = Signal(object)
    failed = Signal(str)
    finished = Signal()


class _ReadTask(QRunnable):
    def __init__(self, reader, found, page):
        super().__init__()
        self.reader, self.found, self.page = reader, found, page
        self.signals = _ReadSignals()

    def run(self):
        reader = self.reader
        try:
            from .openwrt.password_history import PasswordAttemptHistory
            with PasswordAttemptHistory() as history:
                if reader.cancel.is_set():
                    return
                if not reader.imported:
                    history.catalog.import_results(reader.directory, reader.cancel)
                    reader.imported = True
                for target in reader.targets:
                    if reader.cancel.is_set():
                        return
                    key = history.catalog.identity(target)
                    if history.connection.execute('SELECT 1 FROM attempts WHERE target=? LIMIT 1', (key,)).fetchone():
                        history.catalog.remember(target)
                history.connection.commit()
                result = history.catalog.query(found=self.found, page=self.page, cancel=reader.cancel)
            if not reader.cancel.is_set():
                self.signals.completed.emit(result)
        except Exception:
            self.signals.failed.emit('\u8bfb\u53d6\u8bb0\u5f55\u5931\u8d25\uff0c\u8bf7\u91cd\u8bd5\u3002')
        finally:
            self.signals.finished.emit()


class PasswordResultsReader(QObject):
    completed = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, owner, directory, targets):
        super().__init__(owner)
        self.directory = Path(directory)
        self.targets = tuple(targets)[:10000]
        self.cancel = Event()
        self.destroyed.connect(self.cancel.set)
        owner._password_results_reader = self
        self.imported = False
        self.busy = False
        self.closed = False
        self.finished.connect(self._finish)

    def load(self, found=False, page=0):
        if self.busy or self.closed:
            return False
        self.cancel.clear()
        self.busy = True
        task = _ReadTask(self, found, page)
        task.signals.completed.connect(self.completed)
        task.signals.failed.connect(self.failed)
        task.signals.finished.connect(self.finished)
        QThreadPool.globalInstance().start(task)
        return True

    @Slot()
    def _finish(self):
        self.busy = False
        if self.closed:
            self.targets = ()
            self.deleteLater()

    def close(self):
        self.closed = True
        self.cancel.set()
        if not self.busy:
            self.targets = ()
            self.deleteLater()
