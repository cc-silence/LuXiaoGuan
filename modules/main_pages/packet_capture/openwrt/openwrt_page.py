"""OpenWrt 远程抓包、登录与路由器信号管理容器。"""

from PySide6.QtCore import QSignalBlocker, Signal
from modules.ui_scale_manager import UiScaleManager
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QSplitter, QTabWidget, QVBoxLayout, QWidget

from modules.common_widgets.common_dialogs import ConfirmDialog
from modules.common_widgets.action_scroll_area import ActionScrollArea
from .router_login_page import RouterLoginPage


class OpenWrtCapturePage(QWidget):
    """“常规抓包”页面：共享 AP 表、进度条和右侧工具栏。

    页面只发出 actionRequested；后端动作统一由 OpenWrtPage/CaptureService 处理。
    """

    actionRequested = Signal(str)
    advancedWpsRequested = Signal()
    columnWidthsChanged = Signal(object)
    settingsChanged = Signal()

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
        # 右侧只承担快速操作，固定为窄栏；详细设置仍放在“设置”页面。
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
        if action == "export_signals":
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
        # 仅握手类任务进行中才点亮“正在抓包”行；其它任务或空闲一律清空，
        # 即使快照残留旧值也不显示，避免绿行与任务状态脱节。
        capturing = ()
        if (snapshot.task_state in {"starting", "capturing", "stopping"}
                and snapshot.task_type.value == "handshake"):
            capturing = tuple(snapshot.capturing_bssids or ())
        self.access_points.set_capturing_bssids(capturing)
        self.progress.apply_progress(snapshot.progress, snapshot.task_state)
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

    def __init__(self, service, settings, info_panel=None, settings_info_panel=None, parent=None, signal_info_panel=None, file_info_panel=None, common_info_panel=None):
        super().__init__(parent)
        self.service = service
        self.setObjectName("openwrtCapturePage")
        self.settings = settings
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget(self)
        self.tabs.setObjectName("openwrtCaptureTabs")
        self.login_page = RouterLoginPage(
            service,
            settings,
            settings_info_panel,
            self.tabs,
        )
        self._created_pages = {}
        pages = (("common", "\u5e38\u7528\u8bbe\u7f6e"), ("signal", "信号管理"), ("files", "文件管理"),
                 ("terminal", "路由器终端"), ("auto_capture", "自动抓包"), ("capture", "抓包"), ("settings", "抓包设置"))
        self._page_indices = {name: index for index, (name, _title) in enumerate(pages, 1)}
        self._panels = {"common": common_info_panel, "capture": info_panel, "signal": signal_info_panel, "files": file_info_panel}
        for panel in self._panels.values():
            if panel is not None:
                panel.hide()
        self._light_theme = False
        self.tabs.addTab(self.login_page, "路由器登录")
        for _name, title in pages:
            self.tabs.addTab(QWidget(self.tabs), title)
        self.tabs.setCurrentIndex(0)
        root.addWidget(self.tabs)
        self.login_page.actionRequested.connect(self.actionRequested)
        self.login_page.settingsChanged.connect(self.settingsChanged)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.service.router_management.passwordChanged.connect(self._password_changed)

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

    def _ensure_subpage(self, name):
        """首次使用才构造子页；原位替换占位页，保持标签顺序和当前选项。"""
        if name in self._created_pages:
            return self._created_pages[name]
        if name == "capture":
            page = OpenWrtCapturePage(self.service, self.settings, self._panels[name], self.tabs)
            page.actionRequested.connect(self.actionRequested)
            page.settingsChanged.connect(self.settingsChanged)
            page.columnWidthsChanged.connect(self._column_widths_changed)
            page.advancedWpsRequested.connect(self._on_advanced_wps_requested)
        elif name == "auto_capture":
            from .auto_capture_page import AutoCapturePage
            page = AutoCapturePage(self.service, self.settings, self.tabs)
            page.set_light_theme(self._light_theme)
        elif name == "settings":
            from .capture_settings_page import CaptureSettingsPage
            page = CaptureSettingsPage(self.service, self.settings, self.tabs)
            page.actionRequested.connect(self.actionRequested)
            page.settingsChanged.connect(self.settingsChanged)
            page._refresh_history_button()
        elif name == "common":
            from .router_common_settings_page import RouterCommonSettingsPage
            page = RouterCommonSettingsPage(self.service, self.connection_settings, self.tabs, info_panel=self._panels[name])
        elif name == "signal":
            from .router_signal_page import RouterSignalPage
            page = RouterSignalPage(self.service.router_signals, self._panels[name], self.tabs)
            page.loginRequested.connect(lambda: self.tabs.setCurrentIndex(0))
        elif name == "files":
            from .router_files_page import RouterFilesPage
            page = RouterFilesPage(self.service.router_management, self._panels[name], self.tabs)
            page.terminalRequested.connect(self._open_terminal_path)
            page.set_light_theme(self._light_theme)
        else:
            from .router_terminal_page import RouterTerminalPage
            page = RouterTerminalPage(self.service.router_terminal, self.tabs)
            page.set_light_theme(self._light_theme)
        self._created_pages[name] = page
        index = self._page_indices[name]
        placeholder = self.tabs.widget(index)
        current = self.tabs.currentIndex()
        title = self.tabs.tabText(index)
        with QSignalBlocker(self.tabs):
            self.tabs.removeTab(index)
            self.tabs.insertTab(index, page, title)
            self.tabs.setCurrentIndex(current)
        placeholder.deleteLater()
        panel = self._panels.get(name)
        if panel is not None:
            panel.show()
        host = self.parentWidget()
        if getattr(host, "style_mgr", None) is not None:
            host.style_mgr.refresh_subtree(page)
        return page

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

    def _open_terminal_path(self,path,script):
        if self.terminal_page.open_path(path,script):
            self.tabs.setCurrentWidget(self.terminal_page)

    def _on_advanced_wps_requested(self):
        """高级 PIN 入口先过依赖门禁；缺依赖弹窗指引去登录页。"""

        for item in self.service.openwrt_snapshot.dependencies:
            if item.key == "wps" and item.state.value == "missing":
                packages = "、".join(item.missing_packages or ()) or "对应软件包"
                if ConfirmDialog(
                    "WPS 缺少依赖",
                    f"WPS 需要依赖：{packages}。\n\n请到“路由器登录”页安装依赖后再试。",
                    self,
                    yes_text="去登录页安装",
                    no_text="取消",
                ).exec() == ConfirmDialog.Accepted:
                    self.tabs.setCurrentIndex(0)
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
        if "capture" in self._created_pages:
            data.update(self._created_pages["capture"].tools.collect_settings())
            data.update(self._created_pages["capture"].access_points.mask_state())
        return data

    def set_light_theme(self, enabled):
        """由父页面统一切换主题；具体颜色由页面 QSS 负责。"""
        self._light_theme = bool(enabled)
        for name in ("files", "terminal", "auto_capture"):
            if name in self._created_pages:
                self._created_pages[name].set_light_theme(enabled)
        self.login_page.compatible_tools.set_light_theme(enabled)
        self.login_page.common_tools.set_light_theme(enabled)
        self.login_page.dependencies.offline_controls.set_light_theme(enabled)

        return None


    def connection_settings(self):
        """返回页面当前 SSH 和普通配置，明确不包含密码。"""

        return self.collect_settings()

    def apply_scale(self):
        """由父页面统一触发 Qt 布局重算。"""

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
        self.login_page.compatible_tools.dispose()
        self.login_page.dependency_prompt.dispose()
        self.login_page.common_tools.dispose()
        self.login_page.dependencies.dispose()
        self.service.openwrtSnapshotChanged.disconnect(self.login_page._apply_snapshot)
