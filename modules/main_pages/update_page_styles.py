"""更新页样式管理器

主要作用：从 SoftwareUpdatePage 中提取样式应用、主题切换、缩放和
字体相关的辅助方法，让页面类只负责业务逻辑和用户交互。

使用方式：页面创建 UpdatePageStyleManager(page) 实例，页面原有的这些方法改为委托。
"""

from pathlib import Path

from PySide6.QtCore import QSize
from PySide6.QtGui import QFont

from modules.config_manager import BASE_DIR
from modules.icon_utils import generate_dark_icon_file, get_theme_icon
from modules.main_pages.base_page_styles import (
    dark_base_page_stylesheet,
    font_family as base_font_family,
    font_points as base_font_points,
    light_base_page_stylesheet,
)


class UpdatePageStyleManager:
    """更新页样式管理器

    负责：主题切换（set_light_theme）、QSS 应用（_apply_styles）、
    Tab 样式表生成（_tab_stylesheet）、缩放应用（apply_scale）、字体/尺寸查询。
    不负责：页面初始化、业务逻辑、配置读写。

    输入参数：
        page: SoftwareUpdatePage 实例，用于访问界面控件和状态。
    """

    def __init__(self, page):
        self._page = page

    def set_light_theme(self, enabled):
        """切换亮色/深色主题并刷新样式。"""
        page = self._page
        page.light_theme = bool(enabled)
        self._apply_styles()
        page.info_panel.set_light_theme(enabled)
        from modules.common_widgets.sweep_button import SweepButton
        for btn in page.findChildren(SweepButton):
            btn.set_light_theme(page.light_theme)

    def apply_scale(self):
        """应用缩放管理器中的缩放比例到更新页控件。"""
        page = self._page
        if page.scale_manager is None:
            return
        pm = page.scale_manager.px
        page.right_panel.setMinimumWidth(pm(220))
        # 缩放变化时同步更新所有表格的字体，保持与下载页一致。
        family = base_font_family(page.scale_manager)
        _, _, body_pt = base_font_points(page.scale_manager)
        table_font = QFont(family)
        table_font.setPointSize(body_pt)
        for table in page.tables.values():
            table.setFont(table_font)
            # 更新页软件图标由 QTableWidgetItem 绘制，比下载页 QLabel 图标更容易显小，缩放时保持略大尺寸。
            table.setIconSize(QSize(pm(26), pm(26)))
            table.verticalHeader().setDefaultSectionSize(pm(52))
            for row in range(table.rowCount()):
                table.setRowHeight(row, pm(54))
            header = table.horizontalHeader()
            fit_to_viewport = getattr(header, "fit_to_viewport", None)
            if fit_to_viewport:
                fit_to_viewport()
        self._apply_styles()

    def _apply_styles(self):
        """应用下载页 QSS + 更新页特有 QSS。"""
        page = self._page
        if page.light_theme:
            dark_arrow_path = generate_dark_icon_file(
                ":/icons/images/icons/cil-arrow-bottom.png",
                BASE_DIR / "images" / "icons" / "dark",
            )
            arrow_url = str(Path(dark_arrow_path).resolve()).replace("\\", "/")
            page.setStyleSheet(
                light_base_page_stylesheet(arrow_url, page.scale_manager)
                + self._tab_stylesheet(True)
            )
        else:
            page.setStyleSheet(
                dark_base_page_stylesheet(page.scale_manager)
                + self._tab_stylesheet(False)
            )
        from PySide6.QtWidgets import QToolButton
        for btn in page.findChildren(QToolButton, "downloadToolButton"):
            icon_path = btn.property("iconPath")
            if icon_path:
                btn.setIcon(get_theme_icon(icon_path, page.light_theme))
        for btn in page.findChildren(QToolButton, "multiFileArrowBtn"):
            btn.setIcon(
                get_theme_icon(":/icons/images/icons/cil-arrow-bottom.png", page.light_theme)
            )
        page.info_panel.refresh_font()

    def _tab_stylesheet(self, light_theme):
        """生成更新页 TabBar QSS。

        QMenu 样式已统一放到 themes/dark_page_base.qss / light_page_base.qss，
        由两个页面共享，这里只保留 TabBar 的独有样式。
        """
        body_pt = str(base_font_points(self._page.scale_manager)[2])
        family = base_font_family(self._page.scale_manager)
        if light_theme:
            return f"""
QTabBar#updatePositionTabBar::tab {{
    background: transparent;
    color: #42526b;
    border: none;
    padding: 7px 14px;
    margin-right: 2px;
    font: {body_pt}pt "{family}";
}}
QTabBar#updatePositionTabBar::tab:selected {{
    color: #0078d4;
    border-bottom: 2px solid #0078d4;
}}
QTabBar#updatePositionTabBar::tab:hover {{
    color: #0078d4;
}}
"""
        return f"""
QTabBar#updatePositionTabBar::tab {{
    background: transparent;
    color: #c8d0dc;
    border: none;
    padding: 7px 14px;
    margin-right: 2px;
    font: {body_pt}pt "{family}";
}}
QTabBar#updatePositionTabBar::tab:selected {{
    color: #ffffff;
    border-bottom: 2px solid #0078d4;
}}
QTabBar#updatePositionTabBar::tab:hover {{
    color: #ffffff;
}}
"""
