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


def build_main_stylesheet(config_manager, theme, scale_manager):
    """组合主题、公共控件和窗口外框；不加载设置页通用规则。"""
    light = theme == "light.qss"
    stylesheet = "\n".join((theme_stylesheet_text(config_manager, theme),
                             combo_popup_stylesheet(light), _chrome_stylesheet(light)))
    if light:
        stylesheet = replace_light_theme_qss_icons(stylesheet)
    return scale_manager.scale_stylesheet(stylesheet) + "\n" + scale_manager.scaled_main_stylesheet()
