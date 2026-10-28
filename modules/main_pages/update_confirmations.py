"""更新页确认交互

主要作用：把更新前需要用户确认的弹窗逻辑留在页面层，避免
update_services 这类执行服务反向依赖 Qt 弹窗或具体页面。

使用方式：软件更新页在启动后台 worker 前调用这里的函数，用户确认后
把普通配置参数交给进程预检 worker。此模块只负责 Qt 确认框和文案，
绝不执行进程等待、taskkill 或 UAC，避免阻塞主线程。
"""

from modules.release_identity import DISPLAY_NAME
from pathlib import Path

from modules.common_widgets.common_dialogs import (
    ConfirmDialog,
    create_themed_confirm_dialog,
    exec_confirm_with_countdown,
)
from modules.config_manager import resolve_portable_path
from modules.platform_services.process_service import group_process_holders
from modules.update_services.process_preflight import process_pids


def confirm_process_close_plan(plan, parent=None, stylesheet=""):
    """在主线程确认是否允许后台关闭进程。

    参数：
        plan: 更新服务层收集的实际进程与文件占用计划。
        parent: 父窗口，用于弹出主题统一的确认框；没有父窗口时不弹窗。
        stylesheet: 可选 QSS，用于让确认框和当前页面主题一致。
    返回值：
        "close" 表示已确认，需要启动后台关闭；"continue" 表示无需关闭或
        用户选择继续；"cancel" 表示取消更新。
    副作用：只显示一次 Qt 确认框，不关闭进程、不等待、不调用 UAC。

    进程关闭可能等待数秒，绝不能在这里执行。调用方收到 "close" 后必须启动
    专用 QThread，主线程保持事件循环和窗口重绘能力。
    """

    running_configured = plan["running_configured"]
    locked_files = plan["locked_files"]
    all_pids = plan["all_pids"]
    system_names = plan["system_names"]

    if not all_pids and not system_names:
        return "continue"

    if not all_pids:
        # 只有系统进程占用时绝不自动关闭。保留“仍要尝试”语义，但不再出现
        # 配置进程确认或关闭失败确认，整个预检仍只有这一个窗口。
        if parent is None:
            return "cancel"
        message = _system_only_message(locked_files, system_names)
        dialog = create_themed_confirm_dialog(
            "文件占用警告",
            message,
            parent,
            stylesheet,
            yes_text="取消更新",
            no_text="仍要尝试",
        )
        if dialog.exec() == ConfirmDialog.Rejected:
            return "continue"
        return "cancel"

    if parent is not None:
        dialog = create_themed_confirm_dialog(
            "关闭运行中的程序",
            _close_update_processes_message(running_configured, locked_files, system_names),
            parent,
            stylesheet,
            yes_text="关闭并继续更新",
            no_text="取消更新",
        )
        # 10 秒倒计时自动执行"关闭并继续更新"（杀进程），用户可提前点取消中止。
        # 按钮文字会显示为"关闭并继续更新 10s/9s/.../1s"，倒计时结束自动继续更新。
        result = exec_confirm_with_countdown(dialog, seconds=10, timeout_action="yes")
        if result != ConfirmDialog.Accepted:
            return "cancel"

    return "close"


def _close_update_processes_message(running_configured, locked_files, system_names):
    """生成唯一一次更新前关闭确认的正文。

    参数：running_configured 是实际运行的配置进程；locked_files 是文件占用列表；
    system_names 是不可自动关闭的系统进程名集合。
    返回值：确认框正文。副作用：无。
    """

    names = []
    for process in running_configured:
        name = str(process.get("name", "")).strip()
        if name and name not in names:
            names.append(name)
    message = "更新前需要关闭以下正在运行的程序：\n\n"
    if names:
        message += "\n".join(f"  • {name}" for name in names)
    # 已在上方列出的配置进程同时作为文件持有者时，不再重复展示一次。只有
    # 额外程序（例如注入 DLL 的浏览器）或查不到持有者的锁文件才需要补充说明。
    extra_locked_files = _extra_locked_files(locked_files, process_pids(running_configured))
    if extra_locked_files:
        message += "\n\n以下更新文件还被其它程序占用：\n" + _format_locked_files(extra_locked_files)
    if system_names:
        message += "\n\n系统进程（" + "、".join(sorted(system_names)) + f"）不会被 {DISPLAY_NAME} 自动关闭。"
    message += "\n\n关闭这些程序可能丢失未保存的更改。是否关闭并继续更新？"
    return message


def _extra_locked_files(locked_files, configured_pids):
    """筛出未在配置进程列表中展示过的文件占用者。

    参数：locked_files 是文件与持有者列表；configured_pids 是已在确认框上半段列出的
    配置进程 PID。返回值：仅含额外持有者或未知持有者的同结构列表。
    副作用：无。

    同一个 geph 进程既会被配置进程枚举命中，也会因锁住自身 exe 被文件占用扫描
    命中。确认框只需要展示一次该进程；但其它进程注入 DLL、或无法查出持有者时，
    仍必须保留文件占用信息让用户知道额外风险。
    """

    configured_set = set(configured_pids)
    extras = []
    for file_path, holders in locked_files:
        extra_holders = []
        for holder in holders:
            try:
                pid = int(holder.get("pid"))
            except (AttributeError, TypeError, ValueError):
                pid = None
            if pid not in configured_set:
                extra_holders.append(holder)
        if extra_holders or not holders:
            extras.append((file_path, extra_holders))
    return extras


def _remaining_processes_message(running_configured, locked_files, system_names):
    """生成关闭失败后的页面信息，不再创建第二个确认弹窗。"""

    names = []
    for process in running_configured:
        name = str(process.get("name", "")).strip()
        if name and name not in names:
            names.append(name)
    message = "更新未启动：以下程序或文件仍被占用，请手动关闭后重试。"
    if names:
        message += "\n运行中的程序：" + "、".join(names)
    if locked_files:
        message += "\n" + _format_locked_files(locked_files)
    if system_names:
        message += "\n系统进程：" + "、".join(sorted(system_names)) + "。请重启电脑后再更新。"
    return message


def _format_locked_files(locked_files):
    """把 [(文件路径, holders)] 列表格式化成可读的多行文本。

    参数：locked_files 是预检得到的占用文件列表。
    返回值：多行文本字符串。
    副作用：无。
    """

    lines = []
    for file_path, holders in locked_files[:10]:
        name = Path(file_path).name
        if holders:
            if all(h.get("own_image") for h in holders):
                lines.append(f"  • {name} ← 正在运行（软件自身）")
            else:
                lines.append(f"  • {name} ← 被 {group_process_holders(holders)} 占用")
        else:
            lines.append(f"  • {name} ← 被其它程序占用")
    if len(locked_files) > 10:
        lines.append(f"  • ……（还有 {len(locked_files) - 10} 个文件）")
    return "\n".join(lines)


def _system_only_message(locked_files, system_names):
    """生成"只有系统进程占用、需要重启电脑"的提示文案。

    参数：locked_files 是占用文件列表；system_names 是系统进程名集合。
    返回值：提示弹窗正文文本。
    副作用：无。
    """

    names = "、".join(sorted(system_names)) if system_names else "未知系统进程"
    return (
        f"更新目标目录中有文件被系统进程占用，{DISPLAY_NAME} 不会自动关闭系统进程：\n\n"
        + _format_locked_files(locked_files)
        + f"\n\n占用进程：{names}\n"
        "请重启电脑后再更新（系统进程重启后会释放文件）。"
    )


def confirm_firewall_rules(config, parent=None, stylesheet=""):
    """在更新前由主线程询问用户是否添加防火墙规则。

    参数：
        config: 标准化更新配置，读取 add_firewall_rules/firewall_confirm/
            firewall_confirm_hint/firewall_rule_paths。
        parent: 父窗口，用于弹出主题统一的确认框。
        stylesheet: 可选 QSS，用于让确认框和当前页面主题一致。
    返回值：
        True 表示本次继续添加防火墙规则；False 表示本次更新跳过添加防火墙。
    副作用：只显示确认弹窗，不修改防火墙规则、文件或配置。
    """
    if not config.get("firewall_confirm") or not config.get("add_firewall_rules"):
        return True
    raw_paths = [str(value) for value in config.get("firewall_rule_paths", []) if str(value).strip()]
    if not raw_paths:
        return True
    display_paths = []
    for raw in raw_paths:
        try:
            display_paths.append(str(resolve_portable_path(raw)))
        except Exception:
            display_paths.append(raw)
    message = (
        f"更新完成后，{DISPLAY_NAME} 将把以下程序加入 Windows 防火墙的出站允许规则：\n\n"
        + "\n".join(f"  • {path}" for path in display_paths)
        + "\n\n是否继续添加？"
    )
    hint = str(config.get("firewall_confirm_hint", "") or "").strip()
    if hint:
        message += "\n" + hint
    dialog = create_themed_confirm_dialog(
        "添加防火墙规则确认",
        message,
        parent,
        stylesheet,
        yes_text="添加防火墙",
        no_text="跳过",
    )
    return dialog.exec() == ConfirmDialog.Accepted


def confirm_windows_restart(software_name, reasons, parent=None, stylesheet=""):
    """在更新后由主线程询问用户是否立即重启 Windows。

    参数 software_name 是刚完成更新的软件名称；reasons 是待重启原因列表；parent 和
    stylesheet 用于公共主题弹窗。返回值为 True 表示用户选择立即重启，False 表示稍后重启。
    副作用：只创建并显示确认弹窗，不直接调用 shutdown.exe。
    """

    reason_text = "、".join(str(reason) for reason in reasons if str(reason).strip()) or "系统组件更新"
    message = (
        f"{software_name} 已完成安装，但 {reason_text} 尚待重启完成。\n\n"
        "立即重启后才能可靠启动 VMware。"
    )
    dialog = create_themed_confirm_dialog(
        "需要重启 Windows",
        message,
        parent,
        stylesheet,
        yes_text="立即重启",
        no_text="稍后重启",
    )
    return dialog.exec() == ConfirmDialog.Accepted
