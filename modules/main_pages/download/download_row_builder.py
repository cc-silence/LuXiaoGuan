"""下载页表格行构建器

主要作用：集中创建软件下载页里的分类行、软件行、软件名称列控件和图标，
让 SoftwareDownloadPage 不继续承载每一列怎么摆放的细节。

使用方式：页面创建 DownloadRowBuilder(page) 实例，页面上的原同名方法保留为委托。
此模块只修改表格显示状态和 page.rows/page.row_by_id，不写配置文件、不启动下载。
"""

from modules.config_manager import BASE_DIR
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPalette
from PySide6.QtWidgets import QHBoxLayout, QLabel, QTableWidgetItem, QWidget, QSizePolicy

from .download_status import STATUS_NOT_CHECKED
from modules.icon_utils import invert_icon_file
from modules.common_widgets.overflow_marquee_label import OverflowMarqueeLabel


class DownloadRowBuilder:
    """下载页表格行构建器。

    输入参数：page 是 SoftwareDownloadPage 实例，用于访问 table、scale_manager、
    software_manager、collapsed_categories 和页面委托方法。
    副作用：会向表格插入行，并更新 page.rows、page.row_by_id、page.category_rows。
    """

    def __init__(self, page):
        self._page = page
        # 分类图标缓存：(icon_value, expanded, light_theme) → QIcon
        # folder_node 是每次 reload_items 从 JSON 重新解析出的新 dict，旧对象释放后
        # id 会被新对象复用，不能做缓存键；图标只取决于 icon 路径、展开态和主题。
        self._category_icon_cache = {}

    def add_category_row(self, category_name, category_parts, folder_node=None):
        """插入一个分类行，并使用独立的线框展开图标。"""
        page = self._page
        row = page.table.rowCount()
        page.table.insertRow(row)
        expanded = category_name not in page.collapsed_categories
        item = QTableWidgetItem(category_name)
        item.setFont(self._category_font())
        item.setData(
            Qt.UserRole,
            {
                "type": "category",
                "category": category_name,
                "parts": category_parts,
                "expanded": expanded,
                "folder_node": folder_node,
            },
        )
        item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        item.setIcon(self.category_icon(folder_node, expanded))
        page.table.setItem(row, 0, item)
        for column in range(1, page.table.columnCount()):
            filler = QTableWidgetItem("")
            filler.setData(
                Qt.UserRole,
                {
                    "type": "category",
                    "category": category_name,
                    "parts": category_parts,
                    "expanded": expanded,
                    "folder_node": folder_node,
                },
            )
            filler.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            page.table.setItem(row, column, filler)
        page.table.setSpan(row, 0, 1, page.table.columnCount())
        page.table.setRowHeight(row, page.scale_manager.px(40) if page.scale_manager else 40)
        page.category_rows[category_name] = row

    def add_software_row(self, entry):
        """插入一个软件下载项行。"""
        page = self._page
        node = entry["node"]
        row = page.table.rowCount()
        page.table.insertRow(row)
        software_id = node.get("id", "")
        row_data = {
            "type": "software",
            "id": software_id,
            "row": row,
            "category": entry["category"],
            "node": node,
            "status": STATUS_NOT_CHECKED,
            "progress": 0,
            "progress_percent": 0.0,
            "download_speed": 0,
        }
        name_item = QTableWidgetItem("")
        notes = str(node.get("notes", "") or "").strip()
        if notes:
            name_item.setToolTip(notes)
        self.set_row_item(row, 0, name_item, row_data)
        page.table.setCellWidget(row, 0, self.software_name_widget(node))
        self.set_row_text(row, 1, page._current_version_text(node), row_data)
        self.set_row_text(row, 2, "--", row_data)
        self.set_row_text(row, 3, "--", row_data)
        if page.table.columnCount() > 7:
            self.set_row_text(row, 7, "--", row_data)
        page._column_handler.install_arch_column(row, row_data)
        hidden_item = QTableWidgetItem()
        hidden_item.setForeground(QColor(0, 0, 0, 0))
        self.set_row_item(row, 5, hidden_item, row_data)
        page._column_handler.install_download_mode_combo(row, row_data)
        status_item = QTableWidgetItem("")
        status_item.setForeground(QColor(0, 0, 0, 0))
        self.set_row_item(row, 6, status_item, row_data)
        page.table.setCellWidget(row, 6, page._build_status_widget(row_data))
        page.table.setRowHeight(row, page.scale_manager.px(54) if page.scale_manager else 54)
        page.table.setRowHidden(row, entry["category"] in page.collapsed_categories)
        page.rows.append(row_data)
        if software_id:
            page.row_by_id[software_id] = row_data

    def software_name_widget(self, node):
        """创建软件名称列内容。"""
        page = self._page
        widget = QWidget(page.table)
        widget.setAttribute(Qt.WA_StyledBackground, True)
        widget.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        layout = QHBoxLayout(widget)
        icon_label = QLabel(widget)
        icon_label.setObjectName("downloadSoftwareIcon")
        icon_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(icon_label)

        name_label = QLabel(str(node.get("name", "未命名软件")), widget)
        name_label.setObjectName("downloadSoftwareName")
        name_label.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        # 软件名称根据软件列实际宽度自动换行，列变宽后会减少换行行数。
        name_label.setWordWrap(True)
        name_label.setMinimumWidth(0)
        name_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout.addWidget(name_label, 1)
        self._refresh_name_widget(widget, node)
        return widget

    def _refresh_name_widget(self, widget, node):
        """创建及切档共用尺寸入口，图片、容器、缩进和间距不可分别更新。"""
        scale = self._page.scale_manager
        px = scale.px if scale is not None else lambda value: value
        widget.layout().setContentsMargins(px(22), 0, 0, 0)
        widget.layout().setSpacing(px(5))
        label = widget.findChild(QLabel, "downloadSoftwareIcon")
        if label is not None:
            side = px(26)
            label.setFixedSize(side, side)
            label.setPixmap(self._themed_software_icon(node).pixmap(side, side))

    def _themed_software_icon(self, node):
        """根据当前主题返回软件图标（暗色 + 标记反转时返回反转后的图标）。"""
        page = self._page
        icon = self.software_icon(node)
        if not page.light_theme and node.get("invert_icon_dark_theme", False):
            icon_value = node.get("icon", "")
            if icon_value:
                icon_path = page.software_manager.resolve_path(icon_value)
                if icon_path and icon_path.exists():
                    return invert_icon_file(str(icon_path))
        return icon

    def rebuild_software_name_widgets(self):
        """主题切换后重建所有软件行的名称列图标。"""
        page = self._page
        for row_data in page.rows:
            if "node" not in row_data or "row" not in row_data:
                continue
            node = row_data["node"]
            if node.get("type") != "software":
                continue
            name_widget = self.software_name_widget(node)
            page.table.setCellWidget(row_data["row"], 0, name_widget)

    def refresh_software_name_icons(self):
        """主题切换后只刷新软件行名称列的图标，不重建整个 cell widget。

        原位更新图片、标签尺寸和行高，不销毁名称控件、不改变任务与行顺序。
        """
        page = self._page
        row_height = page.scale_manager.px(54) if page.scale_manager else 54
        for row_data in page.rows:
            if "node" not in row_data or "row" not in row_data:
                continue
            node = row_data["node"]
            if node.get("type") != "software":
                continue
            widget = page.table.cellWidget(row_data["row"], 0)
            if widget is None:
                continue
            self._refresh_name_widget(widget, node)
            page.table.setRowHeight(row_data["row"], row_height)

    def refresh_category_icons(self):
        """主题切换后重绘分类行的线框展开图标。"""
        self._category_icon_cache.clear()
        page = self._page
        for row in page.category_rows.values():
            item = page.table.item(row, 0)
            if item is None:
                continue
            data = item.data(Qt.UserRole) or {}
            item.setIcon(self.category_icon(data.get("folder_node"), data.get("expanded", True)))

    def set_row_item(self, row, column, item, row_data):
        """为表格单元格写入 item，并绑定行数据。"""
        page = self._page
        item.setData(Qt.UserRole, row_data)
        item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        page.table.setItem(row, column, item)

    def set_row_text(self, row, column, text, row_data, tooltip=""):
        """写入普通文本单元格，并为文件名保留完整名称提示。

        参数：row/column 定位目标单元格；text 是表格中的紧凑显示文字；row_data 保存行数据；
        tooltip 是可选完整提示文本，多文件时可使用换行分隔所有文件名。
        返回值：无。
        副作用：创建并写入 QTableWidgetItem；不修改下载任务或配置。
        调用限制：文件名列显示“共 N 个文件”时必须传入 tooltip，避免悬停后仍看不到真实文件名。
        """
        page = self._page
        display_text = str(text or "--")
        if column == 1:
            from .download_status import current_version_display
            display_text = current_version_display(text)
        # 版本列由 OverflowMarqueeLabel 独占文字绘制。底层 item 只承载行数据；如果这里
        # 同时保存可见文字，QTableWidget 会在 cellWidget 下方再次换行绘制，形成文字叠加。
        item = QTableWidgetItem("" if column in (1, 2) else display_text)
        if (column == 3 and display_text != "--") or column == 7:
            item.setToolTip(str(tooltip or display_text))
        if column in (1, 2, 3, 4, 5, 7):
            item.setTextAlignment(Qt.AlignCenter)
        self.set_row_item(row, column, item, row_data)
        if column in (1, 2):
            version_label = page.table.cellWidget(row, column)
            if not isinstance(version_label, OverflowMarqueeLabel):
                version_label = OverflowMarqueeLabel(parent=page.table)
                page.table.setCellWidget(row, column, version_label)
            version_label.setFont(page.table.font())
            version_label.setText(display_text)
            # 版本列使用表格默认字体和默认前景色，不再单独放大或高亮。
            version_label.setTextColor(page.table.palette().color(QPalette.Text))

    def _category_font(self):
        """返回分类行字体，确保设置中的字号变化能传递到主下载页。"""
        page = self._page
        font = QFont(page._font_family())
        font.setPointSize(page.scale_manager.font_pt() if page.scale_manager else 10)
        return font

    def category_icon(self, folder_node=None, expanded=False):
        """返回包含线框展开符和文件夹图标的组合图标。

        组合绘制而不是继续拼接 Unicode 三角符，避免不同字体下箭头大小和基线不一致。
        使用内存缓存避免主题切换时重复 QPainter 合成相同图标。
        """
        page = self._page
        light = page.light_theme
        icon_value = ""
        if isinstance(folder_node, dict):
            icon_value = str(folder_node.get("icon", "") or "")
        cache_key = (icon_value, expanded, light)
        cached = self._category_icon_cache.get(cache_key)
        if cached is not None:
            return cached
        folder_icon = QIcon()
        if icon_value:
            icon_path = page.software_manager.resolve_path(icon_value)
            if icon_path and icon_path.exists():
                folder_icon = QIcon(str(icon_path))
        if folder_icon.isNull():
            path = BASE_DIR / "images" / "material_icons" / "folder.svg"
            folder_icon = QIcon(str(path)) if path.exists() else QIcon(":/icons/images/icons/cil-folder.png")

        # 组合图标横向留出独立间距，避免箭头贴住文件夹；箭头本身不随画布放大。
        pixmap = QPixmap(50, 32)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor("#c9d8e8") if not page.light_theme else QColor("#41627f"), 1.5)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        arrow = QPainterPath()
        if expanded:
            # 展开态只旋转方向，不改变箭头的尺寸；几何中心固定在 10px。
            arrow.moveTo(0, 14)
            arrow.lineTo(5, 18)
            arrow.lineTo(10, 14)
        else:
            arrow.moveTo(1, 11)
            arrow.lineTo(5, 16)
            arrow.lineTo(1, 21)
        painter.drawPath(arrow)
        folder_pixmap = folder_icon.pixmap(26, 26)
        painter.drawPixmap(22, 3, folder_pixmap)
        painter.end()
        icon = QIcon(pixmap)
        self._category_icon_cache[cache_key] = icon
        return icon

    def software_icon(self, node):
        """返回软件行原始图标（不反转，反转在显示层处理）。"""
        page = self._page
        icon_value = node.get("icon", "")
        if icon_value:
            icon_path = page.software_manager.resolve_path(icon_value)
            if icon_path and icon_path.exists():
                return QIcon(str(icon_path))
        script_type = node.get("script_type", "")
        if script_type == "bat":
            return QIcon(":/icons/images/icons/cil-terminal.png")
        return QIcon(":/icons/images/icons/cil-cloud-download.png")
