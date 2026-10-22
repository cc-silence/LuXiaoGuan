"""页面公共样式工具函数和共享 QSS。

主要作用：存放下载页和更新页共同使用的字号计算、字体尺寸、
占位符注入等工具函数，以及共享 QSS（如右键菜单）。

所有函数都是模块级纯函数，不依赖类实例，参数明确。

从 download_page_styles.py 拆分出来，消除更新页对下载页样式表的直接依赖。

QSS 模板来自 themes/dark_page_base.qss 和 light_page_base.qss，
运行时加载后替换字体/尺寸/箭头占位符。
"""

from modules.config_manager import THEMES_DIR as _THEMES_DIR
from modules.dialog_theme import context_menu_stylesheet, create_themed_menu, table_header_stylesheet


# 页面基础模板是运行期间不变的静态资源。缓存文本可避免主题切换、缩放预览和页面切换反复读盘。
_QSS_TEMPLATE_CACHE = {}


def _load_qss_template(filename):
    """从 themes/ 目录读取 .qss 模板文件（UTF-8）。"""
    cached = _QSS_TEMPLATE_CACHE.get(filename)
    if cached is None:
        cached = (_THEMES_DIR / filename).read_text(encoding="utf-8")
        _QSS_TEMPLATE_CACHE[filename] = cached
    return cached


def font_points(scale_manager):
    """返回页面使用的三档字体大小：(标题pt, 表头pt, 正文pt)。"""
    if scale_manager is None:
        return 16, 9, 10
    return scale_manager.font_pt(6), scale_manager.font_pt(-1), scale_manager.font_pt()


def mini_button_size(scale_manager):
    """返回状态栏小按钮尺寸，和运行时 setFixedSize 保持一致。"""
    if scale_manager is None:
        return 28
    return scale_manager.px(28)


def font_family(scale_manager):
    """返回页面使用的主界面字体。"""
    if scale_manager is not None and hasattr(scale_manager, "_resolved_font"):
        return scale_manager._resolved_font()
    return "Microsoft YaHei"


def inject_font_points(stylesheet, title_pt, header_pt, body_pt, scale_manager):
    """替换 QSS 中的字体大小和族占位符。

    QSS 本身大量使用 { }，不直接用 f-string 拼整段样式，避免 CSS 大括号被 Python 当成表达式。
    参数：
        stylesheet: 含 __TITLE_PT__ 等占位符的 QSS 字符串
        title_pt, header_pt, body_pt: 三档字号
        scale_manager: 用于获取 mini_button_size 和 font_family
    返回值：
        替换完成后的 QSS 字符串
    """
    mini = mini_button_size(scale_manager)
    family = font_family(scale_manager)
    # 固定尺寸先从原始模板转换；字号和小按钮占位符已是缩放后的值，随后再注入。
    if scale_manager is not None:
        stylesheet = scale_manager.scale_stylesheet(stylesheet)
    return (
        stylesheet.replace("__TITLE_PT__", str(title_pt))
        .replace("__HEADER_PT__", str(header_pt))
        .replace("__BODY_PT__", str(body_pt))
        .replace("__TOOLTIP_PT__", str(body_pt + 1))
        .replace("__MINI_BTN_PX__", str(mini))
        .replace("__FONT_FAMILY__", family)
        .replace('"Segoe UI Semibold"', f'"{family}"')
        .replace('"Segoe UI"', f'"{family}"')
    )


def dark_base_page_stylesheet(scale_manager):
    """生成页面深色主题基础 QSS。

    从 themes/dark_page_base.qss 加载模板，替换字体/尺寸占位符。
    包含两个页面（下载/更新）共用的选择器。
    """
    title_pt, header_pt, body_pt = font_points(scale_manager)
    stylesheet = _load_qss_template("dark_page_base.qss") + "\n" + table_header_stylesheet(False)
    return inject_font_points(stylesheet, title_pt, header_pt, body_pt, scale_manager)


def light_base_page_stylesheet(arrow_url, scale_manager):
    """生成页面亮色主题基础 QSS。

    从 themes/light_page_base.qss 加载模板，替换箭头/字体/尺寸占位符。
    arrow_url 是亮色主题专用的深色箭头图标绝对路径。
    """
    title_pt, header_pt, body_pt = font_points(scale_manager)
    arrow_image = f"url({arrow_url})"
    stylesheet = _load_qss_template("light_page_base.qss") + "\n" + table_header_stylesheet(True)
    stylesheet = stylesheet.replace("__ARROW_URL__", arrow_image)
    return inject_font_points(stylesheet, title_pt, header_pt, body_pt, scale_manager)
