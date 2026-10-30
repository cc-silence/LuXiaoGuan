"""\u8def\u7531\u670d\u52a1\u548c\u66f4\u65b0\u670d\u52a1\u7684\u751f\u547d\u5468\u671f\u6240\u6709\u8005\u3002"""
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QApplication
from modules.config_manager import ConfigManager
from modules.capture_services.capture_service import CaptureService
from modules.desktop_shell.app_update_presenter import AppUpdatePresenter
from modules.desktop_shell.startup_firewall_controller import StartupFirewallController
from modules.display_privacy import DisplayPrivacy


class RouterRuntime(QObject):
    """\u6301\u6709\u540e\u53f0\u4efb\u52a1\uff0c\u9000\u51fa\u65f6\u7b49\u5f85\u8d44\u6e90\u6536\u5c3e\uff0c\u4e0d\u963b\u585e\u754c\u9762\u3002"""
    logHistoryClosed = Signal()

    def __init__(self, version, parent=None):
        super().__init__(parent)
        self.main_window = None
        self._exiting = False
        self._ready_to_close = False
        self._log_export_service = None
        self._desktop_shortcuts = None
        self._history_closing = False
        self._history_closed = False
        self.logHistoryClosed.connect(self._history_close_finished)
        from modules.log_history import LogHistory, install_history
        self.log_history = LogHistory(version=version)
        install_history(self.log_history)
        QApplication.instance().aboutToQuit.connect(self._close_log_history)
        self.config_manager = ConfigManager()
        self.config = self.config_manager.load()
        self.capture_service = CaptureService(self)
        self.display_privacy = DisplayPrivacy(self)
        self.capture_service.openwrtSnapshotChanged.connect(self.display_privacy.register_snapshot)
        self.capture_service.router_signals.snapshotChanged.connect(self.display_privacy.register_snapshot)
        self.capture_service.openwrtSnapshotChanged.connect(self._update_log_privacy)
        self.capture_service.router_signals.snapshotChanged.connect(self._update_log_privacy)
        self._startup_firewall_controller = StartupFirewallController(self)
        self.capture_service.network_ready = self._startup_firewall_controller.wait_ready
        self.app_updates = AppUpdatePresenter(version, self)
        QApplication.instance().routermanager_app_updates = self.app_updates
        self.capture_service.activityChanged.connect(self._maybe_finish_exit)
        self.app_updates.activityChanged.connect(self._maybe_finish_exit)
        self._startup_firewall_controller.activity_changed.connect(self._maybe_finish_exit)
        self._startup_firewall_controller.finished.connect(self._firewall_finished)

    def _history_close_finished(self):
        self._history_closed = True
        self._maybe_finish_exit()

    def _drain_log_history(self):
        try:
            self.log_history.close()
        finally:
            self.logHistoryClosed.emit()

    def _update_log_privacy(self, snapshot):
        self.log_history.masker.identities = self.display_privacy.snapshot()

    def _close_log_history(self):
        from modules.log_history import install_history
        try:
            self.log_history.close()
        except (OSError, TimeoutError):
            pass
        install_history(None)

    @property
    def desktop_shortcuts(self):
        """Share one lazy shortcut controller across startup and settings."""
        if self._desktop_shortcuts is None:
            from modules.desktop_shell.desktop_shortcut_presenter import DesktopShortcutPresenter
            self._desktop_shortcuts = DesktopShortcutPresenter(self)
            self._desktop_shortcuts.activityChanged.connect(self._maybe_finish_exit)
            self._desktop_shortcuts.startupFinished.connect(self.app_updates.window_ready, Qt.QueuedConnection)
        return self._desktop_shortcuts

    @property
    def log_export_service(self):
        """\u5ef6\u8fdf\u521b\u5efa\u516c\u5171\u65e5\u5fd7\u5bfc\u51fa\u670d\u52a1\uff0c\u9000\u51fa\u65f6\u7b49\u5f85\u53d6\u6d88\u6536\u5c3e\u3002"""
        if self._log_export_service is None:
            from modules.log_services import LogExportService
            self._log_export_service = LogExportService(self)
            self._log_export_service.activityChanged.connect(self._maybe_finish_exit)
        return self._log_export_service

    def _firewall_finished(self, success, error):
        """\u653e\u884c\u68c0\u67e5\u901a\u8fc7\u540e\u4ea4\u7ed9\u539f\u66f4\u65b0\u5165\u53e3\u3002"""
        if success and not self._exiting:
            self.desktop_shortcuts.start_first_launch()

    def exit_tasks(self):
        from .exit_activity import exit_tasks
        return exit_tasks(self.capture_service)

    def idle_blockers(self):
        """\u5217\u51fa\u963b\u6b62\u66f4\u65b0\u5b89\u88c5\u7684\u6d3b\u52a8\uff0c\u4e0d\u6267\u884c\u8bbe\u5907\u64cd\u4f5c\u3002"""
        service = self.capture_service
        blockers = []
        if service.is_busy or service.router_terminal.active or service.router_management.current or service.router_management.queue:
            blockers.append("\u8def\u7531\u5668\u4efb\u52a1\u4ecd\u5728\u8fd0\u884c")
        for name in ("common_bundle_download", "capture_bundle_download"):
            task = getattr(service, name, None)
            if task is not None and task.active:
                blockers.append("\u79bb\u7ebf\u8d44\u6e90\u4ecd\u5728\u4e0b\u8f7d")
        if self._startup_firewall_controller.active:
            blockers.append("\u9632\u706b\u5899\u68c0\u67e5\u4ecd\u5728\u8fd0\u884c")
        if self._log_export_service is not None and self._log_export_service.active:
            blockers.append("Log export is still running")
        if self._desktop_shortcuts is not None and self._desktop_shortcuts.active:
            blockers.append("Desktop shortcut operation is still running")
        return blockers

    def exit_application(self):
        """\u53d1\u8d77\u53ef\u53d6\u6d88\u4efb\u52a1\u7684\u6536\u5c3e\uff0c\u7531\u5b8c\u6210\u4fe1\u53f7\u9a71\u52a8\u6700\u7ec8\u9000\u51fa\u3002"""
        if self._exiting:
            return
        self._exiting = True
        if self.main_window is not None:
            self.main_window.setEnabled(False)
        if self._log_export_service is not None:
            self._log_export_service.cancel()
        if self._desktop_shortcuts is not None:
            self._desktop_shortcuts.stop()
        self.app_updates.stop()
        self.capture_service.shutdown()
        self._maybe_finish_exit()

    def _maybe_finish_exit(self):
        """\u670d\u52a1\u5b8c\u6210\u6e05\u7406\u540e\u624d\u91ca\u653e\u7a97\u53e3\uff0c\u65e0\u8f6e\u8be2\u6216\u5ef6\u65f6\u8865\u4e01\u3002"""
        if not self._exiting or self._ready_to_close:
            return
        if (self.idle_blockers() or self.app_updates.active
                or not self.capture_service._shutdown_started):
            return
        if not self._history_closed:
            if not self._history_closing:
                import threading
                self._history_closing = True
                threading.Thread(target=self._drain_log_history, name='log-history-exit', daemon=True).start()
            return
        self._ready_to_close = True
        if self.main_window is not None:
            self.main_window.dispose()
            self.main_window.close()
        QApplication.instance().quit()
