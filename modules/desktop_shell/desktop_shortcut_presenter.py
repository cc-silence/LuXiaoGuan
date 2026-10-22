"""First-launch and manual shortcut actions share one runtime-owned controller."""
from PySide6.QtCore import QObject, Signal
from modules.config_services.config_store import user_config_dir
from modules.common_widgets.common_dialogs import ConfirmDialog, MessageDialog
from modules.platform_services.desktop_shortcut import shortcut_state
from .desktop_shortcut_controller import DesktopShortcutController


class DesktopShortcutPresenter(QObject):
    activityChanged = Signal()
    startupFinished = Signal()

    def __init__(self, runtime, controller=None):
        super().__init__(runtime)
        self.runtime = runtime
        self.controller = controller or DesktopShortcutController(self)
        self.controller.inspected.connect(self._inspected)
        self.controller.completed.connect(self._completed)
        self.controller.failed.connect(self._failed)
        self.controller.activityChanged.connect(self.activityChanged)
        self._mode = ""
        self._startup_started = False
        self._startup_pending = False
        self._stopping = False
        self._dialog = None

    @property
    def active(self):
        return self.controller.active

    @property
    def marker(self):
        return user_config_dir() / ".desktop-shortcut-prompted"

    def start_first_launch(self):
        if self._startup_started or self._stopping or self.runtime._exiting:
            return
        self._startup_started = True
        self._startup_pending = True
        try:
            prompted = self.marker.is_file()
        except OSError as exc:
            self._failed(str(exc))
            return
        if prompted:
            self._finish_startup()
            return
        self._mode = "startup"
        self.controller.inspect()

    def add_manually(self):
        if self.active or self._mode or self._stopping or self.runtime._exiting:
            return False
        self._mode = "manual"
        return self.controller.inspect()

    def _remember(self):
        try:
            self.marker.parent.mkdir(parents=True, exist_ok=True)
            self.marker.write_text("1\n", encoding="utf-8")
        except OSError as exc:
            self._message("\u65e0\u6cd5\u4fdd\u5b58\u9996\u6b21\u63d0\u793a\u8bb0\u5f55\uff1a\n" + str(exc))

    def _message(self, text):
        if self._stopping or self.runtime._exiting:
            return
        self._dialog = MessageDialog("\u684c\u9762\u5feb\u6377\u65b9\u5f0f", text, self.runtime.main_window)
        self._dialog.exec()
        self._dialog.deleteLater()
        self._dialog = None

    def _ask(self, text):
        self._dialog = ConfirmDialog("\u684c\u9762\u5feb\u6377\u65b9\u5f0f", text,
                                     self.runtime.main_window,
                                     yes_text="\u6dfb\u52a0", no_text="\u6682\u4e0d\u6dfb\u52a0")
        answer = self._dialog.exec() == ConfirmDialog.Accepted
        self._dialog.deleteLater()
        self._dialog = None
        return answer and not self._stopping and not self.runtime._exiting

    def _inspected(self, observed):
        if self._stopping or self.runtime._exiting:
            return
        state = shortcut_state(self.controller.spec, observed)
        if state == "valid":
            self._remember()
            if self._mode == "manual":
                self._message("\u684c\u9762\u5feb\u6377\u65b9\u5f0f\u5df2\u5b58\u5728\uff0c\u5e76\u4e14\u6307\u5411\u5f53\u524d\u7a0b\u5e8f\u3002")
            self._finish_startup()
            return
        if self._mode == "startup" or state == "foreign":
            text = ("\u684c\u9762\u4e0a\u7684\u201c\u8def\u5c0f\u7ba1\u201d\u5feb\u6377\u65b9\u5f0f\u6307\u5411\u5176\u4ed6\u7a0b\u5e8f\uff0c\u662f\u5426\u66ff\u6362\uff1f"
                    if state == "foreign" else
                    "\u662f\u5426\u5728\u684c\u9762\u6dfb\u52a0\u8def\u5c0f\u7ba1\u5feb\u6377\u65b9\u5f0f\uff1f\n\n\u4ee5\u540e\u4e5f\u53ef\u4ee5\u5728\u8bbe\u7f6e\u4e2d\u6dfb\u52a0\u6216\u4fee\u590d\u3002")
            accepted = self._ask(text)
            if self._stopping or self.runtime._exiting:
                return
            if self._mode == "startup":
                self._remember()
            if not accepted:
                self._finish_startup()
                return
        self._repairing = state == "outdated"
        self.controller.create(observed, replace_foreign=state == "foreign")

    def _completed(self, observed):
        if self._stopping or self.runtime._exiting:
            return
        self._remember()
        message = ("\u684c\u9762\u5feb\u6377\u65b9\u5f0f\u5df2\u4fee\u590d\u3002" if self._repairing
                   else "\u684c\u9762\u5feb\u6377\u65b9\u5f0f\u5df2\u521b\u5efa\u3002")
        self._message(message + "\n" + str(self.controller.spec.path))
        self._finish_startup()

    def _failed(self, error):
        self._message("\u6dfb\u52a0\u684c\u9762\u5feb\u6377\u65b9\u5f0f\u5931\u8d25\uff1a\n" + error)
        self._finish_startup()

    def _finish_startup(self):
        pending, self._startup_pending = self._startup_pending, False
        self._mode = ""
        if pending and not self._stopping and not self.runtime._exiting:
            self.startupFinished.emit()

    def stop(self):
        self._stopping = True
        self._startup_pending = False
        if self._dialog is not None:
            self._dialog.reject()
        self.controller.cancel()
