"""下载页 QSS 样式表生成和样式管理器。

公共样式和工具函数已移到 base_page_styles.py。
此处保留为向后兼容的薄导入层，并包含 DownloadPageStyleManager。
"""

from pathlib import Path

from PySide6.QtWidgets import QToolButton
from PySide6.QtGui import QPalette

from modules.config_manager import BASE_DIR
from modules.icon_utils import generate_dark_icon_file, get_theme_icon
from modules.main_pages.base_page_styles import (
    dark_base_page_stylesheet,
    light_base_page_stylesheet,
)


class DownloadPageStyleManager:
    """下载页样式管理器。

    负责：主题切换（set_light_theme）、QSS 应用（_apply_styles）、
    缩放应用（apply_scale）、图标刷新。
    不负责：页面初始化、业务逻辑、配置读写。

    输入参数：
        page: SoftwareDownloadPage 实例，用于访问界面控件和状态。
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
        """应用缩放管理器中的缩放比例到下载页控件。"""
        self._page._table_layout.apply_scale()

    def _apply_styles(self):
        """应用下载页 QSS + 刷新图标字体。"""
        page = self._page
        if page.light_theme:
            dark_arrow_path = generate_dark_icon_file(
                ":/icons/images/icons/cil-arrow-bottom.png",
                BASE_DIR / "images" / "icons" / "dark",
            )
            arrow_url = str(Path(dark_arrow_path).resolve()).replace("\\", "/")
            stylesheet = light_base_page_stylesheet(arrow_url, page.scale_manager)
        else:
            stylesheet = dark_base_page_stylesheet(page.scale_manager)
        if page.styleSheet() != stylesheet:
            page.setStyleSheet(stylesheet)
        for target_name in ("downloadMiniButton", "downloadToolButton"):
            for btn in page.findChildren(QToolButton, target_name):
                icon_path = btn.property("iconPath")
                if icon_path:
                    btn.setIcon(get_theme_icon(icon_path, page.light_theme))
        # 多文件入口的下拉箭头用 setIcon() 创建，不会随页面 QSS 自动反色。它也没有
        # iconPath 属性，必须在统一主题刷新入口显式处理，避免亮色主题继续显示白色箭头。
        for btn in page.findChildren(QToolButton, "multiFileArrowBtn"):
            btn.setIcon(get_theme_icon(
                ":/icons/images/icons/cil-arrow-bottom.png",
                page.light_theme,
            ))
        page.info_panel.refresh_font()
        from modules.common_widgets.overflow_marquee_label import OverflowMarqueeLabel
        for label in page.table.findChildren(OverflowMarqueeLabel):
            label.setFont(page.table.font())
            label.setTextColor(page.table.palette().color(QPalette.Text))
