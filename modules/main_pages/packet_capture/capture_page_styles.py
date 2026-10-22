"""抓包页面主题和缩放样式管理器。"""

from pathlib import Path
from contextlib import contextmanager
import re

from modules.config_manager import BASE_DIR, THEMES_DIR
from modules.dialog_theme import collapsible_section_stylesheet, combo_popup_stylesheet, spin_box_stylesheet, table_header_stylesheet, extract_selector_property, replace_light_theme_qss_icons
from modules.icon_utils import generate_dark_icon_file
from modules.main_pages.base_page_styles import (
    dark_base_page_stylesheet,
    font_family,
    font_points,
    inject_font_points,
    light_base_page_stylesheet,
)


_DARK_FALLBACK = """
QFrame#packetCapturePage { background-color: #181b1f; background-image: none; }
QFrame#captureCard { background: #1d2126; border: 1px solid #303741; border-radius: 7px; }
QLabel#captureGroupTitle { color: #f4f6f8; font: __HEADER_PT__pt \"__FONT_FAMILY__\"; }
QLabel#captureDescription, QLabel#captureFormLabel { color: #aeb8c6; }
"""
_LIGHT_FALLBACK = """
QFrame#packetCapturePage { background-color: #edf2f8; background-image: none; }
QFrame#captureCard { background: #ffffff; border: 1px solid #cbd5e3; border-radius: 7px; }
QLabel#captureGroupTitle { color: #172033; font: __HEADER_PT__pt \"__FONT_FAMILY__\"; }
QLabel#captureDescription, QLabel#captureFormLabel { color: #52627a; }
"""


class CapturePageStyleManager:
    """只负责 PacketCapturePage 的两套 QSS、标签页样式和缩放尺寸。"""

    _template_cache = {}

    def __init__(self, page):
        self.page = page
        self._metrics_key = None
        self._stylesheet_cache = {}

    @contextmanager
    def appearance_update(self):
        """Measure action cells once after the complete host/page style transaction."""
        from .openwrt.wireless_network_table import WirelessNetworkTable
        tables = self.page.findChildren(WirelessNetworkTable)
        for table in tables:
            table.begin_appearance_update()
        try:
            yield
        finally:
            for table in tables:
                table.end_appearance_update()

    def set_light_theme(self, enabled):
        """切换亮暗主题并刷新页面样式、信息面板、扫光按钮和图标按钮。"""

        page = self.page
        page.light_theme = bool(enabled)
        self._apply_styles()
        page.openwrt_tab.set_light_theme(page.light_theme)
        for panel in page.info_panels:
            panel.set_light_theme(enabled)
        from modules.common_widgets.sweep_button import SweepButton
        for button in page.findChildren(SweepButton):
            button.set_light_theme(page.light_theme)
        # AP 表格内高亮是代码写的前景色，QSS 换肤够不着；切主题必须按模型重刷，
        # 否则旧颜色残留在格子里（亮色下尤为明显）。
        from modules.main_pages.packet_capture.openwrt.access_point_table import (
            AccessPointTable,
        )
        for table in page.findChildren(AccessPointTable):
            table.set_light_theme(page.light_theme)
        # 图标按钮（密码显示、私钥、目录选择等）走 setIcon，必须在这里显式刷新，
        # 否则亮色主题下继续显示白色图标。
        from modules.common_widgets.icon_button import refresh_tool_button_icons
        refresh_tool_button_icons(page, page.light_theme)

    def apply_scale(self):
        """按全局缩放更新右侧面板和 QSS 字号。"""

        self._apply_styles()

    def _load_template(self, filename):
        template = self._template_cache.get(filename)
        if template is None:
            try:
                template = (THEMES_DIR / "page_partials" / filename).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                # themes 是发布后的可编辑目录。局部文件被误删或改坏时使用内置最小样式，
                # 页面仍可打开并提示环境状态，不能让导航按钮槽直接抛异常。
                template = _LIGHT_FALLBACK if "light" in filename else _DARK_FALLBACK
            self._template_cache[filename] = template
        return template

    def _capture_stylesheet(self, light):
        title_pt, header_pt, body_pt = font_points(self.page.scale_manager)
        filename = "packet_capture_light.qss" if light else "packet_capture_dark.qss"
        stylesheet = inject_font_points(
            self._load_template(filename),
            title_pt,
            header_pt,
            body_pt,
            self.page.scale_manager,
        ).replace("__FONT_FAMILY__", font_family(self.page.scale_manager))
        # 文件列表与信号表格共用现有主题规则，不另定颜色或选中样式。
        file_rules = []
        for selector in ("QTableWidget#captureAccessPointTable", "QTableWidget#captureAccessPointTable::item"):
            match = re.search(re.escape(selector) + r"\s*\{([^{}]*)\}", stylesheet)
            if match:
                file_rules.append(selector.replace("QTableWidget#captureAccessPointTable", "QTreeWidget#routerFileList") + ", " + selector.replace("QTableWidget#captureAccessPointTable", "QListView#routerFileIcons") + " {" + match[1] + "}")
        selection_background = extract_selector_property(
            stylesheet, "QTableWidget#captureAccessPointTable", "selection-background-color",
            "palette(highlight)")
        selection_foreground = extract_selector_property(
            stylesheet, "QTableWidget#captureAccessPointTable", "selection-color",
            "palette(highlighted-text)")
        file_rules.append(
            "QTreeWidget#routerFileList::item:selected, QListView#routerFileIcons::item:selected "
            "{ background-color: " + selection_background + "; color: " + selection_foreground + "; }")
        stylesheet += "\n" + "\n".join(file_rules)
        header_background = extract_selector_property(
            table_header_stylesheet(light), "QTreeView QHeaderView::section:horizontal",
            "background-color", "palette(button)")
        stylesheet = stylesheet.replace("__TABLE_HEADER_BG__", header_background)
        # 公共控件片段追加到末尾，复选框与下拉列表均与主窗口、设置窗口同源。
        controls = combo_popup_stylesheet(light) + "\n" + spin_box_stylesheet(light) + "\n" + collapsible_section_stylesheet(light)
        if self.page.scale_manager is not None:
            controls = self.page.scale_manager.scale_stylesheet(controls)
        return f"{stylesheet}\n\n{controls}"

    def _apply_styles(self):
        page = self.page
        scale = page.scale_manager
        metrics_key = (scale.global_scale, scale.font_family) if scale is not None else None
        if scale is not None and self._metrics_key != metrics_key:
            scale.register_layout_tree(page)
            scale.apply_bound_metrics(page)
        self._metrics_key = metrics_key
        key = (page.light_theme, metrics_key)
        stylesheet = self._stylesheet_cache.get(key)
        if stylesheet is None:
            stylesheet = self._build_stylesheet()
            if len(self._stylesheet_cache) >= 8:
                self._stylesheet_cache.pop(next(iter(self._stylesheet_cache)))
            self._stylesheet_cache[key] = stylesheet
        if page.styleSheet() != stylesheet:
            page.setStyleSheet(stylesheet)
        self._refresh_controls()

    def _build_stylesheet(self):
        page = self.page
        if page.light_theme:
            dark_arrow_path = generate_dark_icon_file(
                ":/icons/images/icons/cil-arrow-bottom.png",
                BASE_DIR / "images" / "icons" / "dark",
            )
            arrow_url = str(Path(dark_arrow_path).resolve()).replace("\\", "/")
            base = light_base_page_stylesheet(arrow_url, page.scale_manager)
        else:
            base = dark_base_page_stylesheet(page.scale_manager)
        stylesheet = base + self._capture_stylesheet(page.light_theme)
        if page.light_theme:
            # 抓包页数字框和设置页使用同一套浅色资源图标。亮色背景下必须先生成
            # 深灰缓存再交给 QSS，否则白色上下箭头几乎不可见。
            stylesheet = replace_light_theme_qss_icons(stylesheet)
        return stylesheet

    def _refresh_controls(self):
        page = self.page
        from .vertical_tab_bar import VerticalTextTabBar
        from .openwrt.access_point_table import AccessPointTable
        for tabs in page.findChildren(VerticalTextTabBar):
            tabs.refresh_scale()
        for table in page.findChildren(AccessPointTable):
            table.refresh_scale()
        from modules.common_widgets.combo_box import CommonComboBox
        for combo in page.findChildren(CommonComboBox):
            combo.set_light_theme(page.light_theme)
        for panel in page.info_panels:
            panel.refresh_font()
        from .openwrt.wireless_network_table import WirelessNetworkTable
        for table in page.findChildren(WirelessNetworkTable):
            table.refresh_scale()
        from modules.common_widgets.header_corners import round_table_headers
        round_table_headers(page)

    def refresh_subtree(self, root):
        """为按需创建的子页同步当前缩放和图标，不重设整个页面样式。"""
        from modules.common_widgets.icon_button import refresh_tool_button_icons
        from modules.common_widgets.combo_box import CommonComboBox
        from modules.common_widgets.sweep_button import SweepButton
        from .openwrt.access_point_table import AccessPointTable
        scale = self.page.scale_manager
        if scale is not None:
            scale.register_layout_tree(root)
            scale.apply_bound_metrics(root)
        for button in root.findChildren(SweepButton):
            button.set_light_theme(self.page.light_theme)
        for table in root.findChildren(AccessPointTable):
            table.set_light_theme(self.page.light_theme)
            table.refresh_scale()
        for combo in root.findChildren(CommonComboBox):
            combo.set_light_theme(self.page.light_theme)
        refresh_tool_button_icons(root, self.page.light_theme)
        from .openwrt.wireless_network_table import WirelessNetworkTable
        for table in root.findChildren(WirelessNetworkTable):
            table.refresh_scale()
        from modules.common_widgets.header_corners import round_table_headers
        round_table_headers(root)
