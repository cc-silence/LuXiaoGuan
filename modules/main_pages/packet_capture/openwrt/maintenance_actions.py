"""File pickers and connection-bound maintenance confirmations, with no remote work on UI."""
from datetime import datetime
from pathlib import Path
from PySide6.QtWidgets import QFileDialog
from .maintenance_review_dialog import MaintenanceReviewDialog
from modules.common_widgets.common_dialogs import ConfirmDialog
from .settings_impacts import DISCONNECT_WARNING


class MaintenanceActions:
    def __init__(self, page):
        self.page = page
        self.pending = {}
        self.disposed = False
        self.progress_dialogs = {}
        progress_signal = getattr(page.manager, "progress", None)
        if progress_signal is not None: progress_signal.connect(self._progress)

    @property
    def active(self):
        return bool(self.pending)

    def _identity(self):
        ssh = self.page.manager.manager.ssh
        return ssh._client, ssh.host_fingerprint

    def _current(self, identity):
        return self.page.manager.manager.ssh.connected and identity == self._identity()

    def _destination(self, before=False):
        default = ("before-change-" if before else "openwrt-backup-") + datetime.now().strftime("%Y%m%d-%H%M%S") + ".tar.gz"
        path, _ = QFileDialog.getSaveFileName(self.page, "保存操作前备份" if before else "保存配置备份", default,
                                             "OpenWrt 配置备份 (*.tar.gz)")
        if not path:
            return None
        if not path.lower().endswith((".tar.gz", ".tgz")):
            path += ".tar.gz"
        destination = Path(path)
        if destination.is_symlink():
            raise ValueError("请选择普通文件路径，不覆盖符号链接。")
        from modules.capture_services.openwrt.router_management import RouterManagement
        expected = RouterManagement.identity(destination.lstat()) if destination.exists() else None
        return path, expected

    def start(self, operation):
        page = self.page
        if self.active or page.capture_service.is_busy or not page.manager.manager.ssh.connected:
            return
        identity = self._identity()
        try:
            if operation == "reboot":
                dialog = ConfirmDialog("确认重启路由器", DISCONNECT_WARNING +
                    "\n\n请先保存配置，并确认已结束其他路由器任务。", page,
                    yes_text="确认重启", no_text="取消")
                try:
                    if dialog.exec() != dialog.Accepted or not self._current(identity):
                        return
                finally:
                    dialog.deleteLater()
                key = page.manager.submit("maintenance_reboot")
                self.pending[key] = ("done", identity)
                page._message("正在发送重启请求；连接断开属于预期情况，请等待后重新登录。")
            elif operation == "backup":
                picked = self._destination()
                if picked is None or not self._current(identity):
                    return
                path, expected = picked
                key = page.manager.submit("maintenance_backup", destination=path, expected=expected)
                self.pending[key] = ("done", identity)
                self._show_progress(key)
                page._message("正在生成并校验配置备份，请等待。")
            else:
                source = ""
                if operation == "restore":
                    source, _ = QFileDialog.getOpenFileName(page, "选择配置备份", "", "OpenWrt 配置备份 (*.tar.gz *.tgz)")
                    if not source or not self._current(identity):
                        return
                key = page.manager.submit("maintenance_preview", kind=operation, source=source)
                self.pending[key] = ("preview", identity)
                page._message("正在检查设备能力和备份内容，尚未修改路由器。")
        except Exception as exc:
            from modules.capture_services.management_diagnostics import record_management_error
            record_management_error(page.manager.manager.runner,"maintenance_validation",operation,exc)
            page._message(str(exc), "error")
        page._sync()

    def completed(self, key, result):
        item = self.pending.pop(key, None)
        if item is None:
            return False
        self._finish_progress(key)
        page = self.page
        try:
            if self.disposed:
                return True
            phase, identity = item
            if phase == "done":
                page._message(result["note"])
                return True
            if not self._current(identity):
                page._message("连接已变化，请重新打开操作。")
                return True
            picked = self._destination(before=True)
            if picked is None or not self._current(identity):
                return True
            backup_path, expected = picked
            if result["operation"] == "restore" and Path(backup_path).resolve() == Path(result["source"]).resolve():
                raise ValueError("操作前备份不能覆盖即将恢复的源文件，请选择另一个路径。")
            dialog = MaintenanceReviewDialog(result, backup_path, page)
            try:
                if dialog.exec() != dialog.Accepted:
                    return True
                if not self._current(identity):
                    page._message("连接已变化，未执行恢复或重置。")
                    return True
                new_key = page.manager.submit("maintenance_apply", review=result,
                                             backup_path=backup_path, backup_expected=expected)
                self.pending[new_key] = ("done", identity)
                self._show_progress(new_key)
                page._message("正在备份当前配置；校验通过后执行所确认的操作，请保持供电。")
            finally:
                dialog.deleteLater()
        except Exception as exc:
            from modules.capture_services.management_diagnostics import record_management_error
            record_management_error(page.manager.manager.runner,"maintenance_validation",result.get("operation","read"),exc)
            page._message(str(exc), "error")
        finally:
            page._sync()
        return True

    def failed(self, key, message):
        if self.pending.pop(key, None) is None:
            return False
        self._finish_progress(key)
        if not self.disposed:
            self.page._message(message, "error")
            self.page._sync()
        return True

    def _show_progress(self, key):
        from .router_task_progress import RouterTaskProgressDialog
        dialog = RouterTaskProgressDialog("\u8def\u7531\u5668\u5907\u4efd\u4e0e\u7ef4\u62a4", lambda: self.page.manager.cancel(key), self.page)
        self.progress_dialogs[key] = dialog
        dialog.show()

    def _progress(self, key, value):
        dialog = self.progress_dialogs.get(key)
        if dialog is not None: dialog.update_progress(value)

    def _finish_progress(self, key):
        dialog = self.progress_dialogs.pop(key, None)
        if dialog is not None: dialog.finish()

    def dispose(self):
        self.disposed = True
        for key in tuple(self.pending):
            self.page.manager.cancel(key)
        self.pending.clear()
        for key in tuple(self.progress_dialogs): self._finish_progress(key)
        signal = getattr(self.page.manager, "progress", None)
        if signal is not None: signal.disconnect(self._progress)
