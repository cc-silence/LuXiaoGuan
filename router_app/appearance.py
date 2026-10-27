"""主窗口主题组合，与设置页的样式作用域分离。"""
from functools import lru_cache
from modules.config_manager import THEMES_DIR
from modules.dialog_theme import (
    theme_stylesheet_text, combo_popup_stylesheet, replace_light_theme_qss_icons,
)


@lru_cache(maxsize=2)
def _chrome_stylesheet(light):
    """缓存两套窗口外框片段，不重复读取磁盘。"""
    kind = "light" if light else "dark"
    return (THEMES_DIR / ("router_chrome_" + kind + ".qss")).read_text(encoding="utf-8")


@lru_cache(maxsize=2)
def _builtin_main_stylesheet(light):
    """A default or unavailable theme must still supply the full shell theme."""
    name = "light.qss" if light else "dark.qss"
    text = (THEMES_DIR / name).read_text(encoding="utf-8")
    if not text.strip():
        raise ValueError("The built-in application theme is empty: " + name)
    return text


def build_main_stylesheet(config_manager, theme, scale_manager):
    """组合主题、公共控件和窗口外框；不加载设置页通用规则。"""
    light = theme == "light.qss"
    main_theme = theme_stylesheet_text(config_manager, theme)
    if not main_theme.strip():
        main_theme = _builtin_main_stylesheet(light)
    stylesheet = "\n".join((main_theme,
                             combo_popup_stylesheet(light), _chrome_stylesheet(light)))
    if light:
        stylesheet = replace_light_theme_qss_icons(stylesheet)
    return scale_manager.scale_stylesheet(stylesheet) + "\n" + scale_manager.scaled_main_stylesheet()
