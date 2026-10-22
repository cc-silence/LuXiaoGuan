"""更新页软件图标辅助函数。

主要作用：集中处理更新页表格里的软件图标选择、用户自定义图标和暗色主题反转。
输入参数：软件配置 node、软件配置管理器、当前主题状态。
返回值：QIcon。
副作用：可能读取图标文件；不会修改配置、不会写文件、不会启动更新。
调用限制：这里只处理更新页表格展示图标，不处理侧边栏、标题栏或 QSS background-image 图标。
"""

from PySide6.QtGui import QIcon


def themed_update_software_icon(node, software_manager, light_theme):
    """根据软件配置和当前主题返回更新页使用的软件图标。

    参数：node 是软件配置字典；software_manager 用于解析 {APP_DIR} 等可移植路径；
    light_theme 表示当前是否亮色主题。
    返回值：可直接设置到 QTableWidgetItem 的 QIcon。
    副作用：如果使用用户图标，会读取该图标文件；暗色主题反转时会创建内存图标缓存。
    """
    icon = _software_icon(node, software_manager)
    if not light_theme and node.get("invert_icon_dark_theme", False):
        icon_value = node.get("icon", "")
        if icon_value:
            icon_path = software_manager.resolve_path(icon_value)
            if icon_path and icon_path.exists():
                from modules.icon_utils import invert_icon_file
                return invert_icon_file(str(icon_path))
    return icon


def _software_icon(node, software_manager):
    """返回更新页软件基础图标。

    参数：node 是软件配置；software_manager 用于解析用户自定义图标路径。
    返回值：QIcon。用户图标不存在时按脚本类型回退到默认图标。
    副作用：只读取文件存在性，不修改文件。
    """
    icon_value = node.get("icon", "")
    if icon_value:
        icon_path = software_manager.resolve_path(icon_value)
        if icon_path and icon_path.exists():
            return QIcon(str(icon_path))
    script_type = node.get("script_type", "")
    if script_type == "bat":
        return QIcon(":/icons/images/icons/cil-terminal.png")
    return QIcon(":/icons/images/icons/cil-cloud-download.png")
