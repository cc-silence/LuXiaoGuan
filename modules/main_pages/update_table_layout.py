"""更新页表格列宽管理。

主要作用：统一保存和恢复软件更新页各标签表格的用户列宽，并在用户调整任意标签
的可调列后同步到其他更新标签。状态列的右边缘由 LeftResizeHeaderView 自动贴住
容器，因此自动调整不能覆盖用户实际拖动得到的列宽偏好。
"""

from PySide6.QtCore import QTimer


class UpdateTableLayoutManager:
    """管理更新页所有表格的共享列宽状态。

    参数：page 是 SoftwareUpdatePage，提供 tables、config_manager 和 scale_manager。
    副作用：用户拖动可调列后会延迟写入 settings.ini，并同步修改其他标签页表格。
    调用限制：每个更新表格只应调用一次 configure_table()，表格必须已安装
    LeftResizeHeaderView 才能识别自动贴边期间的 sectionResized 信号。
    """

    CONFIG_SECTION = "software_update_table"
    WIDTHS_VERSION = "1"
    RESIZABLE_COLUMNS = (0, 1, 3)

    def __init__(self, page):
        """初始化列宽保存与跨标签同步计时器，不立即读写配置。"""
        self._page = page
        self._applying_widths = False
        self._pending_source = None
        self._sync_timer = QTimer(page)
        self._sync_timer.setSingleShot(True)
        self._sync_timer.setInterval(0)
        self._sync_timer.timeout.connect(self._sync_pending_source)
        self._save_timer = QTimer(page)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(400)
        self._save_timer.timeout.connect(self.save_column_widths)

    def configure_table(self, table):
        """恢复新建更新表格的列宽，并连接用户拖动后的同步入口。

        参数：table 是刚由 update_table_factory 创建的 QTableWidget。
        返回值：无。
        副作用：修改 table 列宽，连接 sectionResized；不立即写配置。
        """
        header = table.horizontalHeader()
        header.sectionResized.connect(
            lambda logical, old, new, source=table: self._on_section_resized(
                source, logical, old, new
            )
        )
        self._apply_saved_widths(table)

    def _default_widths(self):
        """返回按当前 UI 缩放计算的更新页默认列宽。"""
        scale = self._page.scale_manager
        px = scale.px if scale is not None else lambda value: value
        return {0: px(170), 1: px(300), 2: px(90), 3: px(220)}

    def _apply_saved_widths(self, table):
        """将已保存或默认列宽应用到一个新建标签表格。

        固定的文件时间列始终使用当前缩放的默认宽度；三列可调宽度优先读取用户保存值。
        最后排队调用表头贴边逻辑，确保状态列右侧没有空白。
        """
        saved_widths = self.load_column_widths()
        defaults = self._default_widths()
        self._applying_widths = True
        try:
            for column in (2, 0, 1, 3):
                table.setColumnWidth(column, saved_widths.get(column, defaults[column]))
        finally:
            self._applying_widths = False
        fit_to_viewport = getattr(table.horizontalHeader(), "fit_to_viewport", None)
        if fit_to_viewport:
            QTimer.singleShot(0, fit_to_viewport)

    def _on_section_resized(self, source, logical_index, _old_size, _new_size):
        """记录用户列拖动，并在本轮 Qt 布局结束后同步其他标签。

        LeftResizeHeaderView 自动贴边时也会触发本信号；通过 is_fitting_viewport 排除，
        否则窗口缩放会不断覆写用户的列宽设置。
        """
        header = source.horizontalHeader()
        if (
            self._applying_widths
            or getattr(header, "is_fitting_viewport", False)
            or logical_index not in self.RESIZABLE_COLUMNS
        ):
            return
        self._pending_source = source
        self._sync_timer.start()

    def _sync_pending_source(self):
        """把最近一次用户拖动的列宽同步到其他更新标签并安排保存。"""
        source = self._pending_source
        self._pending_source = None
        if source is None or source not in self._page.tables.values():
            return
        widths = {
            column: source.columnWidth(column)
            for column in self.RESIZABLE_COLUMNS
        }
        self._applying_widths = True
        try:
            for table in self._page.tables.values():
                if table is source:
                    continue
                for column, width in widths.items():
                    table.setColumnWidth(column, width)
        finally:
            self._applying_widths = False
        self._save_timer.start()

    def load_column_widths(self):
        """读取 settings.ini 中用户保存的更新页可调列宽。

        返回值：列号到宽度的字典；配置缺失、旧版本或非法值时返回空字典。
        副作用：只读配置文件，不修改表格或配置。
        """
        config = self._page.config_manager.load()
        if (
            not config.has_section(self.CONFIG_SECTION)
            or config.get(self.CONFIG_SECTION, "widths_version", fallback="") != self.WIDTHS_VERSION
        ):
            return {}
        widths = {}
        for column in self.RESIZABLE_COLUMNS:
            try:
                width = int(config.get(self.CONFIG_SECTION, f"column_{column}", fallback=""))
            except (TypeError, ValueError):
                continue
            if width >= 60:
                widths[column] = width
        return widths

    def save_column_widths(self):
        """保存当前更新标签表格的用户可调列宽到 settings.ini。

        优先使用当前可见表格；页面重建后没有表格时安全跳过。只保存用户可调列，
        文件时间固定列和窗口宽度驱动的自动贴边结果不单独持久化。
        """
        current = self._page.tables.get(self._page.current_position_id)
        if current is None and self._page.tables:
            current = next(iter(self._page.tables.values()))
        if current is None:
            return
        config = self._page.config_manager.load()
        if not config.has_section(self.CONFIG_SECTION):
            config.add_section(self.CONFIG_SECTION)
        config.set(self.CONFIG_SECTION, "widths_version", self.WIDTHS_VERSION)
        for column in self.RESIZABLE_COLUMNS:
            config.set(self.CONFIG_SECTION, f"column_{column}", str(current.columnWidth(column)))
        self._page.config_manager.save(config)
