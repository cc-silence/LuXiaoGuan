"""OpenWrt 远程抓包、登录与路由器信号管理容器。"""

from dataclasses import replace
from PySide6.QtCore import QSignalBlocker, Signal
from modules.ui_scale_manager import UiScaleManager
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QSplitter, QTabWidget, QVBoxLayout, QWidget, QStackedWidget

from modules.common_widgets.common_dialogs import ConfirmDialog
from modules.common_widgets.action_scroll_area import ActionScrollArea
from .router_login_page import RouterLoginPage
from .router_dependency_page import RouterDependencyPage


class OpenWrtCapturePage(QWidget):
    """“常规抓包”页面：共享 AP 表、进度条和右侧工具栏。

    页面只发出 actionRequested；后端动作统一由 OpenWrtPage/CaptureService 处理。
    """

    actionRequested = Signal(str)
    advancedWpsRequested = Signal()
    columnWidthsChanged = Signal(object)
    settingsChanged = Signal()
    pageChanged = Signal(str)

    def __init__(self, service, settings, info_panel=None, parent=None):
        super().__init__(parent)
        self.service = service
        self.info_panel = info_panel
        self.settings = settings
        self.setObjectName("openwrtCaptureWorkPage")
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 4, 0)
        root.setSpacing(8)
        upper_panel = QWidget(self)
        upper_panel.setObjectName("openwrtCaptureUpperPanel")
        upper_layout = QVBoxLayout(upper_panel)
        upper_layout.setContentsMargins(0, 0, 0, 0)
        # 表格使用 QSplitter 分配的高度，进度条固定在
        # 表格区最下方，不随表格行数上下漂移。
        upper_layout.setSpacing(0)
        from .access_point_table import AccessPointTable
        from .task_progress import CaptureTaskProgress
        self.access_points = AccessPointTable(upper_panel)
        self.access_points.set_column_widths(settings.get("access_point_column_widths", []))
        self.access_points.set_masked(
            bool(settings.get("mask_bssid_column", False)),
            bool(settings.get("mask_ssid_column", False)),
            notify=False,
        )
        self.access_points.maskingChanged.connect(self._on_masking_changed)
        self.access_points.maskingChanged.connect(self._sync_display_privacy)
        self.access_points.privacyDisplayChanged.connect(self._sync_display_privacy)
        self.progress = CaptureTaskProgress(upper_panel)
        self.access_points.setSizeAdjustPolicy(AccessPointTable.AdjustToContents)
        upper_layout.addWidget(self.access_points, 1)
        upper_layout.addWidget(self.progress)
        if info_panel is not None:
            from modules.common_widgets.info_panel_dock import InfoPanelDock
            self.info_dock = InfoPanelDock(upper_panel, info_panel, Qt.Vertical, self, sizes=(390, 170))
            root.addWidget(self.info_dock, 3)
        else:
            left_splitter = QSplitter(Qt.Vertical, self)
            left_splitter.setObjectName("captureContentSplitter")
            left_splitter.setChildrenCollapsible(False)
            left_splitter.setHandleWidth(5)
            left_splitter.addWidget(upper_panel)
            root.addWidget(left_splitter, 3)
        from .capture_tool_panel import CaptureToolPanel
        self.tools = CaptureToolPanel(settings, self)
        # The narrow action panel opens detailed settings in a shared dialog.
        self.tools_scroll = ActionScrollArea(self.tools, self)
        root.addWidget(self.tools_scroll)
        self.access_points.selectionChangedByUser.connect(self._selection_changed)
        self.access_points.clearStatusRequested.connect(self.service.clear_verified_result)
        self.access_points.columnWidthsChanged.connect(self.columnWidthsChanged)
        from .signal_list_export import SignalListExportController
        self.signal_export = SignalListExportController(self)
        self.tools.actionRequested.connect(self._handle_tool_action)
        self.tools.advancedWpsRequested.connect(self.advancedWpsRequested)
        service.openwrtSnapshotChanged.connect(self.apply_snapshot)
        self.apply_snapshot(service.openwrt_snapshot)

    def _handle_tool_action(self, action):
        """本机导出由独立控制器处理，其他动作仍走原业务入口。"""
        if action == "password_results":
            from .password_results_dialog import show_password_results
            show_password_results(self)
        elif action == "clear_password_attempts":
            if self.service.task_controller.busy:return
            if ConfirmDialog("清理已尝试记录", "将清空所有信号的已尝试记录，下次可重新尝试。\n不删除字典和已保存的结果文件。", self).exec() == ConfirmDialog.Accepted:
                self.service.password_history.clear_async()
        elif action == "export_signals":
            self.signal_export.export()
        else:
            self.actionRequested.emit(action)

    def _sync_display_privacy(self):
        if not hasattr(self, "progress"):
            return
        masker = self.access_points.local_masker()
        self.progress.title_label.set_local_masker(masker)
        self.progress.progress_bar.set_local_masker(masker)
        if self.info_panel is not None:
            self.info_panel.set_local_masker(masker)

    def _selection_changed(self, bssids):
        """把 AP 选择交给常驻服务，多个功能共用同一集合。"""

        self.service.set_openwrt_selected_bssids(bssids)

    def _on_masking_changed(self):
        """掩码变化只触发保存；当次任务沿用启动时设置，表格即时生效。"""

        self.settingsChanged.emit()

    def apply_snapshot(self, snapshot):
        """从常驻快照刷新表格、进度和动作状态。"""

        self.access_points.set_access_points(snapshot.access_points)
        self.signal_export.set_has_points(snapshot.access_points)
        self._sync_display_privacy()
        # Highlight the current capture/verification target only while active.
        capturing = ()
        if (snapshot.task_state in {"starting", "capturing", "stopping"}
                and snapshot.task_type.value in {"handshake", "password_verify", "wps"}):
            capturing = tuple(snapshot.capturing_bssids or ())
        self.access_points.set_capturing_bssids(capturing, activity=snapshot.task_type.value)
        progress = snapshot.progress
        candidate = str(getattr(self.service, "password_verification_candidate", "") or "")
        if (snapshot.task_type.value == "password_verify"
                and snapshot.task_state in {"starting", "capturing"} and candidate):
            progress = replace(progress, title=f"{progress.title} | 当前密码：{candidate}")
        self.progress.apply_progress(progress, snapshot.task_state)
        enabled = {
            "scan": False,
            "handshake": False,
            "pmkid": False,
            "wps": False,
            "password": False,
        }
        for item in snapshot.dependencies:
            if item.key in enabled:
                enabled[item.key] = item.state.value == "ready"
        connected = snapshot.connection_state.value == "connected"
        self.tools.set_action_availability(
            connected,
            snapshot.task_state not in {"idle", "failed"},
            enabled,
            snapshot.task_type.value,
            snapshot.task_state,
            snapshot.pause_state,
            snapshot.wps_entry,
        )
        # 会话里一旦有已验证的可用产物（可跑包 CAP/22000），就把取结果入口置绿；
        # 多目标任务中途已有目标出可用包时靠会话级标记提前变绿，不用等任务结束；
        # 断开连接后快照清空，颜色自动恢复。
        self.tools.set_result_available(
            snapshot.has_verified_result
            or any(artifact.verified for artifact in snapshot.artifacts)
        )

    def show_advanced_wps(self):
        """在主线程显示高级 Pin 参数弹窗，不启动远程任务。

        确认后先把弹窗参数写回配置并保存（下次直接用），再发动作启动任务。
        """

        defaults = self.settings.get("wps_defaults", {}) if isinstance(self.settings, dict) else {}
        from .wps_dialog import AdvancedWpsDialog
        dialog = AdvancedWpsDialog(defaults, self)
        if dialog.exec():
            if isinstance(self.settings, dict):
                self.settings["wps_defaults"] = dialog.get_settings()
                self.settingsChanged.emit()
            self.actionRequested.emit("advanced_wps_confirmed")


class OpenWrtPage(QWidget):
    """OpenWrt 四个顶层标签容器，业务分别由独立页面处理。

    参数：service 是 ResidentRuntime 持有的常驻服务；settings 是标准化配置。
    返回值：无。副作用：创建页面控件并订阅服务快照，不执行远程命令。
    """

    actionRequested = Signal(str)
    settingsChanged = Signal()
    pageChanged = Signal(str)

    def __init__(self, service, settings, info_panel=None, settings_info_panel=None, parent=None, signal_info_panel=None, file_info_panel=None, common_info_panel=None, dependency_info_panel=None):
        super().__init__(parent)
        self.service = service
        self.setObjectName("openwrtCapturePage")
        self.settings = settings
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.stack = QStackedWidget(self)
        self.stack.setObjectName("openwrtPageStack")
        root.addWidget(self.stack)

        self.tabs = QTabWidget(self)
        self.tabs.setObjectName("openwrtCaptureTabs")
        self.dependency_page = RouterDependencyPage(
            service,
            settings,
            self.tabs,
            info_panel=dependency_info_panel,
        )
        self.login_page = RouterLoginPage(
            service,
            settings,
            settings_info_panel,
            self.tabs,
            dependency_page=self.dependency_page,
        )
        self.tabs.addTab(self.login_page, "路由器登录")
        self.tabs.addTab(self.dependency_page, "依赖管理")
        self.stack.addWidget(self.tabs)

        self._created_pages = {}
        self._settings_dialog = None
        self._page_names = ("login", "common", "signal", "files", "terminal", "auto_capture", "capture")
        self._page_indices = {name: index for index, name in enumerate(self._page_names)}
        self._current_page_name = "login"
        self._panels = {"common": common_info_panel, "capture": info_panel, "signal": signal_info_panel, "files": file_info_panel}
        for panel in self._panels.values():
            if panel is not None:
                panel.hide()
        self._light_theme = False

        self.login_page.actionRequested.connect(self.actionRequested)
        self.login_page.settingsChanged.connect(self.settingsChanged)
        self.dependency_page.actionRequested.connect(self.actionRequested)
        self.dependency_page.settingsChanged.connect(self.settingsChanged)
        self.service.router_management.passwordChanged.connect(self._password_changed)

        orig_set_current_index = self.tabs.setCurrentIndex
        def _compat_set_current_index(index):
            if index in range(self.tabs.count()):
                self.show_page("login")
                orig_set_current_index(index)
            elif index in range(len(self._page_names)):
                self.show_page(self._page_names[index])
            else:
                orig_set_current_index(index)
        self.tabs.setCurrentIndex = _compat_set_current_index

        orig_current_index = self.tabs.currentIndex
        def _compat_current_index():
            if self.stack.currentWidget() is self.tabs:
                return orig_current_index()
            return self._page_indices.get(self._current_page_name, 0)
        self.tabs.currentIndex = _compat_current_index

        orig_current_widget = self.tabs.currentWidget
        def _compat_current_widget():
            if self.stack.currentWidget() is self.tabs:
                return orig_current_widget()
            return self.stack.currentWidget()
        self.tabs.currentWidget = _compat_current_widget

        orig_widget = self.tabs.widget
        def _compat_widget(index):
            if index in range(self.tabs.count()):
                return orig_widget(index)
            if index in range(len(self._page_names)):
                return self._ensure_subpage(self._page_names[index])
            return orig_widget(index)
        self.tabs.widget = _compat_widget

    @property
    def common_page(self):
        return self._ensure_subpage("common")

    @property
    def auto_capture_page(self):
        return self._ensure_subpage("auto_capture")

    @property
    def capture_page(self):
        return self._ensure_subpage("capture")

    @property
    def settings_page(self):
        return self._ensure_subpage("settings")

    @property
    def signal_page(self):
        return self._ensure_subpage("signal")

    @property
    def files_page(self):
        return self._ensure_subpage("files")

    @property
    def terminal_page(self):
        return self._ensure_subpage("terminal")

    @property
    def current_page_name(self):
        return self._current_page_name

    def show_page(self, name):
        if isinstance(name, int):
            if 0 <= name < len(self._page_names):
                name = self._page_names[name]
            else:
                return None
        if name in ("login", "router", "login_page"):
            self.stack.setCurrentWidget(self.tabs)
            self._current_page_name = "login"
            self.pageChanged.emit("login")
            return self.login_page
        page = self._ensure_subpage(name)
        if page is not None:
            self.stack.setCurrentWidget(page)
            self._current_page_name = name
            if name in ("files", "terminal", "auto_capture"):
                page.enter()
            self.pageChanged.emit(name)
            self.settingsChanged.emit()
            return page
        return None

    def _ensure_subpage(self, name):
        """首次使用才构造子页并挂载到主堆栈，不往上方标签栏添加额外标签。"""
        if name in self._created_pages:
            return self._created_pages[name]
        if name == "capture":
            page = OpenWrtCapturePage(self.service, self.settings, self._panels[name], self)
            page.actionRequested.connect(self._handle_capture_action)
            page.settingsChanged.connect(self.settingsChanged)
            page.columnWidthsChanged.connect(self._column_widths_changed)
            page.advancedWpsRequested.connect(self._on_advanced_wps_requested)
        elif name == "auto_capture":
            from .auto_capture_page import AutoCapturePage
            page = AutoCapturePage(self.service, self.settings, self)
            page.set_light_theme(self._light_theme)
        elif name == "settings":
            from .capture_settings_dialog import CaptureSettingsDialog
            self._settings_dialog = CaptureSettingsDialog(self.service, self.settings, self)
            page = self._settings_dialog.page
            self._settings_dialog.settingsChanged.connect(self.settingsChanged)
            page.actionRequested.connect(self.actionRequested)
            page._refresh_history_button()
        elif name == "common":
            from .router_common_settings_page import RouterCommonSettingsPage
            page = RouterCommonSettingsPage(self.service, self.connection_settings, self, info_panel=self._panels[name])
        elif name == "signal":
            from .router_signal_page import RouterSignalPage
            page = RouterSignalPage(self.service.router_signals, self._panels[name], self)
            page.loginRequested.connect(lambda: self.show_page("login"))
        elif name == "files":
            from .router_files_page import RouterFilesPage
            page = RouterFilesPage(self.service.router_management, self._panels[name], self)
            page.terminalRequested.connect(self._open_terminal_path)
            page.set_light_theme(self._light_theme)
        else:
            from .router_terminal_page import RouterTerminalPage
            page = RouterTerminalPage(self.service.router_terminal, self)
            page.set_light_theme(self._light_theme)
        self._created_pages[name] = page
        if name == "settings":
            self._sync_settings_dialog_appearance()
            return page
        self.stack.addWidget(page)
        panel = self._panels.get(name)
        if panel is not None:
            panel.show()
        host = self.parentWidget()
        if getattr(host, "style_mgr", None) is not None:
            host.style_mgr.refresh_subtree(page)
        return page

    def _handle_capture_action(self, action):
        if action == "capture_settings":
            self.show_capture_settings()
        else:
            self.actionRequested.emit(action)

    def show_capture_settings(self):
        self.settings_page
        self._sync_settings_dialog_appearance()
        self._settings_dialog.show()
        self._settings_dialog.raise_()
        self._settings_dialog.activateWindow()

    def _sync_settings_dialog_appearance(self):
        host = self.parentWidget()
        if self._settings_dialog is not None and getattr(host, "style_mgr", None) is not None:
            self._settings_dialog.sync_appearance(host.style_mgr)

    def _password_changed(self, target):
        connection = self.login_page.connection
        settings = connection.collect()
        if (settings["host"], settings["port"], settings["user"]) == tuple(target) and settings["auth_method"] == "password":
            connection.password.setText(self.service.openwrt_password())

    def _tab_changed(self, index):
        name = next((name for name, number in self._page_indices.items() if number == index), None)
        if name is not None:
            page = self._ensure_subpage(name)
            if name in ("files", "terminal", "auto_capture"):
                page.enter()
        self.settingsChanged.emit()

    def _open_terminal_path(self, path, script):
        if self.terminal_page.open_path(path, script):
            self.show_page("terminal")

    def _on_advanced_wps_requested(self):
        """高级 PIN 入口先过依赖门禁；缺依赖弹窗指引去登录页。"""
        for item in self.service.openwrt_snapshot.dependencies:
            if item.key == "wps" and item.state.value == "missing":
                packages = "、".join(item.missing_packages or ()) or "对应软件包"
                if ConfirmDialog(
                    "WPS 缺少依赖",
                    f"WPS 需要依赖：{packages}。\n\n请到“依赖管理”页安装依赖后再试。",
                    self,
                    yes_text="去依赖管理",
                    no_text="取消",
                ).exec() == ConfirmDialog.Accepted:
                    self.show_page("login")
                    self.tabs.setCurrentIndex(1)
                return
        self.capture_page.show_advanced_wps()

    def _column_widths_changed(self, widths):
        """保存用户拖动后的 AP 表列宽，页面重建后继续沿用。"""

        self.settings["access_point_column_widths"] = list(widths)
        self.settingsChanged.emit()

    def collect_settings(self):
        """收集抓包设置页的普通字段，保留没有独立控件的安全偏好。

        返回值不含 SSH 密码。`skip_scan_confirmation` 等由确认流程写入、没有独立控件
        的字段必须从当前配置继承，不能因一次页面保存被意外重置。
        """

        data = dict(self.settings)
        if "settings" in self._created_pages:
            data.update(self._created_pages["settings"].collect_settings())
        data.update(self.login_page.collect_settings())
        data.update(self.dependency_page.collect_settings())
        if "capture" in self._created_pages:
            data.update(self._created_pages["capture"].tools.collect_settings())
            data.update(self._created_pages["capture"].access_points.mask_state())
        return data

    def set_light_theme(self, enabled):
        """由父页面统一切换主题；具体颜色由页面 QSS 负责。"""
        self._light_theme = bool(enabled)
        self._sync_settings_dialog_appearance()
        for name in ("files", "terminal", "auto_capture"):
            if name in self._created_pages:
                self._created_pages[name].set_light_theme(enabled)
        self.dependency_page.set_light_theme(enabled)

        return None


    def connection_settings(self):
        """返回页面当前 SSH 和普通配置，明确不包含密码。"""

        return self.collect_settings()

    def apply_scale(self):
        """由父页面统一触发 Qt 布局重算。"""

        self._sync_settings_dialog_appearance()
        if "terminal" in self._created_pages:
            self._created_pages["terminal"].apply_theme()
        self.updateGeometry()

    def has_background_activity(self):
        """返回服务是否有正在执行的抓包任务。"""

        return self.service.is_busy or self.service.router_terminal.active

    def dispose(self):
        """页面释放前断开服务快照信号。"""

        self.service.router_management.passwordChanged.disconnect(self._password_changed)
        for name, page in self._created_pages.items():
            if name == "capture":
                page.signal_export.dispose()
                self.service.openwrtSnapshotChanged.disconnect(page.apply_snapshot)
            elif name == "settings":
                page.cancel_preview()
                self.service.openwrtSnapshotChanged.disconnect(page._apply_snapshot)
            else:
                page.dispose()
        if getattr(self.login_page, "dependency_prompt", None) is not None:
            self.login_page.dependency_prompt.dispose()
        self.dependency_page.dispose()
        self.service.openwrtSnapshotChanged.disconnect(self.login_page._apply_snapshot)
