from modules.common_widgets.spin_box import CommonSpinBox as QSpinBox
from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QStackedLayout,
    QToolButton,
    QVBoxLayout,
)

from modules.common_widgets.collapsible_section import CollapsibleSection
from modules.ui_layout import PAGE_CONTENT_MARGINS
from modules.app_settings import is_light_theme
from modules.ui_scale_manager import UiScaleManager

# 保留原名称，兼容现有设置页调用；实际值统一由公共页面布局规则提供。
SETTINGS_PAGE_SPACING = 18
SETTINGS_PAGE_MARGINS = PAGE_CONTENT_MARGINS

from modules.common_widgets.discrete_tick_slider import DiscreteTickSlider
from modules.icon_utils import get_theme_icon


# 设置页工具按钮必须与数字输入框上下箭头使用同一套主题规则：
# 亮色主题使用 #555555 深灰，暗色主题直接使用资源原始浅色图标。
SETTINGS_TOOL_ICON_LIGHT_COLOR = QColor("#555555")


def get_settings_tool_icon(icon_path, light_theme):
    """返回设置页工具按钮的统一主题图标。

    参数：icon_path 是 Qt 资源路径或文件路径；light_theme 表示当前是否为亮色主题。
    返回值：按数字输入框箭头相同规则处理后的 QIcon。
    副作用：使用 icon_utils 的内存缓存，不写文件。
    调用限制：所有 settingsToolButton/settingsTreeActionButton 必须通过此函数取图标，
    禁止页面单独传入颜色，避免亮暗主题混用同一种图标颜色。
    """
    # dark_color 保持 None：暗色主题回退到资源原始浅色，和 QSpinBox 的 QSS 箭头一致。
    return get_theme_icon(str(icon_path), light_theme, light_color=SETTINGS_TOOL_ICON_LIGHT_COLOR)


def _settings_context_is_light(parent):
    """从按钮父级链读取当前设置窗口主题，供延迟创建页面首次取图标。

    设置页可能在窗口已经切到亮色主题后才由延迟构建器创建。不能再假定首次创建总是
    暗色主题，否则新按钮会错过既有的主题刷新。优先读取外观控制器写入的动态属性，
    找不到时回退读取祖先 SettingsWindow 的主题下拉框。
    """
    widget = parent
    while widget is not None:
        value = widget.property("settingsLightTheme")
        if value is not None:
            return bool(value)
        theme_combo = getattr(widget, "theme_combo", None)
        if theme_combo is not None:
            return is_light_theme(theme_combo.currentText())
        widget = widget.parentWidget()
    return False


class _CurrentPageStackLayout(QStackedLayout):
    """Exclude hidden pages from all Qt sizing paths, including wrapped text."""
    def sizeHint(self):
        page = self.currentWidget()
        return page.sizeHint() if page is not None else QSize(0, 0)

    def minimumSize(self):
        page = self.currentWidget()
        return page.minimumSizeHint() if page is not None else QSize(0, 0)

    def hasHeightForWidth(self):
        page = self.currentWidget()
        return page.hasHeightForWidth() if page is not None else False

    def heightForWidth(self, width):
        page = self.currentWidget()
        if page is None: return 0
        height = page.heightForWidth(width)
        return height if height >= 0 else page.sizeHint().height()


class ContentSizedStackedWidget(QFrame):
    """Shared stack whose current page alone determines its layout size."""
    currentChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stack = _CurrentPageStackLayout(self)
        self._stack.setContentsMargins(0, 0, 0, 0)
        self._stack.currentChanged.connect(self._current_changed)

    def _current_changed(self, index):
        self._stack.invalidate()
        self.updateGeometry()
        self.currentChanged.emit(index)

    def addWidget(self, widget): return self._stack.addWidget(widget)
    def removeWidget(self, widget): self._stack.removeWidget(widget)
    def currentWidget(self): return self._stack.currentWidget()
    def currentIndex(self): return self._stack.currentIndex()
    def setCurrentIndex(self, index): self._stack.setCurrentIndex(index)
    def setCurrentWidget(self, widget): self._stack.setCurrentWidget(widget)
    def widget(self, index): return self._stack.widget(index)
    def count(self): return self._stack.count()
    def indexOf(self, widget): return self._stack.indexOf(widget)



def create_inline_stack(parent, keys):
    """创建设置弹窗中按 key 管理页面的自适应堆叠控件。

    参数：parent 是父控件；keys 是页面 key 的可迭代对象，例如更新策略或版本检测方式。
    返回值：(stack, pages)，pages 是 key 到 (page, page_layout) 的字典。
    副作用：只创建控件和布局，不读写配置，不切换当前页。
    调用限制：调用方负责向 pages[key][1] 添加具体字段，并把 stack 加入外层布局。
    """
    stack = ContentSizedStackedWidget(parent)
    stack.setObjectName("settingsInlineStack")
    stack.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
    pages = {}
    for key in keys:
        page = QFrame(stack)
        page.setObjectName("settingsInlinePage")
        page.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        page_layout = QVBoxLayout(page)
        UiScaleManager.for_widget(parent).bind_layout(page_layout, margins=(0, 0, 0, 0), spacing=12)
        pages[key] = (page, page_layout)
        stack.addWidget(page)
    return stack, pages


def create_path_row(line_edit, button):
    """创建“路径输入框 + 选择按钮”的水平行。

    主要作用：让下载目录、aria2 路径等同类设置保持一致的输入结构。
    输入参数：line_edit 是路径输入控件；button 是右侧选择文件/文件夹按钮。
    返回值：QHBoxLayout，调用方负责加入外层布局。
    副作用：只创建布局并把两个传入控件加入布局，不读写配置。
    """

    row = QHBoxLayout()
    UiScaleManager.for_widget(line_edit).bind_layout(row, margins=(0, 0, 0, 0), spacing=8)
    row.addWidget(line_edit, 1)
    row.addWidget(button)
    return row


def create_settings_scroll_page(
    parent,
    spacing=SETTINGS_PAGE_SPACING,
    margins=SETTINGS_PAGE_MARGINS,
):
    """创建设置页通用滚动页面骨架。

    主要作用：统一设置页的 BounceScrollArea、settingsPage 容器和纵向布局，避免每个页面
    重复手写滚动区、边距和背景 objectName。
    输入参数：parent 是页面堆栈或父控件；spacing 是页面纵向控件间距；margins 是内容边距。
    返回值：三元组 (page, container, layout)，调用方把内容加到 layout，最后直接返回 page。
    副作用：只创建控件和布局，不读写配置，不连接信号。
    调用限制：适合使用 BounceScrollArea 的普通设置页；软件配置树页面目前不是滚动页，不要硬套。
    """

    # 放在函数内导入，避免 settings_widgets 加载期触发 common_widgets.__init__ 后形成循环导入。
    from modules.common_widgets.bounce_scroll_area import BounceScrollArea

    page = BounceScrollArea(parent)
    page.setWidgetResizable(True)
    page.setFrameShape(QFrame.NoFrame)

    container = QFrame()
    container.setObjectName("settingsPage")
    layout = QVBoxLayout(container)
    UiScaleManager.for_widget(parent).bind_layout(layout, margins=margins, spacing=spacing)
    page.setWidget(container)
    return page, container, layout


def create_settings_content_panel(parent):
    """创建带边框的设置内容面板。

    参数：parent 是面板的 Qt 父控件。
    返回值：二元组 (panel, layout)，调用方把列表、网格或树控件加入 layout。
    副作用：只创建统一主题面板和布局，不读写配置。
    调用限制：适合承载需要清晰边界的主内容区，不要用于每个重复条目。
    """
    panel = QFrame(parent)
    panel.setObjectName("settingsContentPanel")
    layout = QVBoxLayout(panel)
    UiScaleManager.for_widget(parent).bind_layout(layout, margins=(8, 8, 8, 8), spacing=8)
    return panel, layout


def create_settings_grid():
    """创建设置页通用两列表格布局。

    主要作用：左侧标签固定宽度，右侧控件统一起始位置，避免表单出现明显空沟或错位。
    输入参数：无。
    返回值：已设置边距、间距和列伸缩规则的 QGridLayout。
    副作用：只创建布局，不创建控件，不读写配置。
    """

    grid = QGridLayout()
    UiScaleManager().bind_layout(grid, margins=(0, 0, 0, 0), horizontal=14, vertical=12)
    grid.setColumnMinimumWidth(0, 86)
    grid.setColumnStretch(0, 0)
    grid.setColumnStretch(1, 1)
    return grid


def add_grid_row(grid, row, label_text, widget_or_layout, parent):
    """向设置表格里追加一行。

    主要作用：统一设置每一行的标签位置和控件高度，避免路径行、数字行、复选框行互相错位。
    输入参数：grid 是目标 QGridLayout；row 是当前行号；label_text 为空时控件跨两列；
    widget_or_layout 是右侧控件或水平布局；parent 用于创建标签和分隔线时建立 Qt 父子关系。
    返回值：下一行行号，方便调用方连续写 row = add_grid_row(...)。
    副作用：会把新建标签和传入控件/布局加入 grid，不读写配置。
    """

    label = QLabel(label_text, parent)
    label.setMinimumHeight(32)
    label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    if not label_text:
        if isinstance(widget_or_layout, QHBoxLayout):
            grid.addLayout(widget_or_layout, row, 0, 1, 2)
        else:
            widget_or_layout.setMinimumHeight(32)
            grid.addWidget(widget_or_layout, row, 0, 1, 2)
        return row + 1

    grid.addWidget(label, row, 0)
    if isinstance(widget_or_layout, QHBoxLayout):
        grid.addLayout(widget_or_layout, row, 1)
    else:
        widget_or_layout.setMinimumHeight(32)
        grid.addWidget(widget_or_layout, row, 1)
    return row + 1


def add_grid_separator(grid, row, parent):
    """向设置表格里追加跨两列分隔线。

    主要作用：大类设置之间用细线拉开层次，避免整页参数挤在一起看不清。
    输入参数：grid 是目标 QGridLayout；row 是当前行号；parent 是分隔线父控件。
    返回值：下一行行号。
    副作用：会创建并加入一个 settingsSeparator，不读写配置。
    """

    grid.addWidget(create_separator(parent), row, 0, 1, 2)
    return row + 1


def create_separator(parent):
    """创建设置窗口通用分隔线。

    主要作用：给设置页、弹窗和表格中的内容分组提供统一分隔线。
    输入参数：parent 是 Qt 父控件。
    返回值：objectName 为 settingsSeparator 的 QFrame。
    副作用：只创建控件，不读写配置。
    """

    separator = QFrame(parent)
    separator.setObjectName("settingsSeparator")
    separator.setFixedHeight(2)
    return separator


def create_settings_page_title(text, parent):
    """创建设置页主标题。

    主要作用：区分页面主标题和页面内部小节标题，避免都使用 settingsTitle 导致视觉层级
    和后续 QSS 调整混在一起。
    输入参数：text 是标题文字；parent 是 Qt 父控件。
    返回值：设置好 objectName 的 QLabel。
    副作用：只创建控件，不修改配置或文件。
    """

    label = QLabel(text, parent)
    label.setObjectName("settingsPageTitle")
    return label


def create_settings_section_title(text, parent):
    """创建设置页内部小节标题。

    主要作用：用于“aria2 RPC 高级设置”“显示位置管理”等页面内分组标题，视觉上
    应弱于页面主标题。
    输入参数：text 是标题文字；parent 是 Qt 父控件。
    返回值：设置好 objectName 的 QLabel。
    副作用：只创建控件，不修改配置或文件。
    """

    label = QLabel(text, parent)
    label.setObjectName("settingsSectionTitle")
    return label


def create_hint_label(text, parent, word_wrap=True):
    """创建设置窗口通用说明文字。

    主要作用：统一 settingsHint 的创建方式，避免每个页面重复 QLabel、setObjectName、
    setWordWrap 这三步。
    输入参数：text 是说明文字；parent 是 Qt 父控件；word_wrap 控制是否自动换行。
    返回值：设置好 objectName 和换行行为的 QLabel。
    副作用：只创建控件，不修改配置或文件。
    """

    label = QLabel(text, parent)
    label.setObjectName("settingsHint")
    label.setWordWrap(word_wrap)
    # 说明文字只应占据自身内容高度，不能在可伸缩弹窗中被布局拉成大块空白。
    label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
    return label


def create_warning_label(text="", parent=None, word_wrap=True, hidden=False):
    """创建设置窗口通用警告文字。

    主要作用：统一 settingsWarning 的创建方式，适合错误提示、风险提示和保存前校验提示。
    输入参数：text 是警告文字；parent 是 Qt 父控件；word_wrap 控制是否自动换行；hidden
    表示创建后是否默认隐藏。
    返回值：设置好 objectName、换行和可见状态的 QLabel。
    副作用：只创建控件，不修改配置或文件。
    """

    label = QLabel(text, parent)
    label.setObjectName("settingsWarning")
    label.setWordWrap(word_wrap)
    if hidden:
        label.hide()
    return label


def create_settings_button(text, parent, role="primary"):
    """创建设置窗口通用文字按钮。

    主要作用：用 role 表达按钮语义，避免所有按钮都复用 settingsOkBtn，导致“删除”、
    “导入导出”、“确定”看起来完全一样。
    输入参数：text 是按钮文字；parent 是 Qt 父控件；role 可选 primary、secondary、toolbar、
    danger，未知值会按 toolbar 处理。
    返回值：已设置 objectName、最小高度和鼠标样式的 QPushButton。
    副作用：只创建控件，不连接信号，不读写配置。
    注意：具体颜色、边框和 hover/pressed 状态由 settings_partials QSS 统一控制。
    """

    object_names = {
        "primary": "settingsPrimaryButton",
        "secondary": "settingsSecondaryButton",
        "toolbar": "settingsToolbarButton",
        "danger": "settingsDangerButton",
    }
    button = QPushButton(text, parent)
    button.setObjectName(object_names.get(role, object_names["toolbar"]))
    UiScaleManager.for_widget(parent).bind_metrics(button, min_height=30)
    button.setCursor(Qt.PointingHandCursor)
    return button


def create_settings_navigation_tab(text, parent, tooltip=""):
    """创建设置页内部堆栈使用的文字导航标签。

    主要作用：区分“切换子页面”和“执行当前页操作”两种交互，避免把二级页面入口做成
    导入、删除一类的普通工具栏按钮。
    输入参数：text 是标签文字；parent 是所属导航条；tooltip 是可选说明。
    返回值：可 check 的 QRadioButton，调用方通过 QButtonGroup 控制选中态。
    副作用：只创建控件，不切换页面、不读写配置。
    调用限制：需要加载 settings_partials QSS 才会呈现底部指示线导航样式。
    """

    # 单选按钮自带“当前页面”语义，QSS 将 indicator 绘制成代理设置同款绿色状态点。
    button = QRadioButton(text, parent)
    button.setObjectName("settingsSubpageNavTab")
    button.setCheckable(True)
    button.setCursor(Qt.PointingHandCursor)
    button.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    UiScaleManager.for_widget(parent).bind_metrics(button, min_height=36)
    if tooltip:
        button.setToolTip(tooltip)
    return button


def create_dialog_ok_cancel_buttons(parent, ok_text="确定", cancel_text="取消"):
    """创建弹窗底部常用“确定/取消”按钮对。

    主要作用：统一项目无边框弹窗底部按钮的语义样式、尺寸和默认行为，避免每个弹窗重复
    创建 primary/secondary 按钮。
    输入参数：parent 是 Qt 父控件；ok_text/cancel_text 是按钮文字。
    返回值：(ok_button, cancel_button) 元组；调用方负责连接 clicked 信号并加入布局。
    副作用：只创建按钮，不连接信号，不读写配置。
    """

    ok_button = create_settings_button(ok_text, parent, "primary")
    cancel_button = create_settings_button(cancel_text, parent, "secondary")
    # FramelessDialog 通过该属性识别 Enter 应触发的业务提交按钮，不能直接调用 accept()
    # 否则字段校验、保存信号和调用方绑定的 _on_ok/_save 会被绕过。
    ok_button.setProperty("dialogAcceptButton", True)
    scale = UiScaleManager.for_widget(parent)
    scale.bind_metrics(ok_button, min_width=88, min_height=32)
    scale.bind_metrics(cancel_button, min_width=88, min_height=32)
    ok_button.setAutoDefault(False)
    cancel_button.setAutoDefault(False)
    return ok_button, cancel_button


def create_tool_button(icon_path, parent):
    """创建设置页通用小图标按钮。

    主要作用：用于选择文件、选择文件夹、添加项等只需要图标的小操作按钮。
    输入参数：icon_path 是 Qt 资源路径或文件路径；parent 是 Qt 父控件。
    返回值：已设置 objectName、固定尺寸、iconPath 属性和鼠标样式的 QToolButton。
    副作用：只创建按钮，不连接信号；主题切换时 SettingsAppearanceController 会读取
    iconPath 属性刷新明暗图标。
    """

    button = QToolButton(parent)
    button.setObjectName("settingsToolButton")
    scale = UiScaleManager.for_widget(parent)
    scale.bind_metrics(button, fixed_size=(32, 28), icon_size=18)
    button.setProperty("iconPath", str(icon_path))
    # 延迟创建的设置页必须从父级继承当前主题，不能把首次图标固定成暗色版本。
    button.setIcon(get_settings_tool_icon(icon_path, _settings_context_is_light(parent)))
    button.setCursor(Qt.PointingHandCursor)
    return button


def refresh_settings_tool_button_icons(root, light_theme):
    """按当前主题统一刷新设置页小图标按钮的颜色。

    参数：root 是包含按钮的设置窗口或页面；light_theme 表示是否为亮色主题。
    返回值：无。
    副作用：重设 settingsToolButton 和 settingsTreeActionButton 的图标；所有按钮都从
    iconPath 读取资源路径并使用同一组公共颜色，不允许页面单独指定图标颜色。
    调用限制：创建按钮时必须通过 create_tool_button()/create_tree_action_button() 保存 iconPath。

    嵌入模式下 window_frame 会被重新挂载到宿主控件，按钮的实际父链不再经过
    settings_window。为确保两种模式都能找到所有按钮，搜索范围同时包含 root 和
    root.window_frame（如果存在且已被重新挂载）。
    """
    roots = {root}
    frame = getattr(root, "window_frame", None)
    if frame is not None and frame is not root:
        roots.add(frame)
    seen = set()
    for search_root in roots:
        for button in search_root.findChildren(QToolButton):
            if id(button) in seen:
                continue
            seen.add(id(button))
            if button.objectName() not in {"settingsToolButton", "settingsTreeActionButton"}:
                continue
            icon_path = button.property("iconPath")
            if not icon_path:
                continue
            button.setIcon(get_settings_tool_icon(icon_path, light_theme))
            button.setIconSize(button.iconSize())


def create_tree_action_button(icon_path, parent, tooltip=""):
    """创建软件配置树右侧的图标操作按钮。

    主要作用：树操作按钮比普通路径选择小工具按钮更高、更适合竖排工具条，不能继续和
    settingsToolButton 共用同一个 objectName，否则 QSS 尺寸和 Python 固定尺寸会互相覆盖。
    输入参数：icon_path 是图标资源或文件路径；parent 是 Qt 父控件；tooltip 是鼠标悬停说明。
    返回值：已设置 objectName、图标、固定尺寸和 tooltip 的 QToolButton。
    副作用：只创建控件，不连接信号，不修改配置或文件。
    """

    button = QToolButton(parent)
    button.setObjectName("settingsTreeActionButton")
    button.setProperty("iconPath", str(icon_path))
    # 树操作按钮与路径选择按钮共用数字输入框箭头的颜色策略。
    button.setIcon(get_settings_tool_icon(icon_path, _settings_context_is_light(parent)))
    UiScaleManager.for_widget(parent).bind_metrics(button, fixed_size=(34, 34), icon_size=18)
    button.setToolTip(tooltip)
    button.setCursor(Qt.PointingHandCursor)
    return button


def create_spin_box(minimum, maximum, parent):
    """创建设置窗口通用整数输入框。

    主要作用：用于下载并发数、端口、重试次数等整数设置，避免用户输入非法字符。
    输入参数：minimum/maximum 是允许范围；parent 是 Qt 父控件。
    返回值：已设置范围和上下箭头显示方式的 QSpinBox。
    副作用：只创建控件，不读写配置。
    """

    spin_box = QSpinBox(parent)
    spin_box.setRange(minimum, maximum)
    spin_box.setButtonSymbols(QSpinBox.UpDownArrows)
    return spin_box


def set_control_tooltip(control, tooltip):
    """为设置控件及其实际接收鼠标事件的子输入区同步设置提示。

    参数：control 是普通控件或 QSpinBox/QDoubleSpinBox；tooltip 是用户可见说明文字。
    返回值：无。
    副作用：修改控件和数字输入框内部 QLineEdit 的 tooltip，不读写配置。
    调用限制：数字输入框的鼠标通常落在内部 lineEdit，直接对外层调用 setToolTip 在
    PySide6 下可能不显示提示；后续设置页的数字控件必须使用本函数。
    """
    control.setToolTip(tooltip)
    line_edit_getter = getattr(control, "lineEdit", None)
    if callable(line_edit_getter):
        line_edit = line_edit_getter()
        if line_edit is not None:
            line_edit.setToolTip(tooltip)


def create_scale_control_row(
    parent,
    scale_manager,
    label_text,
    minimum,
    maximum,
    single_step,
    page_step,
    tick_interval,
    unit_text,
    slider_tooltip,
    spin_tooltip,
    label_width=86,
    spin_width=92,
    unit_width=18,
):
    """创建设置页通用的“滑杆 + 数字输入 + 单位”控制行。

    主要作用：统一界面缩放、列表缩放、字体尺寸等同类控件的结构和尺寸，避免每个页面
    单独手写宽度、间距和对齐方式。
    输入参数：parent 是 Qt 父控件；scale_manager 用于把固定尺寸转换为当前界面缩放尺寸；
    label_text 是左侧标签；minimum/maximum 是取值范围；single_step/page_step/tick_interval
    控制滑杆步进；unit_text 是右侧单位文字；两个 tooltip 分别用于滑杆和数字框。
    返回值：包含 layout、label、slider、spin_box、suffix_label 的字典，调用方负责保存需要
    后续读写的控件引用。
    副作用：只创建界面控件，不读写配置文件，也不连接信号。
    注意：视觉颜色和上下箭头图片仍由 settings_partials 下的 QSS 统一控制。
    """

    def px(value):
        return scale_manager.px(value) if scale_manager is not None else value

    row = QHBoxLayout()
    (scale_manager or UiScaleManager()).bind_layout(row, margins=(0, 0, 0, 0), spacing=10)

    label = QLabel(label_text, parent)
    label.setObjectName("settingsScaleLabel")
    (scale_manager or UiScaleManager()).bind_metrics(label, min_width=label_width)
    label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

    slider = DiscreteTickSlider(Qt.Horizontal, parent)
    slider.setObjectName("settingsScaleSlider")
    slider.setRange(minimum, maximum)
    slider.setSingleStep(single_step)
    slider.setPageStep(page_step)
    slider.setTickInterval(tick_interval)
    tick_values = range(minimum, maximum + 1, tick_interval)
    slider.set_tick_configuration(tick_values)
    slider.setToolTip(slider_tooltip)

    spin_box = QSpinBox(parent)
    spin_box.setObjectName("settingsScaleSpinBox")
    spin_box.setRange(minimum, maximum)
    spin_box.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    (scale_manager or UiScaleManager()).bind_metrics(spin_box, fixed_width=spin_width)
    set_control_tooltip(spin_box, spin_tooltip)

    suffix_label = QLabel(unit_text, parent)
    suffix_label.setObjectName("settingsScaleSuffixLabel")
    suffix_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    (scale_manager or UiScaleManager()).bind_metrics(suffix_label, min_width=unit_width)

    row.addWidget(label)
    row.addWidget(slider, 1)
    row.addWidget(spin_box)
    row.addWidget(suffix_label)

    return {
        "layout": row,
        "label": label,
        "slider": slider,
        "spin_box": spin_box,
        "suffix_label": suffix_label,
    }


def setup_proxy_input(line_edit, light_theme=False):
    """给代理类单行输入框设置语义 objectName。

    主要作用：把代理地址、代理端口、RPC 地址等输入框标记为 settingsProxyInput，由 QSS
    统一控制颜色、边框和禁用状态。
    输入参数：line_edit 是 QLineEdit；light_theme 是旧调用兼容参数，当前不直接使用。
    返回值：无。
    副作用：会修改控件 objectName，不读写配置。
    """

    line_edit.setObjectName("settingsProxyInput")


def setup_path_input(line_edit, light_theme=False):
    """给路径类单行输入框设置语义 objectName 并标记可拖拽导入。

    主要作用：把脚本路径、安装目录、图标路径等输入框标记为 settingsPathInput，方便 QSS
    识别路径语义（字体与普通输入框一致）；同时设置 winboxPathDrop 属性，让全局拖拽过滤器
    支持从浏览器/文件管理器拖入路径。
    输入参数：line_edit 是 QLineEdit；light_theme 是旧调用兼容参数，当前不直接使用。
    返回值：无。
    副作用：会修改控件 objectName 和动态属性，不读写配置。
    """

    line_edit.setObjectName("settingsPathInput")
    line_edit.setProperty("winboxPathDrop", True)


def setup_code_input(text_edit, light_theme=False):
    """给规则/代码类多行输入框设置语义 objectName。

    主要作用：把扩展名列表、脚本参数、规则文本等多行输入标记为 settingsCodeInput，避免
    继续伪装成路径输入框。
    输入参数：text_edit 是 QTextEdit 或兼容控件；light_theme 是旧调用兼容参数。
    返回值：无。
    副作用：会修改控件 objectName，不读写配置。
    """

    text_edit.setObjectName("settingsCodeInput")


def setup_name_input(line_edit, light_theme=False):
    """给普通名称输入框设置语义 objectName。

    主要作用：把软件名称、文件夹名称、预设名称等普通文本输入标记为 settingsNameInput，
    避免继续使用路径输入样式。
    输入参数：line_edit 是 QLineEdit；light_theme 是旧调用兼容参数，当前不直接使用。
    返回值：无。
    副作用：会修改控件 objectName，不读写配置。
    """

    line_edit.setObjectName("settingsNameInput")


def setup_theme_combo(combo_box, light_theme=False):
    """给主题下拉框设置稳定 objectName。

    主要作用：让主题选择框继续使用历史 `themeCombo` QSS 选择器，避免破坏现有主题样式。
    输入参数：combo_box 是 QComboBox 或兼容控件；light_theme 是旧调用兼容参数。
    返回值：无。
    副作用：会修改控件 objectName，不读写配置。
    """

    combo_box.setObjectName("themeCombo")
