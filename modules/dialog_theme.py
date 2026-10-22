"""通用无边框弹窗主题样式构建。

主要作用：组合当前主题 QSS、设置弹窗兜底样式、亮色图标替换和缩放样式。
输入参数：ConfigManager、主题名称/亮色状态和可选 UiScaleManager。
返回值：可直接传给 QWidget.setStyleSheet() 的 QSS 字符串。
副作用：读取主题文件；亮色主题首次调用时可能生成深色图标缓存。
调用限制：这是跨页面弹窗主题层，不包含具体设置页面或业务控件逻辑。
"""

import re
import os
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMenu

from modules.app_settings import is_light_theme
from modules.config_manager import BASE_DIR, THEMES_DIR


# 主题和设置片段在运行期间视为静态资源。缓存只保存文件文本，缩放 QSS 仍由调用时动态附加。
_THEME_TEXT_CACHE = {}


def build_base_stylesheet(config_manager, light_theme=False, theme_name=None, scale_manager=None, *, themes_dir=None):
    """构建指定主题的通用弹窗基础 QSS。

    返回的最终 QSS 在主题和设置片段之后追加公共复选框与下拉列表规则，
    确保主窗口、设置窗口和 FramelessDialog 弹窗使用同一套公共片段。
    """
    theme_stylesheet = theme_stylesheet_text(config_manager, theme_name)
    fallback_stylesheet = fallback_settings_stylesheet(theme_name, light_theme, themes_dir=themes_dir)
    if "#settingsWindowFrame" in theme_stylesheet:
        base_stylesheet = theme_stylesheet
    else:
        base_stylesheet = f"{theme_stylesheet}\n\n{fallback_stylesheet}"

    if is_light_theme(theme_name, light_theme):
        base_stylesheet = replace_light_theme_qss_icons(base_stylesheet)
    base_stylesheet = f"{base_stylesheet}\n\n{combo_popup_stylesheet(is_light_theme(theme_name, light_theme))}\n{spin_box_stylesheet(is_light_theme(theme_name, light_theme))}"

    base_stylesheet += "\n" + table_header_stylesheet(is_light_theme(theme_name, light_theme))
    if scale_manager is None:
        return base_stylesheet
    base_stylesheet = scale_manager.scale_stylesheet(base_stylesheet)
    return f"{base_stylesheet}\n\n{scale_manager.scaled_settings_stylesheet()}"


def build_current_base_stylesheet(config_manager, light_theme=False, scale_manager=None):
    """按当前保存主题构建通用弹窗基础 QSS。

    参数：config_manager 用于读取当前主题；light_theme 是调用方当前亮色状态；
    scale_manager 用于注入缩放样式。
    返回值：完整 QSS 字符串。
    副作用：读取 settings.ini 和主题文件。
    """
    theme_name = config_manager.load().get("ui", "theme", fallback=config_manager.DEFAULT_THEME_LABEL)
    return build_base_stylesheet(config_manager, light_theme, theme_name, scale_manager)


def replace_light_theme_qss_icons(stylesheet_text):
    """把亮色主题 QSS 中的白色资源图标替换为磁盘深色缓存。

    参数：stylesheet_text 是尚未应用的 QSS 文本。返回值：替换后的 QSS；生成反色图标失败
    时保持原文本。副作用：首次遇到图标时可能在 images/icons/dark 写入缓存文件。
    调用约定：主窗口、设置窗口和独立弹窗都必须在应用亮色 QSS 前调用，避免运行时切换
    后下拉箭头仍引用浅色资源，重启后才被重新初始化修正。
    """
    try:
        from modules import resources_rc  # noqa: F401
        from modules.icon_utils import generate_dark_icon_file

        replacements = {}
        for resource_path in (
            ":/icons/images/icons/icon_close.png",
            ":/icons/images/icons/cil-caret-top.png",
            ":/icons/images/icons/cil-caret-bottom.png",
            ":/icons/images/icons/cil-arrow-bottom.png",
        ):
            icon_path = generate_dark_icon_file(
                resource_path,
                BASE_DIR / "images" / "icons" / "dark",
            )
            icon_url = str(Path(icon_path).resolve()).replace("\\", "/")
            replacements[resource_path] = icon_url
    except Exception:
        return stylesheet_text

    for resource_path, icon_url in replacements.items():
        stylesheet_text = stylesheet_text.replace(f"url({resource_path})", f"url({icon_url})")
    return stylesheet_text


def fallback_settings_stylesheet(theme_name=None, light_theme=False, *, themes_dir=None):
    """读取内置明暗弹窗 QSS 兜底片段。"""
    fallback_name = "settings_light.qss" if is_light_theme(theme_name, light_theme) else "settings_dark.qss"
    try:
        from modules.config_manager import THEMES_DIR
        fallback_path = Path(themes_dir or THEMES_DIR) / "settings_partials" / fallback_name
    except Exception:
        return ""
    return _read_stylesheet_text(fallback_path)


def table_header_stylesheet(light_theme):
    """公共表头的明暗主题配色和细分隔线，供主页面及弹窗复用。"""
    name = "table_header_light.qss" if light_theme else "table_header_dark.qss"
    return _read_stylesheet_text(THEMES_DIR / name)


def collapsible_section_stylesheet(light_theme):
    """\u590d\u7528\u8bbe\u7f6e\u4e2d\u7684\u516c\u5171\u6298\u53e0\u6761\u6837\u5f0f\uff0c\u4e3b\u9875\u9762\u4e0d\u590d\u5236\u660e\u6697\u914d\u8272\u3002"""
    text = fallback_settings_stylesheet(light_theme=light_theme)
    return "\n".join(selectors + " {" + body + "}" for selectors, body in
                      re.findall(r"([^{}]+)\{([^{}]*)\}", text)
                      if "#settingsCollaps" in selectors)


def spin_box_stylesheet(light_theme):
    """读取数字框唯一的明暗主题，设置页、任务页及弹窗共用。"""
    filename = "spin_box_light.qss" if light_theme else "spin_box_dark.qss"
    stylesheet = _read_stylesheet_text(THEMES_DIR / filename)
    return replace_light_theme_qss_icons(stylesheet) if light_theme else stylesheet


def checkbox_theme_stylesheet(light_theme):
    """读取唯一来源的明暗公共 QCheckBox 样式片段。

    参数：light_theme 为 True 时返回亮色片段，否则返回深色片段。
    返回值：UTF-8 QSS 文本；文件缺失时返回空字符串，应用仍可用旧主题规则启动。
    副作用：通过 _read_stylesheet_text 使用内存缓存，不修改主题文件。
    调用约定：调用方必须把返回值追加到自身最终 QSS 的末尾，使其覆盖旧主题中的残留规则。
    """

    filename = "checkbox_light.qss" if light_theme else "checkbox_dark.qss"
    return _read_stylesheet_text(THEMES_DIR / filename)


def theme_stylesheet_text(config_manager, theme_name):
    """读取指定主题 QSS；默认主题或读取失败时返回空字符串。"""
    theme_path = config_manager.theme_path(theme_name)
    if theme_path is None:
        return ""
    return _read_stylesheet_text(theme_path)


def combo_popup_stylesheet(light_theme):
    """组合公共勾选图像、下拉列表尺寸和明暗配色；主窗口、弹窗及公共组件共用。"""
    filename = "combo_popup_light.qss" if light_theme else "combo_popup_dark.qss"
    return "\n\n".join((checkbox_theme_stylesheet(light_theme),
                         _read_stylesheet_text(THEMES_DIR / "combo_popup.qss"),
                         _read_stylesheet_text(THEMES_DIR / filename)))


def _read_stylesheet_text(path):
    """读取并缓存主题文本，避免预览明暗主题时反复同步访问磁盘。

    参数：path 是主题或设置片段的文件路径。
    返回值：UTF-8 文本；读取失败时返回空字符串。
    副作用：首次读取成功后写入模块内存缓存，不修改主题文件。
    调用限制：运行期间编辑 QSS 后需要重启应用才能看到文件改动，发布版主题资源本就按此处理。
    """
    try:
        # 静态资源按绝对路径缓存，命中时不再向磁盘逐级解析真实路径。
        cache_key = os.path.normcase(os.path.abspath(path))
    except OSError:
        return ""
    cached = _THEME_TEXT_CACHE.get(cache_key)
    if cached is not None:
        return cached
    try:
        text = Path(cache_key).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    _THEME_TEXT_CACHE[cache_key] = text
    return text


def extract_selector_property(stylesheet_text, selector, property_name, fallback):
    """从 QSS 选择器中提取单个属性值，找不到时返回 fallback。"""
    if not stylesheet_text:
        return fallback

    selector_pattern = rf"{re.escape(selector)}\s*\{{(?P<body>.*?)\}}"
    selector_match = re.search(selector_pattern, stylesheet_text, flags=re.DOTALL)
    if selector_match is None:
        return fallback

    body = selector_match.group("body")
    property_names = [property_name]
    if property_name == "background-color":
        property_names.append("background")

    for name in property_names:
        value_match = re.search(rf"(?<![\w-]){re.escape(name)}\s*:\s*([^;]+);", body)
        if value_match:
            return value_match.group(1).strip()
    return fallback


def context_menu_stylesheet(light_theme):
    """返回跨设置页和弹窗使用的右键菜单 QSS。

    参数：light_theme 表示菜单当前应使用亮色还是暗色配色。
    返回值：可直接传给 QMenu.setStyleSheet() 的 QSS 字符串。
    副作用：无，不创建控件、不读取配置。
    调用限制：QMenu 是独立弹窗，调用方必须传入当前预览主题，不能只依赖父控件继承样式。
    """
    if light_theme:
        return """
QMenu {
    background-color: #f3f6fb;
    border: 1px solid #c5cfde;
    border-radius: 4px;
    color: #172033;
    padding: 4px 0;
}
QMenu[roundedMenu="true"] { border-radius: 8px; }
QMenu::item {
    padding: 6px 24px 6px 12px;
}
QMenu::item:selected {
    background-color: #d7e0ee;
    color: #172033;
}
QMenu::separator {
    height: 1px;
    background-color: #c5cfde;
    margin: 4px 8px;
}
"""
    return """
QMenu {
    background-color: #2d3240;
    border: 1px solid #3f4a5c;
    border-radius: 4px;
    color: #f8f8f2;
    padding: 4px 0;
}
QMenu[roundedMenu="true"] { border-radius: 8px; }
QMenu::item {
    padding: 6px 24px 6px 12px;
}
QMenu::item:selected {
    background-color: #3b4561;
    color: #f8f8f2;
}
QMenu::separator {
    height: 1px;
    background-color: #3f4a5c;
    margin: 4px 8px;
}
"""


def create_themed_menu(parent, light_theme, *, rounded=False):
    """创建已应用当前主题的 QMenu。

    参数：parent 是菜单所属窗口或控件；light_theme 是当前实时主题状态。
    返回值：已设置明暗主题 QSS 的 QMenu，调用方负责添加 QAction 和执行菜单。
    副作用：创建一个 QMenu 并设置其内联样式，不修改配置。
    """
    # Rounded menus opt into transparent window corners; other menus retain their behavior.
    menu = QMenu(parent)
    if rounded:
        menu.setProperty("roundedMenu", True)
        menu.setWindowFlag(Qt.FramelessWindowHint, True)
        menu.setWindowFlag(Qt.NoDropShadowWindowHint, True)
        menu.setAttribute(Qt.WA_TranslucentBackground, True)
    menu.setStyleSheet(context_menu_stylesheet(light_theme))
    return menu


def task_progress_stylesheet(light_theme, scale_manager=None):
    """复用页面的唯一进度条样式，保证弹窗与入口无关。"""
    filename = 'light_page_base.qss' if light_theme else 'dark_page_base.qss'
    template = _read_stylesheet_text(THEMES_DIR / filename)
    rules = []
    for selectors, body in re.findall(r'([^{}]+)\{([^{}]*)\}', template):
        selected = [selector.strip() for selector in selectors.split(',')
                    if '#taskDialogProgressBar' in selector]
        if selected:
            rules.append(', '.join(selected) + ' {' + body + '}')
    stylesheet = '\n'.join(rules)
    return scale_manager.scale_stylesheet(stylesheet) if scale_manager else stylesheet


def detect_light_theme(widget, fallback=False):
    """沿控件父链检测当前亮色主题。

    参数：widget 是弹窗或编辑控件；fallback 是找不到主题标记时的默认值。
    返回值：布尔值，表示是否应使用亮色配色。
    副作用：只读取父控件样式，不修改控件、配置或文件。
    """
    current = widget
    while current is not None:
        stylesheet = (current.styleSheet() or "").lower().replace(" ", "")
        if any(marker in stylesheet for marker in (
            "#f5f6fa",
            "#f5f7fa",
            "rgb(245,246,250)",
            "rgb(245,247,250)",
        )):
            return True
        if "#181b1f" in stylesheet or "rgb(24,27,31)" in stylesheet:
            return False
        current = current.parent() if hasattr(current, "parent") else None
    return fallback
