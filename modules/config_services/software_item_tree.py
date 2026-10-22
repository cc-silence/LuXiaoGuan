"""软件配置树工具函数。

主要作用：从设置窗口中提取的纯数据转换和校验逻辑，包括树形数据扁平化、
下载项资源校验、导出名称安全处理、导入位置 ID 补全。

所有函数都不依赖 Qt 控件，只处理纯字典数据。需要配置管理器实例时通过参数传入。

输入参数：items（软件配置列表字典）、software_items_manager / update_settings_manager 实例。
返回值：处理后的字典或列表。
副作用：无（prepare_* 函数会在校验失败时抛 ValueError，但不会写磁盘或改界面）。
"""

import copy
import re
import unicodedata
from uuid import uuid4


def flatten_tree_to_two_levels(items):
    """软件配置列表最多保留两层文件夹。

    如果旧配置里有更深层级，把更深层里的软件项移动到第二层文件夹下，尽量保留用户数据。
    深层文件夹的图标只保留前两层的配置，超出两层的文件夹图标取第一层文件夹配置。
    """
    result = []
    folder_map = {}

    def ensure_folder(path_parts):
        safe_parts = [part for part in path_parts[:2] if part]
        if not safe_parts:
            return None
        current_children = result
        current_key = []
        current_node = None
        for part in safe_parts:
            current_key.append(part)
            key = tuple(current_key)
            current_node = folder_map.get(key)
            if current_node is None:
                current_node = {
                    "type": "folder",
                    "id": f"folder_{'_'.join(current_key)}",
                    "name": part,
                    "icon": "",
                    "children": [],
                }
                folder_map[key] = current_node
                current_children.append(current_node)
            current_children = current_node.setdefault("children", [])
        return current_node

    def visit(nodes, parents=None):
        parents = list(parents or [])
        for node in nodes:
            if not isinstance(node, dict):
                continue
            node_type = node.get("type")
            if node_type == "folder":
                name = str(node.get("name") or "未命名文件夹").strip() or "未命名文件夹"
                next_parents = (parents + [name])[:2]
                folder_node = ensure_folder(next_parents)
                if folder_node is not None and node.get("icon") and len(parents) < 2:
                    folder_node["icon"] = node.get("icon", "")
                    if node.get("id"):
                        folder_node["id"] = node.get("id")
                visit(node.get("children", []), next_parents)
            elif node_type == "software":
                target_folder = ensure_folder(parents)
                software_node = dict(node)
                if target_folder is None:
                    result.append(software_node)
                else:
                    target_folder.setdefault("children", []).append(software_node)

    visit(items)
    return result


def safe_export_name(name):
    """Windows 文件名不能包含 <>:\"/\\|?* 这些字符。

    导出默认文件名只做最小清理，不改变下载项自身名称。
    """
    cleaned = re.sub(r'[<>:"/\\|?*]+', "_", str(name)).strip(" ._")
    return cleaned or "下载项"


def _walk_errors(items, errors, software_items_manager, host_mode=False):
    """递归遍历软件配置项，逐项校验资源路径是否有效。"""
    for item in items:
        prepared = dict(item)
        if prepared.get("type") == "folder":
            _walk_errors(prepared.get("children", []), errors, software_items_manager, host_mode)
        elif prepared.get("type") == "software":
            try:
                prepare_software_item(prepared, software_items_manager, host_mode=host_mode)
            except ValueError as e:
                errors.append(str(e))


def collect_errors(items, software_items_manager, host_mode=False):
    """遍历整棵软件配置树，收集所有校验错误并返回列表，不抛异常。

    这样用户一次就能看到所有问题，不用反复点"确定"。
    """
    errors = []
    _walk_errors(flatten_tree_to_two_levels(items), errors, software_items_manager, host_mode)
    return errors


def _prepare_software_items_tree(items, software_items_manager, host_mode=False):
    """递归处理树节点，校验并补全资源路径和配置。"""
    prepared_items = []
    for item in items:
        prepared = dict(item)
        if prepared.get("type") == "folder":
            prepared["icon"] = software_items_manager.prepare_icon_path(prepared.get("icon", ""))
            prepared["children"] = _prepare_software_items_tree(
                prepared.get("children", []), software_items_manager, host_mode
            )
        elif prepared.get("type") == "software":
            prepared = prepare_software_item(prepared, software_items_manager, host_mode=host_mode)
        prepared_items.append(prepared)
    return prepared_items


def prepare_software_items_tree(items, software_items_manager, host_mode=False):
    """保存前先把旧数据或拖拽造成的超深文件夹压平成两层，保证主界面分类规则稳定。"""
    items = flatten_tree_to_two_levels(items)
    return _prepare_software_items_tree(items, software_items_manager, host_mode)


def merge_software_item_trees(local_items, imported_items):
    """按父级和名称递归合并软件配置树。

    参数：local_items 是本机树；imported_items 是分享包或旧 JSON 中的树。
    返回值：``(merged_items, summary)``，summary 记录替换、新增、歧义和类型冲突数量。
    副作用：无，返回全新深拷贝树，不写配置、不访问文件系统。
    调用限制：只在同一父级、相同节点类型、规范化名称唯一时自动合并。不同文件夹内的同名
    软件属于不同项；同名文件夹会递归合并子项；同名软件保留本地 ID 和排序位置。
    """
    if not isinstance(local_items, list) or not isinstance(imported_items, list):
        raise ValueError("软件配置列表格式无效。")
    local_items = copy.deepcopy(flatten_tree_to_two_levels(local_items))
    imported_items = copy.deepcopy(flatten_tree_to_two_levels(imported_items))
    used_ids = _collect_node_ids(local_items)
    summary = {"replaced": 0, "added": 0, "ambiguous": 0, "type_conflicts": 0}

    def unique_id():
        value = str(uuid4())
        while value in used_ids:
            value = str(uuid4())
        used_ids.add(value)
        return value

    def clone_with_new_ids(node):
        cloned = copy.deepcopy(node)
        cloned["id"] = unique_id()
        if cloned.get("type") == "folder":
            cloned["children"] = [clone_with_new_ids(child) for child in cloned.get("children", []) if isinstance(child, dict)]
        return cloned

    def merge_lists(local_nodes, imported_nodes):
        result = list(local_nodes)
        index_by_key = {}
        name_types = {}
        for index, node in enumerate(result):
            if not isinstance(node, dict):
                continue
            name_key = _normalized_node_name(node)
            if name_key is None:
                continue
            node_type = node.get("type")
            index_by_key.setdefault((node_type, name_key), []).append(index)
            name_types.setdefault(name_key, set()).add(node_type)

        matched_keys = set()
        for imported_node in imported_nodes:
            if not isinstance(imported_node, dict) or imported_node.get("type") not in {"folder", "software"}:
                continue
            node_type = imported_node.get("type")
            name_key = _normalized_node_name(imported_node)
            candidates = index_by_key.get((node_type, name_key), []) if name_key is not None else []
            if name_key is not None and len(candidates) == 1 and (node_type, name_key) not in matched_keys:
                local_node = result[candidates[0]]
                if node_type == "folder":
                    replacement = copy.deepcopy(imported_node)
                    replacement["id"] = local_node.get("id") or unique_id()
                    replacement["children"] = merge_lists(
                        local_node.get("children", []),
                        imported_node.get("children", []),
                    )
                else:
                    replacement = copy.deepcopy(imported_node)
                    replacement["id"] = local_node.get("id") or unique_id()
                result[candidates[0]] = replacement
                matched_keys.add((node_type, name_key))
                summary["replaced"] += 1
                continue

            result.append(clone_with_new_ids(imported_node))
            summary["added"] += 1
            if name_key is not None:
                if candidates:
                    summary["ambiguous"] += 1
                elif name_key in name_types and node_type not in name_types[name_key]:
                    summary["type_conflicts"] += 1
        return result

    return merge_lists(local_items, imported_items), summary


def _normalized_node_name(node):
    """返回文件夹或软件项用于合并的规范化名称；空名称不自动合并。"""
    name = unicodedata.normalize("NFKC", str(node.get("name", "") or "")).strip().casefold()
    return name or None


def _collect_node_ids(nodes):
    """递归收集已有节点 ID，避免导入新增节点复用外部或本机 ID。"""
    result = set()
    for node in nodes:
        if not isinstance(node, dict):
            continue
        if node.get("id"):
            result.add(str(node["id"]))
        if node.get("type") == "folder":
            result.update(_collect_node_ids(node.get("children", [])))
    return result


def prepare_software_item(item_data, software_items_manager, host_mode=False):
    """校验单个下载项的资源路径和配置。

    WinBox 不再自动复制资源；这里只校验资源是否位于固定根目录内，并转成 {APP_DIR} 路径。
    注意：所有 ValueError 到上层时都会带上软件名，方便定位问题。
    """
    prepared = dict(item_data)
    item_name = prepared.get("name", "未命名")
    item_id = prepared.get("id")
    try:
        prepared["icon"] = software_items_manager.prepare_icon_path(prepared.get("icon", ""))
    except ValueError as e:
        raise ValueError(f"软件「{item_name}」：{e}")
    if not host_mode:
        prepared["script_type"] = software_items_manager.script_type_from_path(prepared.get("script", ""))
        try:
            prepared["script"] = software_items_manager.prepare_script_path(
                prepared.get("script", ""),
                "获取下载链接脚本",
                required=True,
            )
        except ValueError as e:
            raise ValueError(f"软件「{item_name}」：{e}")
    try:
        prepared["update"] = software_items_manager.prepare_update_config(
            prepared.get("update", {}),
            item_id,
        )
    except ValueError as e:
        raise ValueError(f"软件「{item_name}」：{e}")
    if not host_mode:
        try:
            prepared["version_check"] = software_items_manager.prepare_version_check_config(
                prepared.get("version_check", {}),
                item_id,
            )
        except ValueError as e:
            raise ValueError(f"软件「{item_name}」：{e}")
        prepared["architectures"] = software_items_manager.normalize_architectures(
            prepared.get("architectures")
        )
    return prepared


def ensure_imported_positions(items, update_settings_manager):
    """遍历导入的软件项，把引用的显示位置自动补到 update_settings.json。"""
    all_ids = set()

    def walk(nodes):
        for node in (nodes or []):
            if node.get("type") == "software":
                update = node.get("update", {})
                ids = update.get("display_position_ids") or []
                all_ids.update(ids)
                mid = update.get("display_position_id", "")
                if mid:
                    all_ids.add(mid)
            elif node.get("type") == "folder":
                walk(node.get("children", []))

    if isinstance(items, list):
        walk(items)
    elif isinstance(items, dict) and items.get("type") == "software":
        update = items.get("update", {})
        ids = update.get("display_position_ids") or []
        all_ids.update(ids)
        mid = update.get("display_position_id", "")
        if mid:
            all_ids.add(mid)
    if all_ids:
        update_settings_manager.ensure_positions_exist(list(all_ids))


def find_software_node(items, software_id):
    """在软件配置树中按 ID 递归查找下载项节点。
    
    参数：
        items: 软件配置列表（[{type,id,name,children,...}]）
        software_id: 要查找的下载项 ID
    返回值：匹配的节点字典，或 None。
    """
    for node in (items or []):
        if node.get("type") == "software" and node.get("id") == software_id:
            return node
        if node.get("type") == "folder":
            found = find_software_node(node.get("children", []), software_id)
            if found is not None:
                return found
    return None


def replace_software_node(items, software_id, new_node):
    """在软件配置树中按 ID 递归替换下载项节点。
    
    参数：
        items: 软件配置列表（[{type,id,name,children,...}]）
        software_id: 要替换的下载项 ID
        new_node: 新的节点数据字典
    返回值：找到并替换成功返回 True，未找到返回 False。
    """
    for index, node in enumerate(items or []):
        if node.get("type") == "software" and node.get("id") == software_id:
            items[index] = dict(new_node)
            return True
        if node.get("type") == "folder" and replace_software_node(node.get("children", []), software_id, new_node):
            return True
    return False
