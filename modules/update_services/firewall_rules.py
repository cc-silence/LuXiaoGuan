"""防火墙规则管理

主要作用：更新后把指定 EXE 添加到 Windows 防火墙出站允许规则。
规则创建统一交给平台层：GPO 禁止本地规则合并时写入策略规则库，并验证生效。

依赖：
    - config_manager.resolve_portable_path 解析可移植路径令牌
    - windows_firewall 处理策略感知的规则创建和验证

此模块不依赖 Qt、界面代码或其他业务模块（config_manager 除外）。
"""

from pathlib import Path

from modules.config_manager import resolve_portable_path
from modules.platform_services.windows_firewall import ensure_persistent_allow_rules
from modules.update_services.update_logging import write_update_log


def apply_firewall_rules(config, targets, software_id):
    """更新后把指定 EXE 加入 Windows 防火墙出站允许规则。

    参数：
        config: 更新配置。读取 add_firewall_rules/firewall_rule_paths。
        targets: 当前更新策略解析出的目标目录列表；相对路径会按这些目录解析。
    返回：(是否成功, 普通用户可读的信息列表)。
    副作用：
        可能触发 UAC，并通过平台层创建并验证持久防火墙规则。
    """
    messages = []
    invalid_paths = False
    if not config.get("add_firewall_rules"):
        return True, messages

    raw_paths = config.get("firewall_rule_paths", [])
    if not raw_paths:
        return False, ["已开启更新后添加防火墙规则，但未填写需要放行的 EXE。"]

    base_targets = []
    for target in targets:
        if not target:
            continue
        path = Path(str(target))
        # multi_target_copy 的目标既可以是目录，也可以是完整目标文件；防火墙相对路径
        # 必须相对最终文件所在目录解析，不能附加到文件名后面。
        if (
            config.get("strategy") == "multi_target_copy"
            and path.suffix
            and not (path.exists() and path.is_dir())
        ):
            path = path.parent
        base_targets.append(path)
    resolved_paths = []
    for raw in raw_paths:
        text = str(raw or "").strip()
        if not text:
            continue
        resolved = resolve_portable_path(text)
        if resolved.is_absolute():
            resolved_paths.append(resolved)
        elif base_targets:
            for target in base_targets:
                resolved_paths.append(target / text)
        else:
            messages.append(f"防火墙程序 {text} 是相对路径，但当前更新没有目标目录，已跳过。")
            invalid_paths = True

    unique_paths = []
    seen = set()
    for path in resolved_paths:
        key = str(path).casefold()
        if key in seen:
            continue
        seen.add(key)
        unique_paths.append(path)

    # 预检阶段用户取消或超时跳过的目标目录：该目录下的 EXE 没有被更新，
    # 不应要求添加防火墙规则，更不应因此让本次更新判失败。
    skipped_key = {str(value).casefold() for value in config.get("_create_skip_dirs", [])}

    valid_paths = []
    skipped_count = 0
    for path in unique_paths:
        # 目标可能是目录（复制进目录）或文件：跳过列表里可能是目录本身或其文件路径。
        if any(key in skipped_key for key in (str(path).casefold(), str(path.parent).casefold())):
            messages.append(f"防火墙程序 {path} 位于本次跳过的目录，未创建规则。")
            skipped_count += 1
            continue
        if path.suffix.lower() != ".exe":
            messages.append(f"防火墙程序 {path} 不是 .exe，已跳过。")
            invalid_paths = True
            continue
        if not path.exists() or not path.is_file():
            messages.append(f"防火墙程序 {path} 不存在，已跳过。")
            invalid_paths = True
            continue
        valid_paths.append(path)

    if not valid_paths:
        # 全部路径都因用户跳过目录而无需处理：不算失败，只是本次不添加规则。
        if skipped_count > 0 and not invalid_paths:
            return True, messages or ["本次更新的目标目录已被跳过，未添加防火墙规则。"]
        return False, messages or ["没有可用于添加防火墙规则的有效 EXE。"]
    if invalid_paths:
        return False, messages + ["部分防火墙程序无效，未创建任何规则，请修正配置后重试。"]
    if not str(software_id or "").strip():
        return False, messages + ["软件条目缺少稳定 ID，无法隔离防火墙规则所有权。"]

    # 更新规则在 gpedit 中直接显示 EXE 完整路径；软件 ID 只放在内部 owner 中，
    # 用于路径变化时删除该软件旧规则，避免不同软件或启动规则互相覆盖。
    owner = f"update:{software_id}"
    write_update_log(config, f"防火墙规则存储：LocalGPO；所有权：{owner}")
    for path in valid_paths:
        write_update_log(config, f"防火墙规则程序：{path.resolve()}")
    diagnostics = []
    firewall_messages = ensure_persistent_allow_rules(
        valid_paths,
        owner=owner,
        display_names=[str(path.resolve()) for path in valid_paths],
        diagnostics=diagnostics,
    )
    for detail in diagnostics:
        write_update_log(config, detail)
    messages.extend(firewall_messages)
    return not any("失败" in message for message in firewall_messages), messages
