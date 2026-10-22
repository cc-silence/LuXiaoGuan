"""更新前目标目录收集函数。

主要作用：根据更新策略和配置字段，整理主线程预检需要检查的目标目录。
输入参数：策略名称、标准化后的更新配置、软件配置管理器。
返回值：Path 列表。
副作用：无；只解析路径，不创建目录、不显示弹窗、不修改配置。
调用限制：这里只描述“哪些目录需要预检”，用户确认和目录创建仍由主线程页面流程处理。
"""


def collect_update_target_dirs(strategy, update_config, software_manager):
    """返回指定更新策略需要预检的目标目录。

    参数：strategy 是更新策略名称；update_config 是标准化后的更新配置；
    software_manager 用于解析 {APP_DIR} 等可移植路径。
    返回值：按配置顺序排列的 Path 列表，空字段会被忽略。
    副作用：无。
    """
    if strategy in {"extract_copy", "multi_target_copy"}:
        result = []
        for value in update_config.get("target_dirs", []):
            text = str(value or "").strip()
            if text:
                result.append(software_manager.resolve_path(text))
        return result

    if strategy == "run_installer":
        install_dir = update_config.get("install_dir", "")
        return [software_manager.resolve_path(install_dir)] if install_dir else []

    if strategy == "custom_script":
        target_dir = update_config.get("target_dir", "")
        return [software_manager.resolve_path(target_dir)] if target_dir else []

    return []
