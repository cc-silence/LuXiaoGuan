"""\u81ea\u8eab\u66f4\u65b0\u7684\u7a97\u53e3\u63d0\u793a\uff1b\u4e0d\u6267\u884c\u7f51\u7edc\u6216\u66ff\u6362\u7a0b\u5e8f\u3002"""
from PySide6.QtCore import QDateTime, QEvent, QObject, Qt, QUrl, Signal, Slot
from PySide6.QtWidgets import QApplication
from modules.app_update_services.checker import UpdateChecker
from modules.config_manager import BASE_DIR


class AppUpdatePresenter(QObject):
    """\u8fd0\u884c\u65f6\u6301\u6709\u4e00\u4e2a\u68c0\u67e5\u670d\u52a1\uff0c\u9875\u9762\u91cd\u5efa\u4e0d\u91cd\u590d\u81ea\u52a8\u68c0\u67e5\u3002"""
    activityChanged = Signal()
    stateChanged = Signal()

    def __init__(self, current_version, runtime):
        super().__init__(runtime)
        self.runtime = runtime
        self.checker = UpdateChecker(current_version, BASE_DIR / "config" / "app_update.json", self)
        self.checker.completed.connect(self._completed)
        self.checker.failed.connect(self._failed)
        self.checker.activityChanged.connect(self._activity_changed)
        self._pending_test_code = None
        self._automatic_started = False
        self._release = None
        self._notified = ""
        self.dialog = None
        self._manual_owner = None
        self._inline_check = False
        self.state = "idle"
        self.error = ""
        self.last_checked = ""
        self.plan = None
        from modules.app_update_services.planner import FingerprintCache
        self._fingerprints = FingerprintCache()
        self._download_requested = False
        self._worker = None
        self._maintenance = None
        self._cancelled = False
        self.progress_text = ""
        self.download_completed = 0
        self.download_total = 0
        QApplication.instance().installEventFilter(self)
        import sys
        if getattr(sys,"frozen",False):
            self._start_cache_maintenance()

    def _start_cache_maintenance(self):
        from modules.app_update_services.maintenance import UpdateCacheMaintenance
        worker=UpdateCacheMaintenance(BASE_DIR,self.checker.current_version,self)
        self._maintenance=worker
        worker.finished.connect(self._cache_maintenance_finished,Qt.QueuedConnection)
        try:worker.start()
        except RuntimeError:
            self._maintenance=None
            worker.deleteLater()
            import logging
            logging.getLogger("routermanager.update").warning("Startup cache cleanup could not start")

    @Slot()
    def _cache_maintenance_finished(self):
        worker,self._maintenance=self._maintenance,None
        if worker is not None:worker.deleteLater()
        self.activityChanged.emit()
        if (not self.runtime._exiting and self._release is not None
                and self._release.catalog is not None and self.plan is None):
            self.prepare_plan()


    def eventFilter(self, watched, event):
        """\u5176\u4ed6\u5f39\u7a97\u5173\u95ed\u6216\u4e3b\u7a97\u53e3\u6062\u590d\u65f6\u5c55\u793a\u5f85\u63d0\u793a\u7684\u65b0\u7248\u672c\u3002"""
        if event.type() == QEvent.WindowActivate and watched is self.runtime.main_window:
            self._present_if_visible()
        return False

    @property
    def active(self):
        return self.checker.active or self._worker is not None or self._maintenance is not None

    def window_ready(self):
        """\u9632\u706b\u5899\u5c31\u7eea\u540e\u68c0\u67e5\uff0c\u4e0d\u4f7f\u7528\u5ef6\u65f6\u8865\u4e01\u6216\u91cd\u590d\u8f6e\u8be2\u3002"""
        if self.runtime._exiting:
            return
        if not self._automatic_started:
            import sys
            from modules.feature_policy import FeaturePolicy, CAPABILITY_NETWORK_DOWNLOAD
            if (not getattr(sys, "frozen", False)
                    or not FeaturePolicy.from_config(self.runtime.config).allows(CAPABILITY_NETWORK_DOWNLOAD)):
                return
            self._automatic_started = True
            self.checker.check()
        self._present_if_visible()

    def check_manually(self, owner=None, inline=False):
        """\u624b\u52a8\u68c0\u67e5\u4f7f\u7528\u540c\u4e00\u670d\u52a1\uff0c\u5e76\u660e\u786e\u53cd\u9988\u65e0\u65b0\u7248\u6216\u7f51\u7edc\u5931\u8d25\u3002"""
        if self.runtime._exiting or self._worker is not None:
            return
        from modules.feature_policy import FeaturePolicy, CAPABILITY_NETWORK_DOWNLOAD
        self._manual_owner = owner
        self._inline_check = inline
        if not FeaturePolicy.from_config(self.runtime.config).allows(CAPABILITY_NETWORK_DOWNLOAD):
            self._failed("\u4e3b\u673a\u6a21\u5f0f\u5df2\u5173\u95ed\u8054\u7f51\u4e0b\u8f7d\uff0c\u8bf7\u5148\u5728\u8bbe\u7f6e\u4e2d\u5207\u6362\u6a21\u5f0f\u3002", True)
            return
        self.checker.check(manual=True)

    @property
    def release(self):
        """只暴露已通过签名校验的新版本。"""
        return self._release

    def _activity_changed(self):
        """检查状态统一通知界面，不创建额外检查器。"""
        if self.active:
            self.state = "checking"
            self.error = ""
            self.stateChanged.emit()
        self.activityChanged.emit()

    def _owner(self):
        from shiboken6 import isValid
        if self._manual_owner is not None and isValid(self._manual_owner) and self._manual_owner.isVisible():
            return self._manual_owner
        window = self.runtime.main_window
        return window if window is not None and window.isVisible() else None

    def set_test_code(self, value, owner=None):
        """\u5185\u6d4b\u7801\u8054\u7f51\u9a8c\u8bc1\u6210\u529f\u540e\u624d\u4fdd\u5b58\uff0c\u9000\u51fa\u5185\u6d4b\u7acb\u5373\u64a4\u9500\u672c\u5730\u51ed\u636e\u3002"""
        if self.active:return
        from modules.platform_services.app_update_access import normalize_code,save_code
        try:
            value=normalize_code(value)
            if not value:save_code("")
        except (OSError,ValueError) as exc:
            self.error=str(exc);self.stateChanged.emit();return
        self._release=None;self.plan=None;self.checker.latest=None;self.checker.manifest_data=b""
        self._notified="";self.checker.test_code=value
        self._pending_test_code=value if value else None
        self.check_manually(owner,inline=True)

    def _completed(self, release, manual):
        if self._pending_test_code is not None:
            from modules.platform_services.app_update_access import save_code
            try:save_code(self._pending_test_code)
            except (OSError,ValueError):
                self._failed("\u5185\u6d4b\u7801\u9a8c\u8bc1\u6210\u529f\uff0c\u4f46\u672c\u673a\u51ed\u636e\u4fdd\u5b58\u5931\u8d25\uff0c\u8bf7\u91cd\u8bd5\u3002",manual)
                return
            self._pending_test_code=None
        self._release = release
        self.plan = None
        self.state = "available" if release is not None else "current"
        self.error = ""
        self.last_checked = QDateTime.currentDateTime().toString("yyyy-MM-dd HH:mm:ss")
        self.stateChanged.emit()
        if self._inline_check:
            if release is not None:
                self._notified = release.version
        elif release is not None:
            self._present_if_visible(force=manual)
        elif manual:
            self._message("\u5f53\u524d\u5df2\u662f\u6700\u65b0\u7248\u672c\u3002")
        self._manual_owner = None
        self._inline_check = False
        if release is not None and release.catalog is not None:
            self.prepare_plan()

    @property
    def latest_release(self):
        return self.checker.latest or self._release

    def prepare_plan(self, download=False):
        """计划和下载共用后台所有者，不在页面打开时扫描磁盘。"""
        if download and self._worker is not None and not self._worker.download:
            self._download_requested = True
            self.stateChanged.emit()
            return
        if self.active or self._release is None or self._release.catalog is None:
            return
        from modules.app_update_services.transfer import UpdateTransfer
        self._cancelled = False
        self.error = ""
        self.state = "downloading" if download else "planning"
        self.download_completed = 0
        self.download_total = 0
        self.progress_text = ""
        worker = UpdateTransfer(BASE_DIR, self._release.catalog, self.checker.manifest_data, download, self, cache=self._fingerprints)
        self._worker = worker
        worker.preview.connect(self._transfer_preview)
        worker.result.connect(self._transfer_result)
        worker.failed.connect(self._transfer_failed)
        worker.progress.connect(self._transfer_progress)
        worker.finished.connect(self._transfer_finished)
        worker.start()
        self.stateChanged.emit()
        self.activityChanged.emit()

    def _transfer_preview(self, plan):
        self.plan = plan
        self.stateChanged.emit()

    def _transfer_result(self, plan):
        self.plan = plan
        if plan.get("stage"):
            self.download_completed = self.download_total = plan["download_bytes"]
        self.state = "ready" if plan.get("stage") else "available"
        self.stateChanged.emit()

    def _transfer_failed(self, message):
        self._download_requested = False
        self.state = "error"
        self.error = "\u66f4\u65b0\u51c6\u5907\u5931\u8d25\uff1a" + message
        self.stateChanged.emit()

    def _transfer_progress(self, done, total, path):
        self.download_completed = max(0,done)
        self.download_total = max(0,total)
        self.progress_text = f"{done / 1024 / 1024:.1f} / {total / 1024 / 1024:.1f} MB"
        self.stateChanged.emit()

    def _transfer_finished(self):
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.deleteLater()
        if self._cancelled:
            self.state = "available" if self._release is not None else "idle"
            self.progress_text = ""
        self.stateChanged.emit()
        self.activityChanged.emit()

        requested, self._download_requested = self._download_requested, False
        if requested and not self._cancelled and self.state == 'available' and self.plan and not self.plan['conflicts']:
            self.prepare_plan(download=True)

    def cancel_transfer(self):
        self._download_requested = False
        if self._worker is not None:
            self._cancelled = True
            self._worker.cancel()

    def install_update(self, owner):
        """只在用户确认且无其他任务时启动独立替换。"""
        if self.active or self.state != "ready" or not self.plan:
            return
        blockers = self.runtime.idle_blockers()
        if blockers:
            self.error = "\u8bf7\u5148\u5b8c\u6210\u6216\u505c\u6b62\u5176\u4ed6\u4efb\u52a1\uff1a" + "\uff1b".join(blockers)
            self.stateChanged.emit()
            return
        from modules.common_widgets.common_dialogs import ConfirmDialog
        dialog = ConfirmDialog("\u91cd\u542f\u5e76\u5b89\u88c5\u66f4\u65b0", "\u5c06\u9000\u51fa RouterManager\uff0c\u66ff\u6362\u5df2\u6838\u5bf9\u7684\u6587\u4ef6\u540e\u81ea\u52a8\u91cd\u65b0\u6253\u5f00\u3002\n\u5df2\u4fdd\u5b58\u7684\u7528\u6237\u914d\u7f6e\u548c\u6570\u636e\u4f1a\u4fdd\u7559\uff1b\u8bf7\u5148\u4fdd\u5b58\u5c1a\u672a\u786e\u8ba4\u7684\u8bbe\u7f6e\u3002", owner)
        if not dialog.exec():
            return
        try:
            from modules.platform_services.app_update_install import launch_helper
            from modules.ui_scale_manager import UiScaleManager
            appearance={"theme":self.runtime.config.get("ui","theme",fallback="dark.qss"),
                        "scale":UiScaleManager.for_widget(owner).global_scale}
            launch_helper(BASE_DIR, self.plan["stage"], appearance=appearance)
        except Exception as exc:
            self.error = str(exc)
            self.stateChanged.emit()
            return
        self.runtime.exit_application()

    def _open_center(self, dialog):
        dialog.accept()
        window = self.runtime.main_window
        if window is not None and hasattr(window, "openSettingsWindow"):
            window.openSettingsWindow(update=True)

    def _failed(self, message, manual):
        if self._pending_test_code is not None:
            from modules.platform_services.app_update_access import read_code
            self.checker.test_code=read_code();self._pending_test_code=None
        self.state = "error"
        self.error = message
        self.stateChanged.emit()
        if manual and not self._inline_check:
            self._message(message)
        self._manual_owner = None
        self._inline_check = False

    def _message(self, message):
        from modules.common_widgets.common_dialogs import MessageDialog
        owner = self._owner()
        if owner is None or self.dialog is not None:
            return
        self.dialog = MessageDialog("\u68c0\u67e5 RouterManager \u66f4\u65b0", message, owner)
        self._open_dialog()

    def _open_dialog(self):
        self.dialog.setAttribute(Qt.WA_DeleteOnClose)
        self.dialog.finished.connect(self._closed)
        self.dialog.open()

    def _closed(self, _result):
        self.dialog = None

    def _present_if_visible(self, force=False):
        owner = self._owner()
        release = self._release
        if (owner is None or release is None or self.dialog is not None
                or (not force and self._notified == release.version)
                or (not force and QApplication.activeModalWidget() is not None)):
            return
        from PySide6.QtWidgets import QLabel, QTextEdit
        from modules.common_widgets.frameless_dialog import FramelessDialog
        from modules.common_widgets.common_dialogs import apply_common_dialog_theme
        from modules.settings_pages.settings_widgets import create_settings_button
        self._notified = release.version
        dialog = FramelessDialog("\u53d1\u73b0 RouterManager \u65b0\u7248\u672c", owner, inherit_parent_stylesheet=False)
        apply_common_dialog_theme(dialog, owner)
        dialog.set_scaled_size((500, 320), (640, 440))
        label = QLabel(f"RouterManager v{self.checker.current_version}  \u2192  v{release.version}", dialog)
        label.setTextFormat(Qt.PlainText)
        dialog.content_layout.addWidget(label)
        notes = QTextEdit(dialog)
        notes.setObjectName("settingsMessageText")
        notes.setReadOnly(True)
        notes.setPlainText(release.notes or "\u6b64\u7248\u672c\u672a\u63d0\u4f9b\u66f4\u65b0\u8bf4\u660e\u3002")
        dialog.content_layout.addWidget(notes, 1)
        hint_text = ("\u53ef\u5728\u66f4\u65b0\u4e2d\u5fc3\u67e5\u770b\u5168\u90e8\u8de8\u7248\u672c\u53d8\u5316\u548c\u4e0b\u8f7d\u5927\u5c0f\uff0c\u786e\u8ba4\u540e\u518d\u4e0b\u8f7d\u3002\n"
                     "\u7528\u6237\u914d\u7f6e\u548c\u6570\u636e\u4f1a\u4fdd\u7559\u3002" if release.catalog else
                     f"\u5b8c\u6574\u5305 {release.size / 1024 / 1024:.1f} MB\uff1b\u8bf7\u524d\u5f80\u66f4\u65b0\u4e2d\u5fc3\u4e0b\u8f7d\u3002")
        hint = QLabel(hint_text, dialog)
        hint.setWordWrap(True)
        dialog.content_layout.addWidget(hint)
        download = create_settings_button("\u4e0b\u8f7d\u65b0\u7248", dialog, "primary")
        download.setToolTip("SHA-256: " + release.sha256)
        download.clicked.connect(lambda: self._open_center(dialog))
        download.setText("\u67e5\u770b\u66f4\u65b0")
        later = create_settings_button("\u7a0d\u540e\u518d\u8bf4", dialog, "secondary")
        later.clicked.connect(dialog.reject)
        dialog.button_layout.addWidget(download)
        dialog.button_layout.addWidget(later)
        self.dialog = dialog
        self._open_dialog()

    def download_release(self):
        """从同一已验签结果打开浏览器，失败时保留重试入口。"""
        from PySide6.QtGui import QDesktopServices
        if self._release is None or self.active:
            return False
        if QDesktopServices.openUrl(QUrl(self._release.url)):
            return True
        self.state = "error"
        self.error = "\u65e0\u6cd5\u6253\u5f00\u6d4f\u89c8\u5668\uff0c\u8bf7\u68c0\u67e5\u9ed8\u8ba4\u6d4f\u89c8\u5668\u540e\u91cd\u8bd5\u3002"
        self.stateChanged.emit()
        return False

    def _download(self, release, dialog):
        if release is self._release and self.download_release():
            dialog.accept()

    def stop(self):
        """\u9000\u51fa\u65f6\u4e2d\u6b62\u68c0\u67e5\u5e76\u5173\u95ed\u63d0\u793a\uff0c\u4e0d\u4fdd\u7559\u9875\u9762\u5f15\u7528\u3002"""
        self.checker.cancel()
        if self._maintenance is not None:self._maintenance.requestInterruption()
        self.cancel_transfer()
        self._manual_owner = None
        self._release = None
        if self.dialog is not None:
            self.dialog.reject()


def check_app_updates(owner):
    """\u8bbe\u7f6e\u9875\u4e0e\u6258\u76d8\u5171\u7528\u7684\u624b\u52a8\u5165\u53e3\u3002"""
    app = QApplication.instance()
    presenter = getattr(app, "routermanager_app_updates", None)
    if presenter is not None:
        presenter.check_manually(owner)
    else:
        from modules.common_widgets.common_dialogs import MessageDialog
        dialog = MessageDialog("\u68c0\u67e5 RouterManager \u66f4\u65b0", "\u8bf7\u4ece RouterManager \u6b63\u5f0f\u542f\u52a8\u5165\u53e3\u68c0\u67e5\u66f4\u65b0\u3002", owner)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.open()
