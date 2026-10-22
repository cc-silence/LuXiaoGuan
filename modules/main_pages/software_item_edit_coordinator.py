"""主页面软件下载项编辑协调器。

主要作用：复用下载页和更新页从软件配置树加载节点、打开编辑弹窗、替换节点并保存的共同流程。
输入参数：软件配置管理器、软件 ID、弹窗工厂、父窗口和主题样式；可选回调用于调整弹窗初始展开区块。
返回值：(status, result_data)，status 描述成功、取消或失败原因。
副作用：成功保存时会写入 software_items.json；弹窗工厂由 UI 组合根提供，协调器不依赖 settings_pages。
调用限制：必须在 Qt 主线程调用；调用方仍负责按自己的页面刷新列表、清缓存和显示提示。
"""

from modules.config_services.software_item_tree import find_software_node, replace_software_node


STATUS_SAVED = "saved"
STATUS_CANCELLED = "cancelled"
STATUS_MISSING_ID = "missing_id"
STATUS_MISSING_NODE = "missing_node"
STATUS_MISSING_FACTORY = "missing_factory"
STATUS_REPLACE_FAILED = "replace_failed"


def edit_software_item(
    software_manager,
    software_id,
    dialog_factory,
    parent,
    stylesheet,
    configure_dialog=None,
):
    """执行软件下载项编辑的通用保存流程。

    参数：software_manager 负责读写配置；software_id 定位节点；dialog_factory 创建编辑弹窗；
    parent 是弹窗父窗口；stylesheet 是当前主题 QSS；configure_dialog 可选地调整弹窗初始状态。
    返回值：(status, result_data)。成功时 result_data 是新节点，其他状态时为 None。
    副作用：用户确认且节点替换成功时会保存 software_items.json。
    """
    if not software_id:
        return STATUS_MISSING_ID, None
    if dialog_factory is None:
        return STATUS_MISSING_FACTORY, None

    config = software_manager.load()
    current_node = find_software_node(config.get("items", []), software_id)
    if current_node is None:
        return STATUS_MISSING_NODE, None

    dialog = dialog_factory(software_manager, dict(current_node), parent)
    dialog.setStyleSheet(stylesheet)
    if configure_dialog is not None:
        configure_dialog(dialog)
    if dialog.exec() != dialog.Accepted:
        return STATUS_CANCELLED, None

    try:
        result_data = dict(dialog.result_data)
        result_data["update"] = software_manager.prepare_update_config(
            result_data.get("update", {}),
            result_data.get("id"),
        )
    except ValueError:
        return STATUS_REPLACE_FAILED, None
    if not replace_software_node(config.get("items", []), software_id, result_data):
        return STATUS_REPLACE_FAILED, None

    software_manager.save(config)
    return STATUS_SAVED, result_data
