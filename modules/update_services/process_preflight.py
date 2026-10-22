"""更新前进程关闭预检服务。

主要作用：收集实际运行的配置进程与更新文件占用者，并在用户确认后关闭、提权
重试和重检。这里不创建 Qt 控件，允许专用 QThread 调用；弹窗文案和用户确认
由 main_pages.update_confirmations 在主线程完成。
"""

import os
import logging
import subprocess
import time
import zipfile
from pathlib import Path

from modules.config_manager import resolve_portable_path
from modules.platform_services.file_occupancy import find_file_holders, FileHolderLookupError
from modules.update_services.file_inventory import iter_files
from modules.update_services.package_naming import resolve_rename_source
from modules.platform_services.process_service import (
    close_processes_by_pids,
    find_processes_holding_file,
    get_process_executable_path,
)
from modules.update_services.process_actions import (
    configured_running_processes,
    kill_processes_by_pids_elevated,
)


# 可以自动关闭的系统组件：关闭后 Windows 会自动重启，不会丢失数据。
_RESTARTABLE_SYSTEM_PROCS = {
    "sihost.exe",
    "taskhostw.exe",
    "taskhostex.exe",
    "dllhost.exe",
    "runtimebroker.exe",
}
_WINDOWS_ROOT = "c:\\windows"
_PROCESS_SETTLE_SECONDS = 0.2


def build_process_close_plan(config):
    """收集本次更新需要处理的实际进程和文件占用状态。

    参数：config 是标准化更新配置。返回值是包含 running_configured、locked_files、
    all_pids、system_names 的字典。副作用：只读文件写入状态和系统进程信息。

    配置进程和文件持有者会按 PID 合并，因此同一个软件自身既运行又锁住 exe 时
    只会关闭一次。调用方可用返回结果构造一次确认框。
    """

    warnings = []
    try:
        running_configured = configured_running_processes(config)
    except OSError:
        logging.getLogger("winbox.update").warning("Configured process query failed", exc_info=True)
        running_configured = []
        warnings.append("运行进程检查未完成，将继续尝试更新；实际安装或复制失败会另行提示。")
    try:
        locked_files = collect_locked_target_files(config)
    except FileHolderLookupError as exc:
        logging.getLogger("winbox.update").warning("File occupancy query incomplete", exc_info=True)
        locked_files = list(exc.holders.items())
        warnings.append(str(exc))
    except (OSError, subprocess.SubprocessError):
        logging.getLogger("winbox.update").warning("File occupancy scan failed", exc_info=True)
        locked_files = []
        warnings.append("文件占用扫描未完成，将继续尝试更新；实际安装或复制失败会另行提示。")
    locked_pids, system_names = categorize_holders(locked_files)
    configured_pids = process_pids(running_configured)
    return {
        "running_configured": running_configured,
        "locked_files": locked_files,
        "all_pids": merge_pids(configured_pids, locked_pids),
        "system_names": system_names,
        "warnings": warnings,
    }


def close_confirmed_processes(config, pids):
    """关闭已获用户确认的进程，并在必要时提权重试一次。

    参数：config 是本次运行配置；pids 是确认框中已展示并确认的 PID 列表。
    返回值：(success, plan)，plan 是最终重检到的进程占用计划。
    副作用：会发送 WM_CLOSE、强制结束进程，必要时弹一次 Windows UAC。

    此函数会等待进程退出，必须只在 worker 线程中调用。无论 taskkill 或 UAC
    返回什么，都以最后一次实际进程/文件重检作为成功依据。
    """

    close_processes_by_pids(pids, graceful_wait=5)
    # taskkill/WM_CLOSE 返回后给 Windows 很短的时间释放映像和文件句柄；最终是否
    # 可以更新仍由下面的完整重检决定，不再无条件等待整整一秒。
    time.sleep(_PROCESS_SETTLE_SECONDS)
    remaining = build_process_close_plan(config)
    if remaining["all_pids"]:
        # 所有 PID 都来自已确认的配置进程或文件持有者；一次 UAC 批量处理，
        # 不会按每个进程重复弹系统权限窗口。
        kill_processes_by_pids_elevated(remaining["all_pids"])
        time.sleep(_PROCESS_SETTLE_SECONDS)
        remaining = build_process_close_plan(config)

    return not remaining["all_pids"] and not remaining["system_names"], remaining


def process_pids(processes):
    """从进程字典列表提取有效 PID 并去重。

    参数：processes 是包含 pid 的字典列表。返回值：整数 PID 列表。副作用：无。
    """

    pids = []
    for process in processes:
        try:
            pid = int(process.get("pid"))
        except (AttributeError, TypeError, ValueError):
            continue
        if pid > 0 and pid not in pids and pid != os.getpid():
            pids.append(pid)
    return pids


def merge_pids(*pid_groups):
    """合并多组 PID，保留首次出现顺序。"""

    merged = []
    for group in pid_groups:
        for pid in group:
            if pid not in merged:
                merged.append(pid)
    return merged


def holder_is_system(holder):
    """判断文件持有进程是否属于不应自动关闭的 Windows 系统进程。

    参数：holder 是包含 name/pid 的字典，可能带 own_image 标记。返回值：布尔值。
    副作用：读取一次可执行文件路径。路径未知时保守视为系统进程，避免误关；
    目标软件自身映像带 own_image 标记时例外，允许按用户确认关闭。
    """

    if holder.get("own_image"):
        return False
    exe_path = str(get_process_executable_path(holder.get("pid")) or "").lower()
    if not exe_path:
        return True
    if not exe_path.startswith(_WINDOWS_ROOT):
        return False
    return os.path.basename(exe_path).lower() not in _RESTARTABLE_SYSTEM_PROCS


def categorize_holders(locked_files):
    """把文件持有者分成可关闭 PID 和不可自动关闭的系统进程名。

    参数：locked_files 是 [(文件路径, 持有者列表)]。返回值：(pids, system_names)。
    副作用：只查询进程路径，不关闭进程。
    """

    closable_pids = []
    system_names = set()
    seen = set()
    for _, holders in locked_files:
        for holder in holders:
            try:
                pid = int(holder.get("pid"))
            except (TypeError, ValueError):
                continue
            if pid in seen:
                continue
            seen.add(pid)
            if pid == os.getpid():
                continue
            if holder_is_system(holder):
                system_names.add(str(holder.get("name", "")))
            else:
                closable_pids.append(pid)
    return closable_pids, system_names


def collect_locked_target_files(config):
    """收集更新目标目录中当前无法写入的文件及其持有进程。

    参数：config 读取 target_dirs、install_dir、target_dir。返回值：占用文件列表。
    副作用：用只读方式探测写入权限和 tasklist 持有者信息，不修改目标文件。
    """

    raw_dirs = list(config.get("target_dirs", []) or [])
    if config.get("install_dir"):
        raw_dirs.append(config["install_dir"])
    if config.get("target_dir"):
        raw_dirs.append(config["target_dir"])

    target_dirs = []
    for raw in raw_dirs:
        text = str(raw or "").strip()
        if not text:
            continue
        try:
            directory = resolve_portable_path(text)
        except Exception:
            continue
        path = Path(os.path.abspath(directory))
        if path not in target_dirs:
            target_dirs.append(path)

    candidate_files = archive_target_files(config, target_dirs)
    if candidate_files is None:
        # 只有完整扫描才合并父子目录；精确归档映射仍保留每个目标根目录。
        roots = [path for path in target_dirs
                 if not any(path != other and path.is_relative_to(other) for other in target_dirs)]
        candidate_files = (item for root in roots if root.is_dir() for item in iter_files(root))
    return _collect_locked_files(candidate_files)


def archive_target_files(config, target_dirs):
    """返回解压类更新真正会覆盖、删除或恢复的现有文件集合。

    参数：config 是本次运行配置，target_dirs 是已解析目标目录。返回值：可安全
    精确分析时返回 Path 集合；其他策略、非 ZIP、整目录替换或解析异常时返回 None，
    调用方必须退回完整目录扫描。副作用：只读取 ZIP 中央目录和文件系统元数据。

    这里只优化 merge 型 ``extract_copy``。任何不确定情况都保守回退，确保速度优化
    不会漏掉整目录删除、自定义脚本或未知归档格式可能占用的文件。
    """

    if config.get("strategy") != "extract_copy" or config.get("delete_old_dir"):
        return None
    package_text = str(config.get("_preflight_package_path", "") or "").strip()
    package_path = Path(package_text)
    if package_path.suffix.lower() != ".zip" or not package_path.is_file():
        return None
    if not target_dirs:
        return set()

    try:
        with zipfile.ZipFile(package_path, "r") as archive:
            all_infos = archive.infolist()
            if any(not _safe_archive_member(info.filename) for info in all_infos):
                return None
            safe_infos = all_infos
            members = [
                Path(info.filename.replace("\\", "/"))
                for info in safe_infos
                if not info.is_dir()
            ]
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile, ValueError):
        return None

    mode = str(config.get("extract_mode", "") or "flatten_top_folder")
    if mode == "flatten_all_files":
        relative_files = [Path(member.name) for member in members]
    elif mode == "flatten_top_folder":
        archive_paths = [Path(info.filename.replace("\\", "/")) for info in safe_infos]
        first_parts = {path.parts[0].casefold() for path in archive_paths if path.parts}
        strip_top = (
            bool(members)
            and len(first_parts) == 1
            and all(len(member.parts) > 1 for member in members)
        )
        relative_files = [Path(*member.parts[1:]) if strip_top else member for member in members]
    else:
        relative_files = members

    candidates = {
        target / relative
        for target in target_dirs
        for relative in relative_files
        if relative.parts
    }
    candidates.update(_configured_existing_files(config, target_dirs))
    return {path for path in candidates if path.is_file()}


def _safe_archive_member(member_name):
    """判断 ZIP 成员是否为可映射到目标目录的普通相对路径。"""

    normalized = str(member_name or "").replace("\\", "/")
    if not normalized or normalized.startswith("/"):
        return False
    path = Path(normalized)
    return not path.is_absolute() and ".." not in path.parts


def _configured_existing_files(config, target_dirs):
    """收集显式删除、配置恢复和重命名步骤会操作的现有文件。"""

    files = set()
    for key in ("pre_delete_paths", "post_delete_paths", "config_keep_paths"):
        for raw in config.get(key, []) or []:
            text = str(raw or "").strip()
            if not text:
                continue
            resolved = resolve_portable_path(text)
            paths = [resolved] if resolved.is_absolute() else [target / text for target in target_dirs]
            for path in paths:
                if path.is_file():
                    files.add(path)
                elif path.is_dir():
                    files.update(item for item in path.rglob("*") if item.is_file())

    for rule in config.get("rename_rules", []) or []:
        source = str(rule.get("source", "") or "").strip() if isinstance(rule, dict) else ""
        if not source:
            continue
        source_path = resolve_portable_path(resolve_rename_source(source, config.get("_preflight_package_path")))
        if source_path.is_file():
            files.add(source_path)
        new_name = str(rule.get("name", "") or "").strip()
        if new_name:
            target_path = source_path.with_name(new_name)
            if target_path.is_file():
                files.add(target_path)

    if config.get("copy_extra_files"):
        for rule in config.get("extra_copy_rules", []) or []:
            text = str(rule or "")
            if "=>" not in text:
                continue
            _, target_raw = text.split("=>", 1)
            target_text = target_raw.strip()
            if not target_text:
                continue
            resolved = resolve_portable_path(target_text)
            paths = [resolved] if resolved.is_absolute() else [target / target_text for target in target_dirs]
            files.update(path for path in paths if path.is_file())
    return files


def find_locked_files(directory):
    """扫描一个目录内无法以读写模式打开的文件。

    参数：directory 是目录路径。返回值：占用文件列表。副作用：只尝试打开文件，
    不写入内容；读取失败时查询潜在持有进程。
    """

    directory = Path(directory)
    if not directory.is_dir():
        return []

    return _collect_locked_files(iter_files(directory))


def _collect_locked_files(file_paths):
    """先探测写入状态，再一次查询全部失败文件；每轮重检重新收集。"""
    locked = []
    for path in file_paths:
        try:
            with open(path, "r+b"):
                pass
        except OSError:
            locked.append(str(path))
    holders = find_file_holders(locked)
    return [(path, holders[path]) for path in locked]


def find_locked_file(file_path):
    """探测一个现有文件是否无法读写打开，失败时返回占用者信息。"""

    file_path = Path(file_path)
    if not file_path.is_file():
        return None
    try:
        with open(file_path, "r+b"):
            pass
    except OSError:
        holders = find_processes_holding_file(str(file_path))
        return str(file_path), holders
    return None
