"""One bounded asynchronous shell operation shared by shortcut entry points."""
import json
import logging
from uuid import uuid4
from PySide6.QtCore import QObject, QProcess, QTimer, Signal
from modules.platform_services.desktop_shortcut import shortcut_spec, shell_arguments, shortcut_state


class DesktopShortcutController(QObject):
    activityChanged = Signal()
    inspected = Signal(object)
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, parent=None, timeout_ms=10000):
        super().__init__(parent)
        self.process = None
        self.spec = None
        self._temporary = None
        self._operation = ""
        self._error = ""
        self._cancelled = False
        self._stdout = bytearray()
        self._stderr = bytearray()
        self.deadline = QTimer(self)
        self.deadline.setSingleShot(True)
        self.deadline.setInterval(timeout_ms)
        self.deadline.timeout.connect(self._timeout)

    @property
    def active(self):
        return self.process is not None

    def inspect(self, spec=None):
        if self.active:
            return False
        try:
            self.spec = spec or shortcut_spec()
            return self._start("inspect")
        except (OSError, ValueError) as exc:
            self.failed.emit(str(exc))
            return False

    def create(self, observed, replace_foreign=False):
        if self.active or self.spec is None:
            return False
        try:
            return self._start("create", observed, replace_foreign)
        except (OSError, ValueError) as exc:
            self.failed.emit(str(exc))
            return False

    def _start(self, operation, observed=None, replace_foreign=False):
        temporary = self.spec.path.with_name(".luxiaoguan-shortcut-" + uuid4().hex + ".lnk")
        arguments = shell_arguments(self.spec, operation, observed, replace_foreign, temporary)
        self._temporary = temporary if operation == "create" else None
        self._operation, self._error, self._cancelled = operation, "", False
        self._stdout.clear()
        self._stderr.clear()
        process = self.process = QProcess(self)
        process.readyReadStandardOutput.connect(self._read_stdout)
        process.readyReadStandardError.connect(self._read_stderr)
        process.errorOccurred.connect(self._process_error)
        process.finished.connect(self._finished)
        process.setProgram("powershell.exe")
        process.setArguments(arguments)
        self.activityChanged.emit()
        self.deadline.start()
        process.start()
        return True

    def _collect(self, buffer, data):
        if len(buffer) + len(data) > 65536:
            self._error = "\u5feb\u6377\u65b9\u5f0f\u64cd\u4f5c\u8fd4\u56de\u5185\u5bb9\u8fc7\u5927\u3002"
            self.process.kill()
        else:
            buffer.extend(data)

    def _read_stdout(self):
        if self.process is not None:
            self._collect(self._stdout, bytes(self.process.readAllStandardOutput()))

    def _read_stderr(self):
        if self.process is not None:
            self._collect(self._stderr, bytes(self.process.readAllStandardError()))

    def _process_error(self, error):
        if self.process is not None and error == QProcess.FailedToStart:
            self._error = self.process.errorString()
            self._finished(-1, QProcess.CrashExit)

    def _timeout(self):
        if self.process is not None:
            self._error = "\u5feb\u6377\u65b9\u5f0f\u64cd\u4f5c\u8d85\u65f6\uff0c\u8bf7\u91cd\u8bd5\u3002"
            self.process.kill()

    def cancel(self):
        if self.process is not None:
            self._cancelled = True
            self.process.kill()

    def _finished(self, code, status):
        if self.process is None:
            return
        self.deadline.stop()
        self._read_stdout()
        self._read_stderr()
        process, self.process = self.process, None
        process.deleteLater()
        result, error = None, self._error
        try:
            if not self._cancelled and not error:
                if code != 0 or status != QProcess.NormalExit:
                    raise OSError(self._stderr.decode("utf-8", errors="replace")[-2000:]
                                  or "\u65e0\u6cd5\u5b8c\u6210\u5feb\u6377\u65b9\u5f0f\u64cd\u4f5c\u3002")
                result = json.loads(self._stdout.decode("utf-8-sig"))
                if not isinstance(result, dict) or type(result.get("exists")) is not bool:
                    raise ValueError("Invalid shortcut response")
                if self._operation == "create" and shortcut_state(self.spec, result) != "valid":
                    raise ValueError("Saved shortcut does not match the application")
        except (OSError, ValueError, UnicodeError) as exc:
            error = str(exc)
        self._stdout.clear()
        self._stderr.clear()
        if self._temporary is not None:
            try:
                self._temporary.unlink(missing_ok=True)
            except OSError as exc:
                logging.getLogger("routermanager.shortcut").error("Shortcut staging cleanup failed: %s", exc)
                error = (error + "; " if error else "") + str(exc)
            self._temporary = None
        # Result callbacks may immediately start the next operation.
        if not self._cancelled:
            if error:
                self.failed.emit(error)
            elif self._operation == "inspect":
                self.inspected.emit(result)
            else:
                self.completed.emit(result)
        self.activityChanged.emit()
