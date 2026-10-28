from __future__ import annotations

from modules.release_identity import DISPLAY_NAME
import time
from pathlib import Path

from PySide6.QtCore import QTimer, Qt, Slot
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QStackedWidget,
    QTabBar,
)

from modules.config_manager import ConfigManager, DownloadConfigManager
from modules.config_services.software_item_tree import find_software_node
from modules.config_services.software_items_manager import SoftwareItemsConfigManager
from .update_context_actions import UpdateContextActions
from .update_confirmations import confirm_windows_restart
from modules.update_services import UpdatePackageScanner
from modules.update_services.update_settings_manager import UpdateSettingsManager
from modules.update_services.update_workspace import ensure_update_workspace
from .info_panel import InfoPanel
from .main_page_workspace import create_main_page_workspace
from .update_icon_helper import themed_update_software_icon
from .update_page_styles import UpdatePageStyleManager
from .update_preflight_controller import UpdatePreflightController
from .update_queue_controller import UpdateQueueController
from .update_row_builder import append_update_row
from .update_scan_rows import build_update_scan_rows
from .update_status_widget_factory import install_update_status_cell
from .update_tabs import rebuild_update_tabs
from .update_thread_runner import start_update_worker
from .update_table_layout import UpdateTableLayoutManager
from .page_action_buttons import (
    create_page_icon_button as _shared_icon_button,
    create_page_text_button as _shared_text_button,
)


STATUS_READY = "ready"
STATUS_NO_PLAN = "no_plan"
STATUS_UPDATING = "updating"
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"


class SoftwareUpdatePage(QFrame):
    """主窗口“软件更新”页面。

    主要作用：扫描统一下载目录下已存在的更新包，按用户定义的显示位置分组展示，并执行软件项里的更新方案。
    输入参数：parent 是 ui_main.py 里的软件更新页；scale_manager 用来跟随全局缩放。
    返回值：无。
    副作用：创建目录、删除安装包、执行更新按钮会修改用户文件或运行脚本；普通扫描只读取文件系统。
    """

    def __init__(self, parent=None, scale_manager=None, software_item_dialog_factory=None, runtime=None, host_mode=False):
        super().__init__(parent)
        self.setObjectName("softwareUpdatePage")
        self.setAttribute(Qt.WA_StyledBackground, True)
        # 更新结束后需要通知常驻运行时重新评估主窗口是否可释放；页面不自行管理待机计时器。
        self.runtime = runtime
        # 主机模式只过滤自定义显示位置；“全部软件”标签始终由 update_tabs 保留。
        self.host_mode = bool(host_mode)
        self.scale_manager = scale_manager
        self.software_manager = SoftwareItemsConfigManager()
        self.config_manager = ConfigManager()
        self.download_manager = DownloadConfigManager()
        self.update_settings_manager = UpdateSettingsManager()
        self.scanner = UpdatePackageScanner(self.download_manager, self.software_manager, self.update_settings_manager)
        self.light_theme = False
        self.style_mgr = UpdatePageStyleManager(self)
        # 更新页的多个标签共享同一套用户列宽，避免每个显示位置都重新调整一次。
        self._table_layout = UpdateTableLayoutManager(self)
        self._update_actions = UpdateContextActions(self, software_item_dialog_factory)
        self._preflight = UpdatePreflightController(self)
        self.rows = []
        self.row_by_id = {}
        self.tables = {}
        self.tab_buttons = []
        self.tab_position_ids = []
        self.position_items = []
        self.current_position_id = ""
        self._workers = {}
        self._threads = {}
        # 进程关闭预检与正式更新使用独立线程集合，避免关闭阶段卡住界面，也避免
        # 预检完成时误清理正式更新 worker 的对象引用。
        self._preflight_workers = {}
        self._preflight_threads = {}
        self._update_queue = UpdateQueueController(self)
        self._update_log_paths = {}
        self._active_update_id = None
        self._update_started_at = 0.0
        self._last_update_log_line = ""
        self._last_update_report_at = 0.0
        self._update_cancel_path = None
        self._update_log_path = None
        self._update_monitor_timer = QTimer(self)
        self._update_monitor_timer.setInterval(1000)
        self._update_monitor_timer.timeout.connect(self._poll_update_progress)
        self._build_ui()
        self._initial_scan_pending = True
        # 先切换到更新页骨架，再扫描目录并创建大量表格控件，避免首次点击导航时阻塞。
        QTimer.singleShot(0, self._run_initial_scan)

    def _run_initial_scan(self):
        """在更新页首次显示后执行一次初始更新包扫描。

        参数：无。返回值：无。
        副作用：读取下载目录、重建位置标签和更新表格；不会执行更新或修改用户文件。
        调用限制：由构造函数单次排队；若用户已手动触发扫描则直接跳过，避免重复扫描。
        """
        if self._initial_scan_pending:
            self.scan_packages()

    def set_host_mode(self, enabled):
        """切换更新页的主机模式标签过滤并重新扫描更新包。

        参数：enabled 为当前主机模式状态。返回值：无。
        副作用：重建更新页标签和表格行；不会修改更新设置或软件文件。
        调用限制：由主窗口在模式切换完成后调用，避免页面自行读取全局 UI 配置。
        """
        enabled = bool(enabled)
        if self.host_mode == enabled:
            return
        self.host_mode = enabled
        self.scan_packages()

    def _build_ui(self):
        workspace = create_main_page_workspace(self)
        self.root_layout = workspace.root_layout
        left_layout = workspace.left_layout
        right_layout = workspace.right_layout
        self.right_panel = workspace.right_panel

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(4)
        self.scan_btn = _shared_text_button(self, "检测更新包", "重新扫描下载目录里的更新包。")
        self.create_dirs_btn = _shared_text_button(self, "创建软件目录", "为所有启用软件创建对应的下载/离线更新目录。")
        self.open_root_btn = _shared_text_button(self, "打开下载根目录", f"打开 {DISPLAY_NAME} 统一下载目录。")
        toolbar.addWidget(self.scan_btn)
        toolbar.addWidget(self.create_dirs_btn)
        toolbar.addWidget(self.open_root_btn)
        toolbar.addStretch(1)
        left_layout.addLayout(toolbar)

        self.tab_bar = QTabBar(self)
        self.tab_bar.setObjectName("updatePositionTabBar")
        self.tab_bar.setExpanding(False)
        self.tab_bar.setDrawBase(False)
        self.tab_bar.currentChanged.connect(self._handle_tab_changed)
        left_layout.addWidget(self.tab_bar)

        self.table_stack = QStackedWidget(self)
        left_layout.addWidget(self.table_stack, 1)

        toolbar_right = QHBoxLayout()
        toolbar_right.setContentsMargins(0, 0, 0, 0)
        toolbar_right.setSpacing(4)
        self.clear_info_btn = _shared_icon_button(self, ":/icons/images/icons/cil-x-circle.png", "清空更新信息")
        toolbar_right.addStretch(1)
        toolbar_right.addWidget(self.clear_info_btn)
        right_layout.addLayout(toolbar_right)

        saved_font_delta = int(self.config_manager.load().get("ui", "info_font_delta", fallback="0"))
        self.info_panel = InfoPanel(
            self, log_category="\u66f4\u65b0",
            placeholder_text="更新信息会显示在这里。",
            default_text=(
                "软件更新使用说明\n\n"
                f"1. 点击\u201c检测更新包\u201d，{DISPLAY_NAME} 会扫描统一下载目录里的安装包或压缩包。\n"
                "2. 只有检测到更新包的软件才会显示在表格中。\n"
                "3. 有更新方案的软件可点击\u201c更新\u201d自动执行；没有更新方案的软件只提供打开下载目录。\n"
                "4. 更新界面的目录和软件下载界面共用：Downloads\\winbox下载\\软件名称。"
            ),
            zoom_out_tooltip="缩小更新信息文字",
            zoom_in_tooltip="放大更新信息文字",
            light_theme=self.light_theme,
            scale_manager=self.scale_manager,
            initial_font_delta=saved_font_delta,
            save_font_delta_callback=self._save_info_font_delta,
        )
        right_layout.addWidget(self.info_panel, 1)

        self.scan_btn.clicked.connect(self.scan_packages)
        self.create_dirs_btn.clicked.connect(self._update_actions.create_directories)
        self.open_root_btn.clicked.connect(self._update_actions.open_download_root)
        self.clear_info_btn.clicked.connect(self.info_panel.clear)
        self.style_mgr._apply_styles()
        self.style_mgr.apply_scale()

    def _save_info_font_delta(self, delta):
        """保存信息框字体缩放值到配置，下次启动恢复。"""
        config = self.config_manager.load()
        config.set("ui", "info_font_delta", str(delta))
        self.config_manager.save(config)

    def _rebuild_tabs(self):
        """委托标签 helper 按当前更新设置重建显示位置和空表格。"""
        rebuild_update_tabs(self)

    def _handle_tab_changed(self, index):
        if 0 <= index < len(self.tab_position_ids):
            self._select_position(self.tab_position_ids[index])

    def _select_position(self, position_id):
        self.current_position_id = position_id
        if position_id in self.tab_position_ids:
            index = self.tab_position_ids.index(position_id)
            if self.tab_bar.currentIndex() != index:
                self.tab_bar.setCurrentIndex(index)
        table = self.tables.get(position_id)
        if table:
            self.table_stack.setCurrentWidget(table)
            header = table.horizontalHeader()
            fit_to_viewport = getattr(header, "fit_to_viewport", None)
            if fit_to_viewport:
                # QStackedWidget 切换后当前表格才拿到真实视口宽度，延迟贴边避免右侧留白。
                QTimer.singleShot(0, fit_to_viewport)

    def scan_packages(self):
        self._initial_scan_pending = False
        self._rebuild_tabs()
        self.rows = []
        self.row_by_id = {}
        records = self.scanner.scan()
        valid_positions = {item.get("id") for item in self.position_items}
        default_position = self.update_settings_manager.default_position_id()
        self._append_incomplete_download_warnings()
        self._append_unverified_windows_package_warnings()
        for record in records:
            scan_rows = build_update_scan_rows(
                record,
                valid_positions,
                default_position,
                STATUS_READY,
                STATUS_NO_PLAN,
            )
            task_status = self._update_queue.status(record.software_id)
            if task_status:
                for data in [*scan_rows['rows'], scan_rows['all_row'], scan_rows['primary_row']]:
                    data['status'] = task_status
            for row_data in scan_rows["rows"]:
                self.rows.append(row_data)
                self._append_row(row_data)
            self.row_by_id[record.software_id] = scan_rows["primary_row"]

            all_row = scan_rows["all_row"]
            self.rows.append(all_row)
            self._append_row(all_row)
            self._append_record_scan_notes(record)
        self._append_info(f"检测完成：发现 {len(records)} 个有更新包的软件。")

    def _append_record_scan_notes(self, record):
        """追加单条扫描记录的辅助提示。

        参数：record 是 UpdateScanRecord。
        返回值：无。
        副作用：只向右侧信息面板追加提示文本，不修改表格和配置。
        """
        if record.package_count > 1:
            self._append_info(f"{record.name}：发现 {record.package_count} 个更新包，已自动按系统架构选择：{record.package_path.name}")
        if record.renamed_dir:
            self._append_info(f"{record.name}：软件名包含 Windows 不允许的目录字符，实际目录为 {record.directory.name}", level="warning")

    def _append_incomplete_download_warnings(self):
        """显示因 aria2 未完成下载而跳过的软件目录提示。

        参数：无。
        返回值：无。
        副作用：只向右侧信息面板追加 warning 文本，不修改扫描结果或配置。
        """
        for skipped in getattr(self.scanner, "skipped_incomplete_downloads", []):
            self._append_info(
                f"{skipped.get('name')}：检测到未完成下载（目录内存在 .aria2 文件），已跳过更新包扫描。",
                level="warning",
            )

    def _append_unverified_windows_package_warnings(self):
        """提示因缺少 Catalog 确认单而暂不显示的 Windows 月度补丁。

        Windows 24H2/25H2 可能共用同一 KB 文件，扫描器不能只根据文件名推断适用
        版本。提示用户在下载页重新检测并点击下载，已有同名文件会补写确认单而不
        重复下载，避免安全过滤变成无提示的“检测不到”。
        """

        for skipped in getattr(self.scanner, "skipped_unverified_windows_packages", []):
            package = skipped.get("package")
            filename = package.name if isinstance(package, Path) else "补丁包"
            self._append_info(
                f"{skipped.get('name', 'Windows 月度更新')}：发现 {filename}，但缺少或不匹配 "
                ".winbox-update.json 确认单，暂不显示为可安装更新。"
                f"请到下载页面重新检测该项后点击下载；若文件仍匹配，{DISPLAY_NAME} 会跳过重复下载并补写确认单。",
                level="warning",
            )

    def _append_row(self, row_data):
        """委托行构建器追加更新包行，页面类只保留扫描和状态编排职责。"""
        append_update_row(self, row_data)

    def _install_status_cell(self, row_data):
        """委托状态列工厂安装更新按钮/标签，避免页面类继续承载控件细节。"""
        install_update_status_cell(self, row_data)

    def _fresh_software_node(self, software_id):
        """从 software_items.json 重新读取指定软件的节点数据。

        主要作用：用户在设置窗口改了更新方案后，更新页面还持有旧的内存数据。
        这个方法在每次点击"更新"前调用，确保拿到最新配置。
        参数：software_id 是软件的 UUID。
        返回值：找到返回节点 dict，找不到返回 None。
        副作用：读取一次 JSON 文件。
        """
        if not software_id:
            return None
        full_config = self.software_manager.load()
        return find_software_node(full_config.get("items", []), software_id)

    def start_update(self, row_data):
        if self._update_queue.contains(row_data.get('id', '')):
            return
        # 从配置文件重新读取节点数据，确保更新方案是用户最新保存的
        software_id = row_data.get("id", "")
        fresh_node = self._fresh_software_node(software_id)
        if fresh_node is not None:
            row_data["node"] = fresh_node
        update_config = self.software_manager.normalize_update_config(row_data.get("node", {}).get("update", {}))
        strategy = update_config.get("strategy", "download_only")
        if strategy == "download_only":
            self._append_info(f"{row_data.get('name')}：没有配置更新方案。", level="warning")
            self._set_rows_status(software_id, STATUS_NO_PLAN)
            return
        package_path = Path(row_data.get("package_path", ""))
        if not package_path.is_file():
            self._append_info(f"{row_data.get('name')}：更新包不存在：{package_path}", level="error")
            return
        self._update_queue.enqueue(
            row_data, exclusive_key='windows_patch' if self._is_windows_patch_update(update_config) else ''
        )

    def _begin_queued_update(self, row_data):
        """获得名额后再重检安装包，并执行点击时的更新方案。"""
        software_id = row_data.get('id', '')
        package_path = Path(row_data.get('package_path', ''))
        if not package_path.is_file():
            self._set_rows_status(software_id, STATUS_FAILED)
            self._append_info(f"{row_data.get('name')}：更新包已不存在：{package_path}", level='error')
            return
        update_config = self.software_manager.normalize_update_config(row_data.get('node', {}).get('update', {}))
        strategy = update_config.get('strategy', 'download_only')
        self._preflight.begin(
            row_data,
            package_path,
            update_config,
            strategy,
            self._start_update_after_preflight,
        )

    def _start_update_after_preflight(self, row_data, update_config, strategy):
        """在异步进程预检通过后启动正式更新 worker。

        参数：row_data 是当前更新行；update_config 是已同步运行时标记的配置；strategy
        是更新策略。返回值：无。副作用：把状态改为 updating、可能创建 Windows 补丁
        工作区，并启动实际执行安装/复制脚本的后台线程。

        调用限制：只能由 UpdatePreflightController 在主线程调用。进程关闭尚未完成时
        不会进入这里，避免安装器和关闭动作并发争抢目标文件。
        """

        software_id = row_data.get("id", "")

        is_windows_patch = self._is_windows_patch_update(update_config)
        if is_windows_patch and self._active_update_id is not None:
            # DISM 使用系统级 servicing engine，同时安装多个补丁可能互相锁定或破坏安装状态。
            # 普通安装包仍然可以和其他普通安装包并行运行。
            self._append_info(
                f"{row_data.get('name')}：已有 Windows 补丁任务正在执行，请等待它完成后再继续。",
                level="warning",
            )
            self._set_rows_status(software_id, STATUS_READY)
            return

        self._set_rows_status(software_id, STATUS_UPDATING)
        if is_windows_patch:
            self._active_update_id = software_id
            self._update_started_at = time.monotonic()
            self._last_update_log_line = ""
            self._last_update_report_at = 0.0
            if self._update_cancel_path is not None:
                try:
                    self._update_cancel_path.unlink(missing_ok=True)
                except OSError:
                    pass
            # 必须在启动 worker 前创建工作区：页面的日志轮询和取消操作与提权脚本共享
            # 第五个参数目录，避免读取其他更新遗留的全局临时文件。
            workspace = ensure_update_workspace(update_config, row_data.get("name", "WindowsPatch"))
            row_data["_update_workspace_dir"] = str(workspace)
            self._update_cancel_path = workspace / "WinBox_WindowsPatch.cancel"
            from user_scripts.update.diagnostic_paths import diagnostic_log_path
            self._update_log_path = diagnostic_log_path(workspace, "WinBox_WindowsPatch.log")
            self.info_panel.add_diagnostic_reference(self._update_log_path, workspace)
            try:
                self._update_cancel_path.unlink(missing_ok=True)
            except OSError:
                pass
            self._update_monitor_timer.start()
        message = f"{row_data.get('name')}：开始执行更新方案 {strategy}。"
        if is_windows_patch:
            message += "大型补丁在老电脑上可能需要较长时间，单次 DISM 最长等待 2 小时；界面会持续显示运行状态。"
        self._append_info(message)
        start_update_worker(self, row_data)

    def _is_windows_patch_update(self, update_config):
        """判断当前更新配置是否使用内置 Windows 补丁安装脚本。

        参数：update_config 是已标准化的更新配置。
        返回值：使用 ``安装Windows补丁.py`` 时返回 True，否则返回 False。
        副作用：只解析配置中的脚本路径，不启动脚本、不修改界面或配置。
        """
        script_value = str(update_config.get("update_script", "") or "")
        script_path = self.software_manager.resolve_path(script_value) if script_value else None
        return bool(script_path and script_path.name == "安装Windows补丁.py")

    def can_cancel_update(self, row_data):
        """返回当前更新项是否支持安全停止。

        参数：row_data 是更新表格行数据。
        返回值：仅 Windows 补丁脚本返回 True，因为它会读取取消标记并安全停止 DISM。
        副作用：无；只读取当前行内存配置。
        """
        update_config = self.software_manager.normalize_update_config(
            row_data.get("node", {}).get("update", {})
        )
        return self._is_windows_patch_update(update_config)

    def _on_update_progress(self, software_id, text):
        """接收后台脚本进度标记行，在信息窗口实时显示，避免长时间无反馈被误认为卡死。"""
        row_data = self.row_by_id.get(software_id)
        name = row_data.get("name", "") if row_data else ""
        self._append_info(f"{name}：{text}" if name else text)

    def _on_update_finished(self, software_id, success, message, log_path="", restart_reasons=None):
        """接收后台更新结果，保存日志路径并刷新同一软件的所有状态栏。

        参数：software_id 是软件 ID；success 是更新结果；message 是用户可读说明；log_path
        是本次持久更新日志路径。返回值：无。
        副作用：更新行状态、信息面板和内存日志路径映射；不修改软件配置文件。
        """
        if software_id == self._active_update_id:
            elapsed = int(time.monotonic() - self._update_started_at) if self._update_started_at else 0
            self._update_monitor_timer.stop()
            self._active_update_id = None
            self._append_info(f"更新任务结束，耗时 {elapsed // 60} 分 {elapsed % 60} 秒。")
        row_data = self.row_by_id.get(software_id)
        self._set_rows_status(software_id, STATUS_SUCCESS if success else STATUS_FAILED)
        if row_data:
            if log_path:
                self._update_log_paths[software_id] = Path(log_path)
                self.info_panel.add_diagnostic_reference(log_path)
            self._append_info(f"{row_data.get('name')}：{message}", level="success" if success else "error")
            if success and restart_reasons:
                if confirm_windows_restart(row_data.get("name", "VMware"), restart_reasons, self):
                    from modules.platform_services.windows_restart import restart_windows

                    restarted, restart_message = restart_windows()
                    self._append_info(restart_message, level="warning" if restarted else "error")
                else:
                    self._append_info("已选择稍后重启。请在重启 Windows 后再启动 VMware。", level="warning")

    @Slot(str)
    def _on_update_thread_finished(self, software_id):
        """线程完成资源清理后释放名额，不在结果信号到达时提前启动下一件。"""
        if self._update_queue.status(software_id) == STATUS_UPDATING:
            self._set_rows_status(software_id, STATUS_FAILED)
            self._append_info('更新线程已退出，但未返回结果，请查看日志。', level='error')
        self._workers.pop(software_id, None)
        self._threads.pop(software_id, None)
        self._update_queue.finish(software_id)

    def has_background_activity(self):
        """等待、预检和更新均阻止页面或窗口被自动释放。"""
        return bool(self._update_queue.busy or self._threads or self._preflight_threads)

    def cancel_queued_update(self, row_data):
        """取消尚未开始的更新，恢复所有标签中的按钮。"""
        self._update_queue.cancel(row_data.get('id', ''))

    def open_update_log_folder(self, row_data):
        """在资源管理器中打开当前软件最近一次更新日志所在目录。

        参数：row_data 是状态栏按钮对应的软件行数据。返回值：无。
        副作用：启动资源管理器并向信息面板追加结果；日志缺失时只显示明确错误，不执行更新。
        """
        software_id = row_data.get("id", "")
        log_path = self._update_log_paths.get(software_id)
        if log_path is None or not log_path.is_file():
            self._append_info(
                f"{row_data.get('name', '该软件')}：未找到本次更新日志，可能日志目录不可写或日志已被清理。",
                level="error",
            )
            return
        try:
            from modules.platform_services.windows_shortcuts import open_folder_in_explorer

            open_folder_in_explorer(log_path.parent)
            self._append_info(f"已打开更新日志目录：{log_path.parent}")
        except OSError as exc:
            self._append_info(f"打开更新日志目录失败：{exc}", level="error")

    def cancel_update(self, row_data):
        """请求停止当前 Windows 补丁更新。

        通过临时取消标记通知提权脚本；脚本会终止 DISM、恢复服务并清理临时目录。
        这里只发出请求，不直接强杀外部进程，避免服务状态没有机会恢复。
        """
        software_id = row_data.get("id", "")
        if software_id != self._active_update_id:
            return
        if not self.can_cancel_update(row_data):
            return
        if self._update_cancel_path is None:
            self._append_info("本次补丁任务未创建可取消的工作目录。", level="error")
            return
        try:
            self._update_cancel_path.touch()
        except OSError as exc:
            self._append_info(f"无法创建停止请求：{exc}", level="error")
            return
        self._append_info(
            f"{row_data.get('name')}：已请求停止，正在等待 DISM 安全退出并恢复服务。",
            level="warning",
        )

    def _poll_update_progress(self):
        """读取 Windows 补丁脚本心跳日志，向信息窗口报告阶段和耗时。"""
        software_id = self._active_update_id
        if software_id is None:
            self._update_monitor_timer.stop()
            return
        row_data = self.row_by_id.get(software_id)
        name = row_data.get("name", "补丁") if row_data else "补丁"
        elapsed = int(time.monotonic() - self._update_started_at)
        latest = ""
        try:
            if self._update_log_path is not None and self._update_log_path.is_file():
                lines = [line.strip() for line in self._update_log_path.read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines() if line.strip()]
                if lines:
                    latest = lines[-1]
        except OSError:
            latest = "正在等待提权安装脚本启动..."
        now = time.monotonic()
        if latest and latest != self._last_update_log_line:
            self._last_update_log_line = latest
            self._last_update_report_at = now
            self._append_info(f"{name}：已运行 {elapsed // 60:02d}:{elapsed % 60:02d}，{latest}")
        elif now - self._last_update_report_at >= 10:
            self._last_update_report_at = now
            self._append_info(
                f"{name}：仍在运行，已耗时 {elapsed // 60:02d}:{elapsed % 60:02d}。"
                "DISM 可能长时间没有百分比变化，请勿重复点击更新。"
            )

    def _set_rows_status(self, software_id, status):
        self._update_queue.record_status(software_id, status)
        for row_data in self.rows:
            if row_data.get("id") == software_id:
                row_data["status"] = status
                self._install_status_cell(row_data)

    def _append_info(self, text, level="info"):
        """委托给 InfoPanel.append_info。保留此方法避免修改其他调用方。"""
        self.info_panel.append_info(text, level)

    def set_light_theme(self, enabled, force=False):
        """应用更新页主题；force 用于全局主题切换时重套现有控件 QSS。"""
        if self.light_theme == enabled and not force:
            return
        self.light_theme = enabled
        self.style_mgr.set_light_theme(enabled)
        self._rebuild_software_icons()

    def _rebuild_software_icons(self):
        """主题切换后刷新所有软件行的图标。"""
        for row_data in self.rows:
            table = row_data.get("table")
            row = row_data.get("row")
            node = row_data.get("node", {})
            if table is None or row is None:
                continue
            item = table.item(row, 0)
            if item:
                item.setIcon(themed_update_software_icon(node, self.software_manager, self.light_theme))

    def apply_scale(self):
        self.style_mgr.apply_scale()
