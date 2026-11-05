"""Bounded asynchronous credential helper, owned by the resident service."""
import json
from collections import deque
from dataclasses import dataclass
from PySide6.QtCore import QObject, QProcess, QTimer, Signal
from shiboken6 import isValid
from .credentials import CredentialError, MAX_BYTES, validate_request, worker_command


@dataclass
class _Request:
    payload: dict
    context: QObject
    callback: object


class CredentialController(QObject):
    activityChanged = Signal()

    def __init__(self, parent=None, *, timeout_ms=10000, command=None):
        super().__init__(parent)
        self._command = command or worker_command
        self._queue = deque()
        self._current = None
        self.process = None
        self._output = bytearray()
        self._error = None
        self._stopping = False
        self.deadline = QTimer(self)
        self.deadline.setSingleShot(True)
        self.deadline.setInterval(timeout_ms)
        self.deadline.timeout.connect(self._timeout)

    @property
    def active(self):
        return self.process is not None or bool(self._queue)

    def submit(self, operation, target, username, password, context, callback):
        if self._stopping or len(self._queue) >= 8:
            raise CredentialError("unavailable")
        payload = validate_request(dict(operation=operation, target=target,
                                        username=username, password=password))
        if len(json.dumps(payload, ensure_ascii=True).encode("utf-8")) > MAX_BYTES:
            raise CredentialError("invalid")
        self._queue.append(_Request(payload, context, callback))
        self._start_next()

    def _start_next(self):
        if self.process is not None or self._stopping:
            return
        while self._queue:
            request = self._queue.popleft()
            if not isValid(request.context):
                request.payload.clear()
                continue
            self._current = request
            self._output.clear()
            self._error = None
            process = self.process = QProcess(self)
            command = self._command()
            process.setProgram(command[0])
            process.setArguments(command[1:])
            process.started.connect(self._send)
            process.readyReadStandardOutput.connect(self._read)
            # Diagnostics from storage libraries may contain sensitive content.
            process.setStandardErrorFile(QProcess.nullDevice())
            process.errorOccurred.connect(self._process_error)
            process.finished.connect(self._finished)
            self.deadline.start()
            process.start()
            break
        self.activityChanged.emit()

    def _send(self):
        if self.process is None or self._current is None:
            return
        data = json.dumps(self._current.payload, ensure_ascii=True).encode("utf-8")
        self.process.write(data)
        self.process.closeWriteChannel()
        # Retain the operation only, never the password while awaiting a reply.
        operation = self._current.payload["operation"]
        self._current.payload.clear()
        self._current.payload["operation"] = operation

    def _read(self):
        if self.process is None:
            return
        data = bytes(self.process.readAllStandardOutput())
        if len(self._output) + len(data) > MAX_BYTES:
            self._error = CredentialError("verification")
            self.process.kill()
        else:
            self._output.extend(data)

    def _timeout(self):
        if self.process is not None:
            self._error = CredentialError("timeout")
            self.process.kill()

    def _process_error(self, error):
        if self.process is not None and error == QProcess.FailedToStart:
            self._error = CredentialError("unavailable")
            self._finished(-1, QProcess.CrashExit)

    def _finished(self, code, status):
        if self.process is None:
            return
        self.deadline.stop()
        self._read()
        process, self.process = self.process, None
        request, self._current = self._current, None
        process.deleteLater()
        value, error = None, self._error
        try:
            if error is None and not self._stopping:
                reply = json.loads(self._output)
                if not isinstance(reply, dict) or type(reply.get("ok")) is not bool:
                    raise ValueError()
                if not reply["ok"]:
                    raise CredentialError(reply.get("code"))
                if code != 0 or status != QProcess.NormalExit:
                    raise ValueError()
                value = reply["value"]
                if request.payload["operation"] == "read":
                    if not isinstance(value, str) or len(value) > 4096 or "\0" in value:
                        raise ValueError()
                elif value is not True:
                    raise ValueError()
        except CredentialError as exc:
            error = exc
        except (ValueError, KeyError, TypeError, UnicodeError):
            error = CredentialError("verification")
        self._output.clear()
        self._error = None
        request.payload.clear()
        try:
            if not self._stopping and isValid(request.context):
                request.callback(value, error)
        finally:
            self._start_next()
            self.activityChanged.emit()

    def shutdown(self):
        self._stopping = True
        while self._queue:
            self._queue.popleft().payload.clear()
        if self.process is not None:
            self.process.kill()
        self.activityChanged.emit()
