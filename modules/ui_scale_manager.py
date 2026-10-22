import re

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QWidget, QLayout, QGridLayout, QFormLayout, QHBoxLayout

from .app_settings import Settings


class UiScaleManager:
    # WinBox 的界面缩放管理器。
    # 这里不修改系统 DPI，也不直接散落修改每个控件，而是集中把“百分比”转换成字体、间距和控件尺寸。
    GLOBAL_LEVELS = (100, 125, 150, 175)
    GLOBAL_MIN = GLOBAL_LEVELS[0]
    GLOBAL_MAX = GLOBAL_LEVELS[-1]
    BASE_FONT_PT = 11
    # 折叠导航宽度与标题栏高度共享基准，左上角 Logo 区域保持正方形。
    CHROME_EDGE = 56
    NAV_EXPANDED_WIDTH = 132
    NAV_BUTTON_SIZE = CHROME_EDGE - 12
    NAV_MARGIN_Y = 3
    NAV_MARGIN_X = 6
    TREE_MIN = 80
    TREE_MAX = 160

    FONT_SIZE_MIN = 8
    FONT_SIZE_MAX = 18

    def __init__(self, global_scale=100, font_family="default", font_size=10):
        self.global_scale = self.clamp_global_scale(global_scale)
        self.font_family = font_family
        # 保留旧参数的调用兼容性，但不再叠加独立的全局字号。
        self.font_size = self.BASE_FONT_PT

    @staticmethod
    def clamp_global_scale(value):
        # 旧版任意百分比就近迁移到四档；平局取较小档，避免意外放大。
        try:
            scale = int(value)
        except (TypeError, ValueError):
            scale = 100
        return min(UiScaleManager.GLOBAL_LEVELS, key=lambda level: abs(level - scale))

    @staticmethod
    def clamp_tree_scale(value):
        # 软件树需要给近视用户更大的行高，也需要给信息密集用户更紧凑的显示，所以范围比全局缩放稍宽。
        try:
            scale = int(value)
        except (TypeError, ValueError):
            scale = 100
        return max(UiScaleManager.TREE_MIN, min(UiScaleManager.TREE_MAX, scale))

    @staticmethod
    def clamp_font_size(value):
        """兼容旧配置读取入口；全局字号只随整体缩放，日志局部字号不走此处。"""
        return UiScaleManager.BASE_FONT_PT

    @staticmethod
    def for_widget(widget, fallback=None):
        """沿实际父链取得缩放上下文，兼容嵌入页及延迟创建的控件。"""
        while widget is not None:
            scale = widget.property("winboxUiScale")
            if scale is not None:
                return UiScaleManager(scale, widget.property("winboxUiFont") or "default")
            manager = getattr(widget, "scale_manager", None)
            if isinstance(manager, UiScaleManager):
                return manager
            widget = widget.parentWidget()
        return fallback if fallback is not None else UiScaleManager()

    def publish(self, widget):
        """发布只读外观上下文；不修改配置，不递归遍历或启动轮询。"""
        widget.setProperty("winboxUiScale", self.global_scale)
        widget.setProperty("winboxUiFont", self.font_family)

    def bind_metrics(self, widget, **metrics):
        """登记控件的 100% 逻辑尺寸；创建与后续切档都从基准计算，避免累乘。"""
        metrics = {**getattr(widget, "_winbox_base_metrics", {}), **metrics}
        widget._winbox_base_metrics = metrics
        self._apply_metrics(widget, metrics)

    def _apply_metrics(self, widget, metrics):
        """只应用登记过的尺寸，不猜测业务控件的动态几何。"""
        for name, value in metrics.items():
            if name == "icon_size":
                widget.setIconSize(QSize(self.px(value), self.px(value)))
            elif name == "fixed_size":
                widget.setFixedSize(self.px(value[0]), self.px(value[1]))
            else:
                setters = {"min_width": "setMinimumWidth", "min_height": "setMinimumHeight",
                           "max_width": "setMaximumWidth", "fixed_height": "setFixedHeight",
                           "fixed_width": "setFixedWidth"}
                getattr(widget, setters[name])(self.px(value))

    def bind_layout(self, layout, **metrics):
        """登记原始布局留白；与控件尺寸一样从基准缩放，零间距保持零。"""
        layout._winbox_base_layout = metrics
        self._apply_layout(layout, metrics)

    def _apply_layout(self, layout, metrics):
        def scaled(value):
            return round(value * self.global_scale / 100) if value >= 0 else value
        for name, value in metrics.items():
            if name == "margins":
                layout.setContentsMargins(*(scaled(v) for v in value))
            else:
                setter = {"spacing": "setSpacing", "horizontal": "setHorizontalSpacing",
                          "vertical": "setVerticalSpacing"}[name]
                getattr(layout, setter)(scaled(value))

    def register_layout_tree(self, root):
        """接入仍使用原始逻辑间距的页面；只登记一次，不采集控件动态宽高。"""
        for layout in root.findChildren(QLayout):
            if hasattr(layout, "_winbox_base_layout"):
                continue
            metrics = {"margins": layout.getContentsMargins()}
            if isinstance(layout, (QGridLayout, QFormLayout)):
                metrics.update(horizontal=layout.horizontalSpacing(), vertical=layout.verticalSpacing())
            else:
                metrics["spacing"] = layout.spacing()
            self.bind_layout(layout, **metrics)

    def apply_bound_metrics(self, root):
        """在显式外观刷新时更新公共控件；无计时器、不保存控件引用。"""
        for widget in [root, *root.findChildren(QWidget)]:
            metrics = getattr(widget, "_winbox_base_metrics", None)
            if metrics:
                self._apply_metrics(widget, metrics)
        for layout in root.findChildren(QLayout):
            metrics = getattr(layout, "_winbox_base_layout", None)
            if metrics:
                self._apply_layout(layout, metrics)

    def scale_stylesheet(self, stylesheet):
        """从原始主题缩放尺寸和字号，跳过注释、字符串及资源地址。

        调用方必须传入未缩放的主题文本，不能把控件当前样式再次送入。
        零值保持零；负边距保留符号，颜色及百分比不参与转换。
        """
        token = re.compile(r'/\*.*?\*/|url\([^)]*\)|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|(?P<number>-?\d+(?:\.\d+)?)(?P<unit>px|pt)\b', re.DOTALL)

        def replace(match):
            if match.group("number") is None:
                return match.group(0)
            value = float(match.group("number")) * self.global_scale / 100
            prefix = stylesheet[max(0, match.start() - 48):match.start()]
            rule_start = stylesheet.rfind("{", 0, match.start())
            selector = stylesheet[stylesheet.rfind("}", 0, rule_start) + 1:rule_start]
            round_control = re.search(r'\bQ(?:Slider|ScrollBar)\b', selector)
            if match.group("unit") == "px" and (round_control or re.search(r'border(?:-[a-z]+)*-radius\s*:\s*$', prefix)):
                # 滑块几何与圆角使用整数，避免小数负边距让 Qt 6.3 绘制退回矩形。
                # 其他控件保留原有精度，避免侧栏等组合布局发生累计舍入偏差。
                value = round(value)
            return f'{value:g}{match.group("unit")}'

        return token.sub(replace, self.apply_font_family_to_qss(stylesheet))

    def _resolved_font(self):
        # 把 font_family 代码解析成实际字体名，供 QSS 生成器使用。
        if self.font_family == "bold":
            return "Microsoft YaHei"
        elif self.font_family and self.font_family not in ("default", "bold"):
            return self.font_family
        # Windows 上 Segoe UI 不一定覆盖中文；设置窗口和主页面都有大量中文，
        # 默认字体优先使用微软雅黑，避免打包或离屏渲染时中文显示成方块。
        return "Microsoft YaHei"

    def px(self, value):
        # 把基础像素按当前全局缩放转换成实际像素。
        return max(1, round(int(value) * self.global_scale / 100))

    def pt(self, value):
        # Qt 字体使用 point size，单独保留 pt()，避免后续误把字体和像素混在一起。
        return max(1, round(int(value) * self.global_scale / 100))

    def font_pt(self, offset=0):
        """返回跟随字体尺寸和界面缩放后的字号。

        参数：
            offset: 相对基础字号的偏移，例如标题可传 4、辅助文字可传 -1。
        返回：
            经过全局缩放后的 point size。
        副作用：
            无。
        """
        return self.pt(self.font_size + int(offset))

    def tree_px(self, value, tree_scale):
        # 软件树缩放叠加全局缩放：全局负责整体可读性，tree_scale 负责列表密度。
        combined = self.global_scale * self.clamp_tree_scale(tree_scale) / 10000
        return max(1, round(int(value) * combined))

    def tree_pt(self, value, tree_scale):
        combined = self.global_scale * self.clamp_tree_scale(tree_scale) / 10000
        return max(1, round(int(value) * combined))

    def scaled_settings_stylesheet(self):
        # 附加在主题 QSS 后面，覆盖常用设置窗口控件尺寸和字体。
        # 只覆盖尺寸和字体，不写颜色，避免和主题系统抢视觉职责。
        # 主题 QSS 中的 QWidget { font: 10pt } 会匹配所有控件，
        # 这里用类型选择器逐类覆盖，让字体跟随 scale_manager.pt() 缩放。
        font = self._resolved_font()
        return f"""
QDialog {{
    font: {self.font_pt()}pt "{font}", "Microsoft YaHei", "Segoe UI";
}}

QLabel,
QCheckBox,
QRadioButton,
QGroupBox {{
    font: {self.font_pt()}pt "{font}", "Microsoft YaHei", "Segoe UI";
}}

QToolTip {{
    font: {self.font_pt()}pt "{font}", "Microsoft YaHei", "Segoe UI";
}}

#settingsTitleBar {{
    min-height: {self.px(44)}px;
    max-height: {self.px(44)}px;
}}

#settingsWindowTitle {{
    font: {self.font_pt()}pt "{font}", "Microsoft YaHei", "Segoe UI";
    font-weight: bold;
}}

QPushButton {{
    font: {self.font_pt()}pt "{font}", "Microsoft YaHei", "Segoe UI";
    min-height: {self.px(30)}px;
    padding: {self.px(6)}px {self.px(12)}px;
}}

QLineEdit,
QComboBox,
QDoubleSpinBox,
QSpinBox {{
    font: {self.font_pt()}pt "{font}", "Microsoft YaHei", "Segoe UI";
    min-height: {self.px(30)}px;
}}

QLabel#settingsTitle,
QLabel#settingsPageTitle {{
    font: {self.font_pt(4)}pt "{font}", "Microsoft YaHei", "Segoe UI";
    font-weight: bold;
}}

QLabel#settingsSectionTitle {{
    font: {self.font_pt(1)}pt "{font}", "Microsoft YaHei", "Segoe UI";
    font-weight: bold;
}}

QTextEdit#settingsCodeInput {{
    font-size: {self.font_pt()}pt;
}}
"""

    def software_tree_stylesheet(self, tree_scale):
        # 软件树专用尺寸样式。
        # 设置页和未来主窗口软件下载树都可以套这段，保证树的密度调节逻辑一致。
        row_height = self.tree_px(36, tree_scale)
        icon_size = self.tree_px(20, tree_scale)
        font_size = self.tree_pt(self.font_size, tree_scale)
        padding = self.tree_px(6, tree_scale)
        font = self._resolved_font()
        return f"""
QTreeWidget#softwareItemsTree {{
    font: {font_size}pt "{font}", "Microsoft YaHei", "Segoe UI";
    padding: {padding}px;
}}

QTreeWidget#softwareItemsTree::item {{
    min-height: {row_height}px;
    padding: {max(2, padding // 2)}px;
}}
""", row_height, icon_size

    def nav_row_height(self):
        """行高按实际内容盒和双侧整数边距相加，避免分数缩放累计一像素误差。"""
        return self.px(self.NAV_BUTTON_SIZE) + 2 * self.px(self.NAV_MARGIN_Y)

    def scaled_main_stylesheet(self):
        # 主窗口里大量控件由 ui_main.py 生成并写死字体/尺寸。
        # 这里用附加 QSS 覆盖最核心的主界面区域，让全局缩放在主窗口也能明显生效。
        font = self._resolved_font()
        # QSS 高度是内容盒；另有上下各 3px 的主题 margin。
        menu_button_height = self.px(self.NAV_BUTTON_SIZE)
        icon_button_size = self.px(44)
        bottom_bar_height = self.px(26)
        return f"""
#bottomBar {{
    min-height: {bottom_bar_height}px;
    max-height: {bottom_bar_height}px;
}}

#titleRightInfo,
#creditsLabel,
#labelVersion {{
    font: {self.font_pt(-1)}pt "{font}", "Microsoft YaHei", "Segoe UI";
}}

#topMenu QPushButton,
#bottomMenu QPushButton,
#toggleButton {{
    margin: {self.px(self.NAV_MARGIN_Y)}px {self.px(self.NAV_MARGIN_X)}px;
    min-height: {menu_button_height}px;
    max-height: {menu_button_height}px;
    font: {self.font_pt()}pt "{font}", "Microsoft YaHei", "Segoe UI";
}}

#topSettings .QPushButton {{
    min-width: {icon_button_size}px;
    max-width: {icon_button_size}px;
    min-height: {icon_button_size}px;
    max-height: {icon_button_size}px;
}}

#bottomMenu .QPushButton {{
    min-height: {menu_button_height}px;
    max-height: {menu_button_height}px;
    font: {self.font_pt()}pt "{font}", "Microsoft YaHei", "Segoe UI";
}}

#toggleBox {{
    min-height: {self.nav_row_height()}px;
    max-height: {self.nav_row_height()}px;
}}

#toggleButton {{
    font: {self.font_pt()}pt "{font}";
}}

#pagesContainer QPushButton,
#pagesContainer QToolButton,
#pagesContainer QComboBox,
#pagesContainer QLabel,
#topMenus QPushButton,
#contentSettings QPushButton {{
    font: {self.font_pt()}pt "{font}", "Microsoft YaHei", "Segoe UI";
}}

QToolTip {{
    font: {self.font_pt()}pt "{font}", "Microsoft YaHei", "Segoe UI";
}}
"""

    def apply_runtime_settings(self):
        # PyDracula 模板把菜单宽度放在 Settings 静态属性里，动画展开时会读取这些值。
        # 这里按全局缩放更新运行时宽度；颜色和选中样式仍然由主题逻辑控制。
        Settings.MENU_WIDTH = self.px(self.NAV_EXPANDED_WIDTH)
        Settings.MENU_COLLAPSED_WIDTH = self.px(self.CHROME_EDGE)
        Settings.RIGHT_BOX_WIDTH = self.px(240)

    def apply_application_font(self, app, font_family=None):
        # 给整个 QApplication 一个基础字体，能覆盖大部分新写控件。
        # 旧 ui_main.py 里手动设置过字体的控件可能不会完全继承，所以后续还需要逐步接入公共样式。
        # font_family 取值："default" → Segoe UI, "bold" → 微软雅黑加粗（支持中文粗体）, 其他 → 自定义字体名
        if app is None:
            return
        # 如果调用方没传 font_family，用 scale_manager 自己存的值
        if font_family is None:
            font_family = self.font_family
        if font_family == "bold":
            font = QFont("Microsoft YaHei")
            font.setBold(True)
            font.setWeight(QFont.Bold)
        elif font_family and font_family not in ("default", "bold"):
            font = QFont(font_family)
        else:
            font = QFont("Microsoft YaHei")
        font.setPointSize(self.font_pt())
        app.setFont(font)

    def apply_font_family_to_qss(self, stylesheet):
        """将主题模板中固定的界面字体替换为当前全局字体。

        参数：stylesheet 是待应用的 QSS 文本。
        返回值：替换字体族后的 QSS 文本。
        副作用：无。
        调用限制：只替换普通界面字体，不处理代码框等刻意指定的等宽字体。
        """
        font = self._resolved_font()
        return (
            str(stylesheet or "")
            .replace('"Segoe UI Semibold"', f'"{font}"')
            .replace('"Segoe UI"', f'"{font}"')
            .replace('"Microsoft YaHei"', f'"{font}"')
        )

    def apply_main_window(self, main_window):
        # 主窗口缩放的统一入口。
        # 既应用 Qt 全局字体，也把附加 QSS 追加到主窗口样式容器，覆盖 ui_main.py 的固定尺寸。
        if hasattr(main_window, "ui") and hasattr(main_window.ui, "styleSheet"):
            base_stylesheet = getattr(main_window, "current_base_stylesheet", "")
            self.publish(main_window)
            ui = main_window.ui
            # Designer 的标题栏固定 50px、Logo 使用绝对坐标；改为共同的缩放基准和布局。
            if ui.topLogoInfo.layout() is None:
                logo_layout = QHBoxLayout(ui.topLogoInfo)
                self.bind_layout(logo_layout, margins=(0, 0, 0, 0), spacing=0)
                logo_layout.addWidget(ui.topLogo, 0, Qt.AlignCenter)
            self.bind_metrics(ui.topLogoInfo, fixed_height=self.CHROME_EDGE)
            self.bind_metrics(ui.contentTopBg, fixed_height=self.CHROME_EDGE)
            self.bind_metrics(ui.topLogo, fixed_size=(40, 40))
            self.bind_layout(ui.horizontalLayout, margins=(0, 0, 10, 0), spacing=0)
            self.bind_layout(ui.horizontalLayout_2, margins=(0, 0, 0, 0), spacing=5)
            ui.titleRightInfo.setMaximumHeight(16777215)
            self.apply_bound_metrics(main_window)
            base_stylesheet = self.scale_stylesheet(base_stylesheet)
            main_window.ui.styleSheet.setStyleSheet(f"{base_stylesheet}\n\n{self.scaled_main_stylesheet()}")
            sidebar = main_window.ui.leftMenuBg
            compact = getattr(main_window, "_sidebar_compact", True)
            width = self.px(self.CHROME_EDGE if compact else self.NAV_EXPANDED_WIDTH)
            sidebar.setFixedWidth(width)
            if sidebar.layout() is not None:
                sidebar.layout().activate()
            if hasattr(main_window, "_sidebar_icon_labels"):
                main_window.refreshSidebarIcons()
                main_window._set_sidebar_compact(compact)

    def apply_tree_widget(self, tree_widget, tree_scale):
        # 把软件树缩放应用到控件属性上。
        # QSS 负责视觉尺寸，setIconSize 和 setIndentation 负责 Qt 树控件内部布局。
        stylesheet, _row_height, icon_size = self.software_tree_stylesheet(tree_scale)
        tree_widget.setStyleSheet(stylesheet)
        tree_widget.setIconSize(QSize(icon_size, icon_size))
        tree_widget.setIndentation(self.tree_px(22, tree_scale))
