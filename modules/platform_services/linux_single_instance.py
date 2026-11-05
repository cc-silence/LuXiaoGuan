"""Unix single-instance ownership; only the lease holder may remove a socket."""
import os
import stat
from pathlib import Path
from PySide6.QtCore import QObject, QLockFile, QStandardPaths
from PySide6.QtNetwork import QLocalServer, QLocalSocket


def instance_paths():
    root = Path(QStandardPaths.writableLocation(QStandardPaths.RuntimeLocation))
    if not root.is_absolute():
        raise OSError("A private desktop runtime directory is required.")
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise OSError("The desktop runtime directory has unsafe ownership or permissions.")
    directory = root / "RouterManager"
    directory.mkdir(mode=0o700, exist_ok=True)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise OSError("The application runtime directory must belong to this user and be private.")
    return str(directory / "control"), directory / "instance.lock"


class LinuxSingleInstance(QObject):
    def __init__(self, activate, parent=None):
        super().__init__(parent)
        self._activate = activate
        self._server = None
        self._lease = None
        self._name = ""

    def acquire(self):
        if self._lease is not None:
            return True
        name, lock_path = instance_paths()
        lease = QLockFile(str(lock_path))
        lease.setStaleLockTime(0)
        if not lease.tryLock(0):
            if lease.error() != QLockFile.LockFailedError:
                raise OSError("Cannot acquire the application instance lock.")
            # A competing owner may still be starting its listener. Never unlink it.
            self._notify(name)
            return False
        self._name, self._lease = name, lease
        try:
            QLocalServer.removeServer(name)
            server = self._server = QLocalServer(self)
            server.setSocketOptions(QLocalServer.UserAccessOption)
            server.setMaxPendingConnections(8)
            if not server.listen(name):
                raise OSError("Cannot listen for application activation: " + server.errorString())
            server.newConnection.connect(self._activated)
            return True
        except Exception:
            self.close()
            raise

    def _notify(self, name):
        socket = QLocalSocket(self)
        try:
            socket.connectToServer(name)
            if socket.waitForConnected(300):
                socket.disconnectFromServer()
        finally:
            socket.deleteLater()

    def _activated(self):
        while self._server is not None and self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            socket.disconnectFromServer()
            socket.deleteLater()
            self._activate()

    def close(self):
        if self._lease is None:
            return
        if self._server is not None:
            self._server.close()
            self._server.deleteLater()
            self._server = None
        # Release the listener while ownership is still held.
        QLocalServer.removeServer(self._name)
        lease, self._lease = self._lease, None
        lease.unlock()
