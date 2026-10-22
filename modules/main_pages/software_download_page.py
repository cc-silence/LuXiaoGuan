from PySide6.QtCore import Qt, QTimer, QUrl, Slot
from PySide6.QtGui import QDesktopServices

from PySide6.QtWidgets import (
    QProgressBar,
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QTableWidget,
)

from modules.config_manager import (
    ConfigManager,
    DownloadConfigManager,
)
from modules.config_services.software_items_manager import SoftwareItemsConfigManager
from modules.download_services.download_state_manager import DownloadStateManager
from modules.download_services.download_paths import software_download_directory
from modules.main_pages.download.download_check_coordinator import DownloadCheckCoordinator
from modules.main_pages.download.download_auto_action_coordinator import DownloadAutoActionCoordinator
from modules.main_pages.download.download_operations import DownloadOperationsCoordinator
from modules.main_pages.download.download_state_restore import DownloadStateRestoreCoordinator
from modules.main_pages.download.download_column_handlers import DownloadColumnHandler
from modules.main_pages.download.download_mode_switch_prompt import DownloadModeSwitchPromptController
from modules.main_pages.download.download_table_layout import DownloadTableLayoutManager
from modules.main_pages.download.download_context_menu import (
    CategoryArrowClickFilter,
    DownloadContextMenuHandler,
)
from modules.main_pages.download.download_row_builder import DownloadRowBuilder
from modules.main_pages.download.download_size_cell import refresh_download_size
from modules.main_pages.download.download_ui_helpers import (
    DEFAULT_INFO_TEXT,
)
from modules.main_pages.page_action_buttons import (
    create_page_icon_button as create_icon_button,
    create_page_text_button as create_text_button,
)
from modules.main_pages.info_panel import InfoPanel
from modules.main_pages.main_page_workspace import create_main_page_workspace
from modules.main_pages.download.download_page_styles import DownloadPageStyleManager
from modules.main_pages.download.download_page_utils import (
    current_download_mode,
    flatten_software_items,
)
from modules.main_pages.status_widget_factory import build_status_widget
from modules.update_services.update_package_scanner import UpdatePackageScanner
from modules.update_services.update_settings_manager import UpdateSettingsManager
from modules.main_pages.download.download_status import (
    STATUS_DONE,
    STATUS_DOWNLOADING,
    current_version_display,
    STATUS_NOT_CHECKED,
    STATUS_PAUSED,
    STATUS_TEXT,
)
from modules.common_widgets.left_resize_header import LeftResizeHeaderView
from modules.common_widgets.auto_hide_scroll_bar import AutoHideScrollBar


class SoftwareDownloadPage(QFrame):
    # 主窗口"软件下载"页面。
    # 这个类只负责界面展示、配置读取和第一版占位交互；
    # 已接入真实下载服务（Aria2Downloader）和版本检测器（VersionChecker）。
    def __init__(self, parent=None, open_settings_callback=None, scale_manager=None,
                 download_service=None, version_checker=None, software_item_dialog_factory=None):
        super().__init__(parent)
        self.setObjectName("softwareDownloadPage")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.open_settings_callback = open_settings_callback
        self.scale_manager = scale_manager
        self.software_manager = SoftwareItemsConfigManager()
        self.config_manager = ConfigManager()
        self.state_manager = DownloadStateManager(self.config_manager.path.parent)
        self.download_manager = DownloadConfigManager()
        self.update_settings_manager = UpdateSettingsManager()
        self.package_scanner = UpdatePackageScanner(
            self.download_manager,
            self.software_manager,
            self.update_settings_manager,
        )
        self.rows = []
        self.row_by_id = {}
        self.category_rows = {}
        self.collapsed_categories = set()
        self.light_theme = False
        self._items_loaded = False

        # 注入真实下载服务和版本检测器
        self.download_service = download_service
        self.version_checker = version_checker
        self._check_results = {}       # software_id -> check result dict
        self._download_tasks = {}      # software_id -> DownloadTask
        # aria2 首次启动期间可能连续点击多个下载项，使用列表保留全部待提交的 ID，
        # 不能只保存最后一次点击，否则前面的下载会被静默覆盖。
        self._pending_download_ids = []
        self._network_generation = 0  # 代理开关或临时下载方式变化时递增，用来丢弃旧检测结果。
        self._download_mode_switch_prompt = DownloadModeSwitchPromptController()
        # 软件、文件名和状态列允许用户调整；版本、架构和下载方式使用固定宽度。
        self._resizable_columns = (0, 6)
        self._applying_column_widths = False
        self._column_width_save_timer = QTimer(self)
        self._column_width_save_timer.setSingleShot(True)

        # 版本检测协调器
        self._check_coordinator = DownloadCheckCoordinator(self)
        # 检测完成后的自动下载/架构切换统一由行级协调器管理，页面刷新时可集中停止。
        self._auto_action_coordinator = DownloadAutoActionCoordinator(self)
        # 下载操作协调器
        self._download_coordinator = DownloadOperationsCoordinator(self)
        # 跨重启恢复只映射状态和页面控件，不在页面类中混入文件确认细节。
        self._state_restore_coordinator = DownloadStateRestoreCoordinator(self)
        # 列控件处理器
        self._column_handler = DownloadColumnHandler(self)
        # 表格布局和列宽持久化管理器
        self._table_layout = DownloadTableLayoutManager(self)
        self._column_width_save_timer.timeout.connect(self._table_layout.save_column_widths)
        # 下载页样式管理器（主题切换、QSS 应用）
        self.style_mgr = DownloadPageStyleManager(self)
        # 右键菜单和下载项编辑处理器
        self._context_menu_handler = DownloadContextMenuHandler(self, software_item_dialog_factory)
        # 表格分类行和软件行构建器
        self._row_builder = DownloadRowBuilder(self)

        self._build_ui()
        self._connect_engine_signals()
        # 首帧先显示可操作的下载页骨架，再由事件循环创建大量表格单元格控件。
        # 这样不会阻塞主窗口首次显示；设置保存前若已触发 reload_items()，回调会自动跳过。
        QTimer.singleShot(0, self._load_initial_items)

    def _connect_engine_signals(self):
        """连接下载引擎信号，用于引擎就绪后重试待下载任务。"""
        if self.download_service:
            self.download_service.networkProgress.connect(self._on_network_progress)
            self.download_service.engineReady.connect(self._retry_pending_download)
            self.download_service.engineStartFailed.connect(self._on_engine_start_failed)

        if self.version_checker:
            self.version_checker.checkNetworkProgress.connect(self._on_network_progress)

    @Slot(str, str, str)
    def _on_network_progress(self, software_id, message, level):
        """在主线程信息窗口显示该软件实际使用的检测/下载通道。"""
        row = self.row_by_id.get(software_id)
        if row is not None:
            name = row.get("node", {}).get("name", "软件")
            self._append_info(f"{name}：{message}", level=level)

    def _on_engine_start_failed(self, message):
        """把 aria2 启动失败反馈给正在等待引擎的下载项。

        参数 message 是引擎启动失败原因。返回值：无。
        副作用：委托下载协调器清空等待 ID 并更新对应行状态；不重启引擎、不创建弹窗。
        """

        self._download_coordinator.on_engine_start_failed(message)

    def _load_initial_items(self):
        """在主窗口首帧显示后加载软件下载列表。

        参数：无。返回值：无。
        副作用：首次读取 software_items.json 并创建表格行和行内控件。
        调用限制：由构造函数的单次计时器调用；若其他入口已经刷新过列表，不重复创建。
        """
        if not self._items_loaded:
            self.reload_items()

    def _build_ui(self):
        # 页面整体采用“顶部操作区 + 中间列表 + 底部信息框”的结构。
        # 顶部只放批量动作，单个软件的完整操作放到右键菜单里，避免列表右侧按钮过多。
        workspace = create_main_page_workspace(self)
        self.root_layout = workspace.root_layout
        left_panel_layout = workspace.left_layout
        right_panel_layout = workspace.right_layout
        self.right_panel = workspace.right_panel

        header = QHBoxLayout()
        header.setSpacing(4)
        header.addStretch(1)
        self.root_layout.insertLayout(0, header)

        toolbar_left = QHBoxLayout()
        toolbar_left.setContentsMargins(0, 0, 0, 0)
        toolbar_left.setSpacing(4)
        self.check_all_btn = create_text_button(self, "全部检测", "检测所有启用软件的最新版本和下载链接。")
        self.download_updates_btn = create_text_button(self, "下载可更新项", "下载检测结果中有新版本的软件。")
        self.open_dir_btn = create_text_button(self, "打开下载目录", "打开文件保存位置。")
        toolbar_left.addWidget(self.check_all_btn)
        toolbar_left.addWidget(self.download_updates_btn)
        toolbar_left.addWidget(self.open_dir_btn)
        toolbar_left.addStretch(1)
        self.resume_all_btn = create_icon_button(
            self,
            ":/icons/images/icons/cil-media-play.png", "继续所有已暂停的下载任务"
        )
        self.pause_all_btn = create_icon_button(
            self,
            ":/icons/images/icons/cil-media-pause.png", "暂停所有正在下载的任务"
        )
        self.cancel_all_btn = create_icon_button(
            self,
            ":/icons/images/icons/cil-x.png", "取消所有下载任务"
        )
        toolbar_left.addWidget(self.resume_all_btn)
        toolbar_left.addWidget(self.pause_all_btn)
        toolbar_left.addWidget(self.cancel_all_btn)
        left_panel_layout.addLayout(toolbar_left)

        toolbar_right = QHBoxLayout()
        toolbar_right.setContentsMargins(0, 0, 0, 0)
        toolbar_right.setSpacing(4)
        self.settings_btn = create_icon_button(self, ":/icons/images/icons/cil-settings.png", "打开下载设置")
        self.proxy_settings_btn = create_icon_button(self, ":/icons/images/icons/cil-link-alt.png", "打开代理设置")
        self.clear_info_btn = create_icon_button(self, ":/icons/images/icons/cil-x-circle.png", "清空下载信息")
        toolbar_right.addStretch(1)
        toolbar_right.addWidget(self.settings_btn)
        toolbar_right.addWidget(self.proxy_settings_btn)
        toolbar_right.addWidget(self.clear_info_btn)
        right_panel_layout.addLayout(toolbar_right)

        # 表格和信息框放在同一行，信息框在右侧作为侧边栏。
        # 表格占主要空间，信息框固定宽度显示日志和引擎状态。
        self.table = QTableWidget(self)
        self.table.setObjectName("softwareDownloadTable")
        self.table.setAttribute(Qt.WA_StyledBackground, True)
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels(["软件", "当前版本", "最新版本", "文件名", "架构", "下载方式", "状态", "大小"])
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setFocusPolicy(Qt.NoFocus)
        self.table.setVerticalScrollBar(AutoHideScrollBar(Qt.Vertical, self.table))
        self.table.setHorizontalScrollBar(AutoHideScrollBar(Qt.Horizontal, self.table))
        # 文本单元格根据实际列宽换行，和更新页表格保持一致。
        self.table.setWordWrap(True)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu_handler.show_context_menu)
        self.table.cellDoubleClicked.connect(self._context_menu_handler.handle_cell_double_clicked)
        # 仅箭头区域支持单击展开；文件夹图标和分类名称仍保持双击交互。
        self._category_arrow_click_filter = CategoryArrowClickFilter(self)
        self.table.viewport().installEventFilter(self._category_arrow_click_filter)
        status_minimum_width = self.scale_manager.px(250) if self.scale_manager else 250
        header_view = LeftResizeHeaderView(
            Qt.Horizontal,
            6,
            (0,),
            {0: 60, 6: status_minimum_width},
            self.table,
        )
        self.table.setHorizontalHeader(header_view)
        header_view.setMinimumSectionSize(60)
        header_view.moveSection(header_view.visualIndex(7), header_view.visualIndex(6))
        header_view.setSectionsMovable(False)
        header_view.setStretchLastSection(False)
        header_view.setSectionResizeMode(0, QHeaderView.Interactive) # 软件 — 可调
        header_view.setSectionResizeMode(1, QHeaderView.Fixed)      # 当前版本 — 固定
        header_view.setSectionResizeMode(2, QHeaderView.Fixed)      # 最新版本 — 固定
        header_view.setSectionResizeMode(3, QHeaderView.Interactive) # 文件名 — 可调
        header_view.setSectionResizeMode(4, QHeaderView.Fixed)       # 架构 — 不允许拖动
        header_view.setSectionResizeMode(5, QHeaderView.Fixed)       # 下载方式 — 不允许拖动
        header_view.setSectionResizeMode(6, QHeaderView.Interactive) # 状态 — 可调
        header_view.setSectionResizeMode(7, QHeaderView.Fixed)
        header_view.sectionResized.connect(self._table_layout.schedule_column_width_save)
        self._table_layout.apply_default_column_widths()
        left_panel_layout.addWidget(self.table, 1)

        saved_font_delta = int(self.config_manager.load().get("ui", "info_font_delta", fallback="0"))
        self.info_panel = InfoPanel(
            self, log_category="\u4e0b\u8f7d",
            placeholder_text="下载信息会显示在这里。",
            default_text=DEFAULT_INFO_TEXT,
            zoom_out_tooltip="缩小下载信息文字",
            zoom_in_tooltip="放大下载信息文字",
            light_theme=self.light_theme,
            scale_manager=self.scale_manager,
            initial_font_delta=saved_font_delta,
            save_font_delta_callback=self._save_info_font_delta,
        )
        right_panel_layout.addWidget(self.info_panel, 1)

        self._connect_actions()
        self.style_mgr._apply_styles()
        self.apply_scale()

    def _connect_actions(self):
        # "全部检测"保持普通按钮行为，不使用引导扫光，避免与同栏操作按钮风格不一致。
        self.check_all_btn.set_hover_sweep(False)
        self.check_all_btn.clicked.connect(self._on_check_all_clicked)

        self.download_updates_btn.set_hover_sweep(False)
        self.open_dir_btn.set_hover_sweep(False)
        self.download_updates_btn.clicked.connect(self.download_updates)
        self.resume_all_btn.clicked.connect(self.resume_all)
        self.pause_all_btn.clicked.connect(self.pause_all)
        self.cancel_all_btn.clicked.connect(self.cancel_all)
        self.open_dir_btn.clicked.connect(self.open_download_dir)
        self.settings_btn.clicked.connect(self.open_settings)
        self.proxy_settings_btn.clicked.connect(self.open_proxy_settings)
        self.clear_info_btn.clicked.connect(self.info_panel.clear)

    def _save_info_font_delta(self, delta):
        """保存信息框字体缩放值到配置，下次启动恢复。"""
        config = self.config_manager.load()
        config.set("ui", "info_font_delta", str(delta))
        self.config_manager.save(config)

    def _on_check_all_clicked(self):
        """"全部检测"点击处理：直接执行检测，不修改按钮视觉状态。"""
        self.check_all()

    def reload_items(self):
        """整批列表构建完毕后再恢复绘制，避免每个单元格反复触发刷新。"""
        updates_enabled = self.table.updatesEnabled()
        self.table.setUpdatesEnabled(False)
        try:
            self._reload_item_rows()
        finally:
            self.table.setUpdatesEnabled(updates_enabled)

    def _reload_item_rows(self):
        # 重新读取 software_items.json，并把设置页里的树状分类压平成主界面适合阅读的分类路径。
        # 主界面只显示启用软件；空分类和全禁用分类不显示。
        self._items_loaded = True
        self._auto_action_coordinator.cancel_all()
        previous_categories = set(self.category_rows.keys())
        self.table.setRowCount(0)
        self.rows.clear()
        self.row_by_id.clear()
        self.category_rows.clear()
        config = self.software_manager.load()
        entries, folder_nodes = flatten_software_items(config.get("items", []))
        if not entries:
            self._append_info("没有可显示的软件。请先到设置里的“软件配置列表”添加并启用软件下载项。", level="warning")
            return

        # 软件下载页默认让分类保持收起，避免首次进入时列表被大量软件撑开。
        # 用户手动展开/收起后的状态在当前页面生命周期内保留；新出现的分类仍默认收起。
        category_names = []
        for entry in entries:
            category = entry["category"]
            if category not in category_names:
                category_names.append(category)
        visible_categories = set(category_names)
        if previous_categories:
            self.collapsed_categories.intersection_update(visible_categories)
            self.collapsed_categories.update(visible_categories - previous_categories)
        else:
            self.collapsed_categories = set(visible_categories)

        current_category = None
        for entry in entries:
            category = entry["category"]
            if category != current_category:
                self._row_builder.add_category_row(category, entry["category_parts"], folder_nodes.get(category))
                current_category = category
            self._row_builder.add_software_row(entry)
        # 恢复之前暂停的下载状态（检查部分文件是否存在）
        self._restore_paused_states()
        # 先完成表格骨架和暂停状态恢复，再扫描目录，减少首次打开下载页时的阻塞感。
        QTimer.singleShot(0, self._refresh_completed_package_states)
        # 行内控件会影响表头的初始测量，行加载完成后重新应用用户/默认列宽再适配。
        self._table_layout.apply_default_column_widths()
        # 等待 QTableWidget 完成 cellWidget 布局后再应用一次，避免状态控件把列宽撑回旧值。
        QTimer.singleShot(0, self._table_layout.apply_default_column_widths)

    def _set_row_text(self, row, column, text, row_data, tooltip=""):
        """委托下载行构建器写入文本，并可附带完整文件名提示。"""
        self._row_builder.set_row_text(row, column, text, row_data, tooltip=tooltip)

    def _build_status_widget(self, row_data):
        """包装 build_status_widget，传入当前页面状态和回调。"""
        callbacks = {
            "pause_single": self.pause_single,
            "cancel_single": self.cancel_single,
            "resume_single": self.resume_single,
            "open_software_dir": self.open_software_dir,
            "retry_with_proxy": self.retry_with_proxy,
            "download_single": self.download_single,
            "arch_mismatch_switch": self._on_arch_mismatch_switch,
            "arch_mismatch_keep": self._on_arch_mismatch_keep,
            "check_single": self.check_single,
            "check_with_proxy": self.retry_check_with_proxy,
            "cancel_check": self.cancel_check,
            "auto_download_enabled": self._auto_action_coordinator.download_enabled,
            "start_auto_download": self._auto_action_coordinator.start_download,
            "start_auto_arch_switch": self._auto_action_coordinator.start_arch_switch,
            "cancel_auto_action": self._cancel_auto_action,
        }
        return build_status_widget(
            row_data, self.table, self.light_theme, self.scale_manager,
            self.config_manager, callbacks,
        )

    def _set_status(self, row_data, status, progress=None):
        if status in ('checking', 'not_checked', 'check_failed'):
            row_data.pop('_download_file_sizes', None)
            refresh_download_size(self, row_data, result={})
        if status == STATUS_DOWNLOADING and row_data.get("status") == status:
            row = self._row_for_data(row_data)
            widget = self.table.cellWidget(row, 6) if row is not None else None
            bar = widget.findChild(QProgressBar, "downloadProgressBar") if widget else None
            if bar is not None:
                from modules.main_pages.status_widget_factory import format_speed
                if progress is not None:
                    row_data["progress"] = progress
                percent = float(row_data.get("progress_percent", row_data.get("progress", 0)))
                bar.setValue(int(percent * 100))
                bar.setFormat(f"{percent:.2f} % · {format_speed(row_data.get('download_speed', 0))}")
                return
        # 只在状态切换时重建控件，进度刷新原位更新。
        self._auto_action_coordinator.stop(row_data.get("id", ""))
        row_data["status"] = status
        if progress is not None:
            row_data["progress"] = progress
        row = self._row_for_data(row_data)
        if row is not None:
            self.table.setCellWidget(row, 6, self._build_status_widget(row_data))

    def _refresh_completed_package_states(self):
        """扫描默认下载状态的软件目录，并记录是否存在完整安装包。

        参数：无。返回值：无。
        副作用：读取各软件下载目录，在行数据中写入 completed_packages；只会重建默认
        “检测更新”状态的状态栏，不覆盖下载中、等待、暂停、完成或失败等任务状态。
        调用限制：目录扫描会访问文件系统，应只在列表加载、配置刷新等低频入口调用。
        """
        scan_targets = [
            (row_data.get("id", ""), row_data.get("node", {}), self._software_download_dir(row_data))
            for row_data in self.rows
        ]
        packages_by_id = self.package_scanner.scan_completed_packages(scan_targets)
        for row_data in self.rows:
            packages = packages_by_id.get(row_data.get("id", ""), [])
            previous_packages = row_data.get("completed_packages", [])
            row_data["completed_packages"] = packages
            if previous_packages == packages:
                continue
            if row_data.get("status") == STATUS_NOT_CHECKED:
                row = self._row_for_data(row_data)
                if row is not None:
                    self.table.setCellWidget(row, 6, self._build_status_widget(row_data))

    def toggle_category(self, category):
        # 分类行采用可折叠分组交互：分类行始终显示，下面的软件行按分类隐藏或显示。
        # 这能保留大分类层级，又不会像完整树形表格那样挤占横向空间。
        if category in self.collapsed_categories:
            self.collapsed_categories.remove(category)
        else:
            self.collapsed_categories.add(category)
        self._refresh_category_visibility(category)

    def _refresh_category_visibility(self, category):
        category_row = self.category_rows.get(category)
        expanded = category not in self.collapsed_categories
        if category_row is not None:
            item = self.table.item(category_row, 0)
            if item is not None:
                data = item.data(Qt.UserRole) or {}
                data["expanded"] = expanded
                item.setData(Qt.UserRole, data)
                item.setIcon(self._row_builder.category_icon(data.get("folder_node"), expanded))
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            data = item.data(Qt.UserRole) if item else {}
            if isinstance(data, dict) and data.get("type") == "software" and data.get("category") == category:
                self.table.setRowHidden(row, not expanded)

    def _row_for_data(self, row_data):
        target_id = row_data.get("id", "")
        target_type = row_data.get("type", "")
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            data = item.data(Qt.UserRole) if item else None
            if isinstance(data, dict) and data.get("id") == target_id and data.get("type") == target_type:
                return row
        return None

    # ── 版本检测（委托给协调器） ──────────────────────────

    def check_single(self, software_id):
        self._check_coordinator.check_single(software_id)

    def cancel_check(self, software_id):
        """取消进行中的版本检测，让该行回到未检测状态。"""
        self._check_coordinator.cancel_check(software_id)

    def check_all(self):
        self._check_coordinator.check_all()

    def retry_check_with_proxy(self, software_id):
        """临时切换代理后重新检测指定软件的更新。"""
        self._check_coordinator.retry_check_with_proxy(software_id)

    def _on_check_result(self, software_id, result):
        self._check_coordinator.on_check_result(software_id, result)

    def _on_check_failed(self, software_id, error):
        self._check_coordinator.on_check_failed(software_id, error)

    def _on_check_retrying(self, software_id, name, attempt):
        self._check_coordinator.on_check_retrying(software_id, name, attempt)

    def _on_all_checks_completed(self):
        self._check_coordinator.on_all_checks_completed()

    # ── 下载操作（委托给协调器） ──────────────────────────



    def _copy_download_link(self, software_id):
        self._download_coordinator.copy_download_link(software_id)

    def download_single(self, software_id):
        self._auto_action_coordinator.cancel(software_id)
        self._download_coordinator.download_single(software_id)

    def _auto_download_single(self, software_id):
        """倒计时结束后直接提交下载，不把状态栏短暂重绘为普通下载按钮。"""

        if self._auto_action_coordinator.download_enabled:
            self._download_coordinator.download_single(software_id, automatic=True)

    def _cancel_auto_action(self, software_id):
        """取消当前检测结果的自动操作，并保留手动下载或入队能力。"""

        row_data = self.row_by_id.get(software_id)
        if row_data is None:
            return
        self._auto_action_coordinator.cancel(software_id)
        self._check_results.pop(software_id, None)
        row_data.pop("_detected_local_arch", None)
        row_data.pop("_arch_auto_switched", None)
        self._set_status(row_data, STATUS_NOT_CHECKED)
        self._append_info(
            f"{row_data['node'].get('name', '')}：已取消自动操作，可重新检测更新。",
            level="warning",
        )

    def _retry_pending_download(self):
        self._download_coordinator._retry_pending_download()

    def download_updates(self):
        self._download_coordinator.download_updates()

    def pause_single(self, software_id):
        self._download_coordinator.pause_single(software_id)

    def resume_single(self, software_id):
        self._download_coordinator.resume_single(software_id)

    def retry_with_proxy(self, software_id):
        self._download_coordinator.retry_with_proxy(software_id)

    def cancel_single(self, software_id):
        self._download_coordinator.cancel_single(software_id)

    def pause_all(self):
        self._download_coordinator.pause_all()

    def cancel_all(self):
        self._auto_action_coordinator.cancel_all(suppress=True)
        self._download_coordinator.cancel_all()

    def resume_all(self):
        self._download_coordinator.resume_all()

    def _on_task_status_changed(self, task):
        self._download_coordinator.on_task_status_changed(task)

    def _do_download(self, software_id, row_data):
        self._download_coordinator._do_download(software_id, row_data)

    def refresh_proxy_state(self):
        self._download_coordinator.refresh_proxy_state()

    def _on_task_completed(self, task):
        self._download_coordinator.on_task_completed(task)

    def _on_task_failed(self, task):
        self._download_coordinator.on_task_failed(task)

    def open_download_dir(self):
        """打开下载根目录。"""
        from modules.platform_services.windows_shortcuts import open_folder_in_explorer
        directory = self.download_manager.normalized_download_dir("")
        open_folder_in_explorer(directory)
        self._append_info(f"已打开下载根目录：{directory}", level="success")

    def open_proxy_settings(self):
        """通过既有设置入口直接打开代理页，不创建额外窗口。"""
        if callable(self.open_settings_callback):
            self.open_settings_callback("proxy")

    def open_settings(self):
        """打开下载设置。"""
        if callable(self.open_settings_callback):
            self.open_settings_callback("download")
        self._append_info("已打开设置窗口，可在“下载设置”里调整下载目录和 aria2 参数。", level="success")

    def open_software_dir(self, row_data):
        """打开软件的下载目录。"""
        name = row_data["node"].get("name", "未命名软件")
        directory = self._software_download_dir(row_data)
        if directory.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))
        self._append_info(f"{name} 下载目录：{directory}")

    def show_download_info(self, row_data):
        node = row_data["node"]
        software_id = node.get("id", "")
        name = node.get("name", "未命名软件")
        check_result = self._check_results.get(software_id, {})
        downloads = check_result.get("downloads") or None
        multi_info = ""
        if downloads and isinstance(downloads, list):
            total = len(downloads)
            index = row_data.get("_multi_download_index", -1) + 1
            multi_info = f"\n多文件进度：{min(index, total)}/{total}"
        lines = [
            f"[{name}] 下载信息",
            f"状态：{STATUS_TEXT.get(row_data.get('status'), '未知状态')}",
            f"当前版本：{self._current_version_text(node)}",
            f"最新版本：{check_result.get('latest_version', '--')}",
            f"文件名：{check_result.get('filename', '--')}",
            f"下载方式：{self._download_mode_text(current_download_mode(row_data))}",
            f"保存目录：{self._software_download_dir(row_data)}",
        ]
        if multi_info:
            lines.insert(2, multi_info)
        self._append_info("\n".join(lines))

    def _software_download_dir(self, row_data):
        # 下载、更新扫描和右键打开目录统一使用服务层路径规则，避免名称含非法字符时指向不同目录。
        name = row_data["node"].get("name", "未命名软件")
        return software_download_directory(self.download_manager, name)

    def _append_info(self, text, level="info"):
        """委托给 InfoPanel.append_info。保留此方法避免修改其他调用方。"""
        self.info_panel.append_info(text, level)

    def _restore_paused_states(self):
        """委托恢复协调器重建跨重启下载状态，保留旧页面调用入口。"""
        self._state_restore_coordinator.restore()

    def _current_version_text(self, node):
        """获取当前版本显示文本。

        优先从已完成的检测结果中读取实际版本号；
        仅当没有检测结果时，才回退到配置中的 manual_version。
        """
        software_id = node.get("id", "")
        check_result = self._check_results.get(software_id, {})
        current_version = check_result.get("current_version")
        if current_version and str(current_version).strip() not in ("", "--"):
            return current_version_display(current_version)
        version_check = node.get("version_check", {})
        if version_check.get("method") == "manual":
            return current_version_display(version_check.get("manual_version"))
        return current_version_display(None)

    def _on_arch_mismatch_switch(self, row_data, arch):
        self._auto_action_coordinator.cancel(row_data.get("id", ""))
        self._column_handler.on_arch_mismatch_switch(row_data, arch)

    def _on_arch_mismatch_keep(self, row_data):
        self._auto_action_coordinator.cancel(row_data.get("id", ""))
        self._column_handler.on_arch_mismatch_keep(row_data)

    def _on_download_mode_changed(self, row_data, index):
        """兼容旧调用：将下拉索引转换为当前控件保存的方式数据。"""
        row = self._row_for_data(row_data)
        combo = self.table.cellWidget(row, 5) if row is not None else None
        mode = combo.itemData(index) if hasattr(combo, "itemData") else "direct"
        self._column_handler._on_download_mode_changed(row_data, mode)

    def confirm_download_mode_switch(self, row_data, target_mode):
        """在用户主动切换下载方式前显示该软件配置的确认提示。"""
        return self._download_mode_switch_prompt.confirm_switch(row_data, target_mode, self)

    def _download_mode_text(self, mode):
        return self._column_handler.download_mode_text(mode)

    def _category_icon(self, folder_node=None):
        return self._row_builder.category_icon(folder_node)

    def _software_icon(self, node):
        return self._row_builder.software_icon(node)

    def resizeEvent(self, event):
        # 窗口变宽后延迟一帧补齐表格宽度，避免右侧出现未使用空白。
        super().resizeEvent(event)
        QTimer.singleShot(0, self._table_layout.fill_remaining_table_width)

    def set_light_theme(self, enabled, force=False):
        """应用下载页主题；force 用于全局主题切换时重套现有控件 QSS。"""
        if self.light_theme == enabled and not force:
            return
        self.light_theme = enabled
        self.style_mgr.set_light_theme(enabled)
        # 多文件选择器使用局部 QSS，不会从下载页样式自动继承主题。已创建的下拉弹窗仍由
        # 单元格持有，因此主题切换时必须同步刷新，不能等用户重启或重新检测后才恢复。
        from modules.common_widgets.multi_file_selector import MultiFileSelector

        for selector in self.findChildren(MultiFileSelector):
            selector.set_light_theme(enabled)
        self._row_builder.refresh_category_icons()
        self._row_builder.refresh_software_name_icons()

    def apply_scale(self):
        self.style_mgr.apply_scale()

    def _font_family(self):
        """返回当前下载页应该使用的主界面字体。"""
        if self.scale_manager is not None and hasattr(self.scale_manager, "_resolved_font"):
            return self.scale_manager._resolved_font()
        return "Microsoft YaHei"
