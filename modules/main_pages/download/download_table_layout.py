"""下载页表格布局管理器

主要作用：集中管理软件下载页表格缩放、默认列宽、剩余宽度填充和列宽持久化，
让 SoftwareDownloadPage 保留页面组装和事件入口，不继续堆表格布局细节。

使用方式：页面创建 DownloadTableLayoutManager(page) 实例，并把原页面方法
委托到此管理器。此模块会读取和写入 config/settings.ini 中的列宽配置。
"""

from PySide6.QtCore import QSize, QTimer, QSignalBlocker
from PySide6.QtGui import QFont


class DownloadTableLayoutManager:
    """下载页表格布局管理器。

    输入参数：page 是 SoftwareDownloadPage 实例，管理器通过它访问 table、
    scale_manager、config_manager 和右侧信息面板控件。
    返回值：构造函数无返回值。
    副作用：apply_scale 会修改控件尺寸和字体；save_column_widths 会写配置文件。
    """

    def __init__(self, page):
        self._page = page
        self._logical_widths = None

    def apply_scale(self):
        """按当前缩放比例刷新表格、按钮和右侧信息区尺寸。"""
        page = self._page
        scale = page.scale_manager
        if scale is None:
            return
        scale.publish(page)
        scale.apply_bound_metrics(page)
        # 分类行使用线框展开符 + 文件夹的组合图标，需要比软件行图标略宽。
        # 软件行自己的 QLabel 仍保持 26px，避免改变下载项行的布局。
        page.table.setIconSize(QSize(scale.px(56), scale.px(32)))
        # 缩放初始化不是用户拖动，避免把默认列宽写回用户偏好。
        with QSignalBlocker(page.table.horizontalHeader()):
            page.table.horizontalHeader().setDefaultSectionSize(scale.px(110))
        page.table.verticalHeader().setDefaultSectionSize(scale.px(52))
        # 不用 table.setStyleSheet("font: ...")，避免覆盖下载页 QSS 中的背景和单元格规则。
        page.table.setStyleSheet("")
        table_font = QFont(page._font_family())
        table_font.setPointSize(scale.pt(10))
        page.table.setFont(table_font)
        # 分类行的文字和行高是逐行创建的，不能只依赖 QTableWidget 的默认字体。
        for row in page.category_rows.values():
            item = page.table.item(row, 0)
            if item is not None:
                item.setFont(table_font)
                page.table.setRowHeight(row, scale.px(40))
        self.apply_default_column_widths()

        page.right_panel.setMinimumWidth(scale.px(220))
        page.info_panel.info_box.setMinimumWidth(scale.px(220))
        for button in (
            page.open_dir_btn,
            page.settings_btn,
            page.clear_info_btn,
            page.info_panel.zoom_out_btn,
            page.info_panel.zoom_in_btn,
        ):
            button.setIconSize(QSize(scale.px(18), scale.px(18)))
            button.setMinimumSize(QSize(scale.px(34), scale.px(32)))
        page.style_mgr._apply_styles()
        page._row_builder.refresh_software_name_icons()

    def apply_default_column_widths(self):
        """按默认值和用户保存值设置表格列宽。"""
        page = self._page
        scale = page.scale_manager
        px = scale.px if scale is not None else lambda value: value
        if self._logical_widths is None:
            self._logical_widths = {0: 440, 1: 110, 2: 110, 3: 150, 4: 70, 5: 70, 6: 250, 7: 85}
            self._logical_widths.update(self.load_column_widths())
        widths = {column: px(width) for column, width in self._logical_widths.items()}
        page._applying_column_widths = True
        try:
            page.table.setColumnHidden(3, True)
            header = page.table.horizontalHeader()
            header.setMinimumSectionSize(px(60))
            header.set_preferred_widths(widths, {0: px(60), 6: px(250)})
            # 先设置固定列，避免架构/下载方式控件的尺寸测量反过来挤压可调列。
            column_order = tuple(column for column in (1, 2, 4, 5, 7, 0, 3, 6) if column < page.table.columnCount())
            for column in column_order:
                page.table.setColumnWidth(column, widths[column])
            self.fill_remaining_table_width()
        finally:
            page._applying_column_widths = False

    def fill_remaining_table_width(self):
        """调整表头列宽，确保状态列右边界贴住表格右侧。"""
        page = self._page
        if not hasattr(page, "table"):
            return
        header = page.table.horizontalHeader()
        fit_to_viewport = getattr(header, "fit_to_viewport", None)
        if fit_to_viewport:
            fit_to_viewport()

    def load_column_widths(self):
        """读取软件下载表格列宽配置。"""
        page = self._page
        config = page.config_manager.load()
        section = "software_download_table"
        if not config.has_section(section):
            return {}
        # 旧版本曾把自动补齐后的宽度写入配置，不能当成用户自定义宽度恢复。
        # 第 4 版保存 100% 逻辑宽度；旧像素宽度可能是自动压缩结果，恢复默认值。
        version = config.get(section, "widths_version", fallback="")
        if version not in {"5", "6", "7"}:
            return {}
        widths = {}
        for column in page._resizable_columns:
            value = config.get(section, f"column_{column}", fallback="").strip()
            try:
                width = int(value)
            except (TypeError, ValueError):
                continue
            if width >= 40:
                widths[column] = width
        if version == "5":
            try:
                filename_width = max(40, config.getint(section, "column_3", fallback=150))
            except ValueError:
                filename_width = 150
            widths[0] = widths.get(0, 220) + filename_width
        if version != "7":
            # 迁移旧版过窄的软件列，新版用户手动设置的宽度仍原样恢复。
            widths[0] = max(440, widths.get(0, 440))
        return widths

    def schedule_column_width_save(self, logical_index, old_size, new_size):
        """列宽改变后延迟保存，避免拖动过程中频繁写 settings.ini。"""
        page = self._page
        header = page.table.horizontalHeader()
        if (
            page._applying_column_widths
            or getattr(header, "is_fitting_viewport", False)
            or logical_index not in page._resizable_columns
        ):
            return
        if self._logical_widths is not None:
            scale = page.scale_manager.global_scale if page.scale_manager else 100
            self._logical_widths[logical_index] = max(40, round(new_size * 100 / scale))
            px = page.scale_manager.px if page.scale_manager else lambda value: value
            header.set_preferred_widths({column: px(width) for column, width in self._logical_widths.items()})
        # 软件或文件名列被手动调整后，状态列仍需贴住表格最右侧。
        QTimer.singleShot(0, self.fill_remaining_table_width)
        page._column_width_save_timer.start(400)

    def save_column_widths(self):
        """保存软件下载表格列宽到 config/settings.ini。"""
        page = self._page
        config = page.config_manager.load()
        section = "software_download_table"
        if not config.has_section(section):
            config.add_section(section)
        config.set(section, "widths_version", "7")
        for column in page._resizable_columns:
            if self._logical_widths is not None:
                config.set(section, f"column_{column}", str(self._logical_widths[column]))
        page.config_manager.save(config)
