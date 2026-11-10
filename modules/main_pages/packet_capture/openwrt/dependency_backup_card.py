"""Dependency backup card using shared themed cards, dialogs and progress."""
from pathlib import Path
from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import QFileDialog, QHBoxLayout
from modules.common_widgets.common_dialogs import ConfirmDialog, MessageDialog
from modules.main_pages.page_action_buttons import create_page_text_button
from modules.main_pages.packet_capture.ui_helpers import create_capture_card
from .router_task_progress import RouterTaskProgressDialog
from .backup_filenames import dependency_backup_filename


class DependencyBackupCard(QObject):
    def __init__(self, parent, owner):
        super().__init__(parent)
        self.owner = owner
        self.disposed = False
        self.progress_dialog = None
        self.confirm_dialog = None
        self.last_directory = str(Path.home())
        from modules.capture_services.dependency_backup_service import DependencyBackupService
        self.service = getattr(owner, "dependency_backup", None)
        if self.service is None:
            self.service = DependencyBackupService(owner)
            owner.dependency_backup = self.service
        self.card, layout = create_capture_card(
            parent, "\u5907\u4efd\u4e0e\u6062\u590d",
            "\u5907\u4efd\u672c\u673a\u5df2\u5b89\u88c5\u7684\u8def\u5c0f\u7ba1\u4f9d\u8d56\u53ca\u9644\u5c5e\u4f9d\u8d56\u3002"
            "\u4ec5\u7528\u4e8e\u540c\u4e00\u53f0\u8def\u7531\u5668\u3001\u540c\u4e00\u56fa\u4ef6\u7684\u79bb\u7ebf\u6062\u590d\uff1b\u5df2\u6ee1\u8db3\u7684\u4f9d\u8d56\u81ea\u52a8\u8df3\u8fc7\u3002")
        buttons = QHBoxLayout()
        self.backup_button = create_page_text_button(self.card, "\u5907\u4efd\u4f9d\u8d56", "")
        self.restore_button = create_page_text_button(self.card, "\u6062\u590d\u4f9d\u8d56", "")
        for button in (self.backup_button, self.restore_button):
            button.setObjectName("captureSecondaryButton")
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.backup_button.clicked.connect(self._backup)
        self.restore_button.clicked.connect(self._restore)
        owner.openwrtSnapshotChanged.connect(self._sync)
        owner.activityChanged.connect(self._sync)
        self.service.changed.connect(self._sync)
        self.service.progress.connect(self._progress)
        self.service.completed.connect(self._completed)
        self.service.failed.connect(self._failed)
        self._sync()
        if self.service.active:
            self._start_progress("\u4f9d\u8d56\u5907\u4efd\u4e0e\u6062\u590d")
            if self.service.last_progress:
                self._progress(self.service.last_progress)

    def _sync(self, *_):
        enabled = (self.owner.openwrt_snapshot.connection_state.value == "connected"
                   and not self.owner.is_busy and not self.service.active)
        for button in (self.backup_button, self.restore_button):
            button.setEnabled(enabled)

    def _backup(self):
        device = getattr(self.owner.openwrt_snapshot, "device", None)
        suggested = str(Path(self.last_directory) / dependency_backup_filename(device))
        path, _ = QFileDialog.getSaveFileName(self.card, "\u5907\u4efd\u4f9d\u8d56", suggested, "ZIP (*.zip)")
        if not path:
            return
        if Path(path).suffix.lower() != ".zip":
            path += ".zip"
        if Path(path).exists():
            self._message("\u8bf7\u4f7f\u7528\u65b0\u6587\u4ef6\u540d\uff0c\u907f\u514d\u8986\u76d6\u539f\u6709\u5907\u4efd\u3002")
            return
        self.last_directory = str(Path(path).parent)
        self._start_progress("\u5907\u4efd\u4f9d\u8d56")
        self.service.backup(path)

    def _restore(self):
        path, _ = QFileDialog.getOpenFileName(self.card, "\u9009\u62e9\u4f9d\u8d56\u5907\u4efd", self.last_directory, "ZIP (*.zip)")
        if path:
            self.last_directory = str(Path(path).parent)
            self._start_progress("\u68c0\u67e5\u4f9d\u8d56\u5907\u4efd")
            self.service.preview(path)

    def _start_progress(self, title):
        self._finish_progress()
        self.progress_dialog = RouterTaskProgressDialog(title, self.service.cancel, self.card)
        self.progress_dialog.show()

    def _finish_progress(self):
        if self.progress_dialog is not None:
            dialog, self.progress_dialog = self.progress_dialog, None
            dialog.finish()

    def _progress(self, value):
        if self.progress_dialog is not None:
            self.progress_dialog.update_progress(value)

    def _completed(self, result):
        self._finish_progress()
        if self.disposed:
            return
        if result["action"] == "preview":
            if not result["missing"]:
                self._message("\u5907\u4efd\u4e2d\u7684\u4f9d\u8d56\u5747\u5df2\u5b89\u88c5\uff0c\u65e0\u9700\u91cd\u590d\u6062\u590d\u3002")
                return
            message = ("\u5c06\u79bb\u7ebf\u8865\u88c5 %d \u4e2a\u4f9d\u8d56\uff0c\u8df3\u8fc7 %d \u4e2a\u5df2\u5b89\u88c5\u4f9d\u8d56\u3002\n" %
                       (len(result["missing"]), len(result["skipped"])))
            message += "\u3001".join(result["missing"][:20])
            if len(result["missing"]) > 20:
                message += "\u2026"
            message += ("\n\n\u8bf7\u4ec5\u6062\u590d\u81ea\u5df1\u7684\u53ef\u4fe1\u5907\u4efd\u3002"
                        "\u5b89\u88c5\u5f00\u59cb\u540e\u8bf7\u52ff\u65ad\u7535\uff0c\u9700\u7b49\u5f85\u5b8c\u6210\u3002")
            if result["manager"] == "apk":
                message += "\n\u672c\u6b21\u6062\u590d\u5141\u8bb8\u81ea\u5907 APK \u5b89\u88c5\u5305\uff0c\u4e0d\u4fee\u6539\u7cfb\u7edf\u7684\u7b7e\u540d\u6821\u9a8c\u8bbe\u7f6e\u3002"
            dialog = self.confirm_dialog = ConfirmDialog("\u786e\u8ba4\u6062\u590d\u4f9d\u8d56", message, self.card)
            dialog.accepted.connect(lambda: self._confirmed(result))
            dialog.finished.connect(lambda _: self._close_confirm(dialog))
            dialog.open()
        elif result["action"] == "backup":
            self._message("\u4f9d\u8d56\u5907\u4efd\u5b8c\u6210\uff1a%d \u4e2a\u4f9d\u8d56\uff08\u542b\u56fa\u4ef6\u81ea\u5e26\u4f9d\u8d56\u8bb0\u5f55\uff09\u3002\n%s\n\n"
                          "\u5907\u4efd\u53ef\u80fd\u5305\u542b\u5de5\u5177\u81ea\u8eab\u914d\u7f6e\uff0c\u8bf7\u59a5\u5584\u4fdd\u7ba1\u3002" %
                          (result["count"], result["path"]))
        else:
            self._message("\u6062\u590d\u5b8c\u6210\uff1a\u8865\u88c5 %d \u4e2a\uff0c\u8df3\u8fc7 %d \u4e2a\u5df2\u5b89\u88c5\u4f9d\u8d56\u3002" %
                          (len(result["installed"]), len(result["skipped"])))

    def _confirmed(self, result):
        self._start_progress("\u6062\u590d\u4f9d\u8d56")
        self.service.restore(result)

    def _close_confirm(self, dialog):
        if self.confirm_dialog is dialog:
            self.confirm_dialog = None
        dialog.deleteLater()

    def _failed(self, text):
        if self.progress_dialog is not None:
            dialog, self.progress_dialog = self.progress_dialog, None
            dialog.finish(False, "\u4f9d\u8d56\u5907\u4efd\u6216\u6062\u590d\u672a\u5b8c\u6210\uff1a\n" + text)
        elif not self.disposed:
            self._message(text)

    def _message(self, text):
        dialog = MessageDialog("\u4f9d\u8d56\u5907\u4efd\u4e0e\u6062\u590d", text, self.card)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.open()

    def set_light_theme(self, enabled):
        self.card.light_theme = bool(enabled)
        for button in (self.backup_button, self.restore_button):
            button.set_light_theme(bool(enabled))

    def dispose(self):
        self.disposed = True
        # A page disappearing must not cancel resident router work.
        for signal, slot in ((self.owner.openwrtSnapshotChanged, self._sync),
                             (self.owner.activityChanged, self._sync),
                             (self.service.changed, self._sync), (self.service.progress, self._progress),
                             (self.service.completed, self._completed), (self.service.failed, self._failed)):
            signal.disconnect(slot)
        self._finish_progress()
        if self.confirm_dialog is not None:
            self.confirm_dialog.reject()

