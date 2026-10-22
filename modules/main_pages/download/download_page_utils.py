"""下载页数据转换工具函数。

主要作用：从 SoftwareDownloadPage 中提取的纯数据转换逻辑，
包括软件配置树展开为扁平列表等操作。

所有函数都不依赖 Qt 控件，只处理纯字典数据。
"""

from modules.network_policy import allowed_channels
from modules.config_services.software_items_manager import SoftwareItemsConfigManager


def current_download_mode(row_data):
    """返回当前允许方式中的实际下载方式：'direct' 或 'proxy'。"""

    node = row_data["node"]
    modes = configured_download_modes(row_data)
    mode = str(row_data.get("temp_download_mode") or modes["default"] or "direct").lower()
    if modes.get(mode, False):
        return mode
    return modes["default"]


def configured_download_modes(row_data):
    """返回软件配置允许的下载方式和默认方式。

    参数：row_data 是下载页的软件行。返回值是标准化后的 download_modes 字典。
    副作用：无。旧配置只有 download_mode 时会按其原值推导为唯一允许方式。
    """

    node = row_data.get("node", {}) if isinstance(row_data, dict) else {}
    return SoftwareItemsConfigManager.normalize_download_modes(
        node.get("download_modes"),
        node.get("download_mode", "direct"),
    )


def is_download_mode_allowed(row_data, mode):
    """判断软件下载项是否允许使用指定的直连或代理方式。"""

    normalized_mode = "proxy" if mode == "proxy" else "direct"
    return bool(configured_download_modes(row_data).get(normalized_mode, False))


def task_mode(task):
    """根据已提交给 aria2 的 options 判断任务实际下载方式。"""
    return "proxy" if task.options.get("http-proxy") or task.options.get("https-proxy") else "direct"


def task_network_channel(task):
    """根据 aria2 options 判断已提交任务实际使用的联网通道。"""
    return task.options.get("winbox-network-channel") or task_mode(task)


def looks_like_network_error(error):
    """粗略识别脚本联网失败，避免把普通脚本逻辑错误误提示成代理问题。"""
    text = str(error or "")
    markers = (
        "urlopen error",
        "WinError 10013",
        "timed out",
        "Temporary failure",
        "Name or service not known",
        "Network is unreachable",
        "Connection refused",
        "Connection reset",
        "请求",
        "网络",
    )
    return any(marker in text for marker in markers)


def network_channels(row_data, config_manager):
    """返回当前软件在当前代理开关下允许使用的联网通道。

    副作用：只读取 settings.ini 和系统代理；不会修改配置。用于按钮禁用、检测和下载前校验。
    """
    return allowed_channels(config_manager, current_download_mode(row_data))


def active_network_channel(row_data, config_manager):
    """返回本次下载正在使用的通道，默认使用第一个允许通道。"""
    channels = network_channels(row_data, config_manager)
    if not channels:
        raise RuntimeError("当前软件下载方式没有可用联网通道")
    index = int(row_data.get("network_channel_index", 0) or 0)
    return channels[max(0, min(index, len(channels) - 1))]


def flatten_software_items(nodes, parents=None):
    """递归展开软件配置树，展平为分类 + 软件行的列表。

    设置页允许文件夹分类；主页面不做深层树缩进，而是显示"一级 / 二级"分类标题。
    如果软件直接放在根目录，归入"未分类"，避免因为没有父文件夹而消失。
    同时收集文件夹节点信息，供主页面显示自定义分类图标。

    输入参数：
        nodes：软件配置节点列表，每个节点为 dict，包含 type、name、children 等字段。
        parents：递归参数，当前所在文件夹路径列表。

    返回值：(entries, folder_nodes)
        entries：list[dict]，每个 dict 包含 category、category_parts、node。
        folder_nodes：dict[category_name -> node]，分类名称到文件夹节点的映射。
    """
    parents = list(parents or [])
    entries = []
    folder_nodes = {}
    for node in nodes:
        node_type = node.get("type")
        if node_type == "folder":
            name = str(node.get("name") or "未命名分类").strip() or "未命名分类"
            folder_path = (parents + [name])[:2]
            category_name = " / ".join(folder_path)
            folder_nodes[category_name] = node
            sub_entries, sub_folders = flatten_software_items(
                node.get("children", []), folder_path
            )
            entries.extend(sub_entries)
            folder_nodes.update(sub_folders)
            continue
        if node_type != "software" or not node.get("enabled", True):
            continue
        category_parts = parents[:2] or ["未分类"]
        entries.append(
            {
                "category": " / ".join(category_parts),
                "category_parts": category_parts,
                "node": node,
            }
        )
    return entries, folder_nodes
