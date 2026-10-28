"""更新策略执行器模块

主要作用：执行具体的更新策略（下载包、解压、复制、安装、自定义脚本），
以及共享辅助函数（签名验证、进程关闭、备份恢复、防火墙规则、删除路径）。

此模块的调用方是 UpdateWorker（update_worker.py），外部通过
StrategyExecutor.execute() 入口使用。
"""

from modules.release_identity import DISPLAY_NAME
import locale
import os
import queue
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path
import shutil
from shutil import copy2, rmtree, copytree
from threading import Event, Thread

from modules.config_manager import resolve_portable_path
from scripts.python_script_host import build_python_script_command, build_logged_python_script_command
from modules.platform_services.update_script_runtime import script_needs_console, run_visible_script
from modules.update_services.process_actions import (
    configured_process_names,
    kill_configured_processes,
    kill_configured_processes_elevated,
)
from modules.update_services.file_deletion_actions import apply_delete_paths
from modules.platform_services.update_elevation import run_elevated_update as run_elevated
from modules.platform_services.runtime import subprocess_creationflags, background_script_command, background_script_options
from modules.platform_services.process_service import find_processes_holding_file, group_process_holders
from modules.platform_services.windows_exit_codes import explain_standard_exit_code
from modules.platform_services.windows_archive_tools import required_7zip_message, resolve_7zip_executable
from modules.update_services.firewall_rules import apply_firewall_rules
from modules.update_services.archive_extractor import (
    copy_extracted_contents,
    extract_archive,
    verify_extracted_files_readable,
)
from modules.update_services.backup_manager import (
    backup_keep_paths,
    cleanup_backup_context,
    restore_keep_paths,
)
from modules.update_services.update_workspace import ensure_update_workspace
from modules.update_services.update_logging import timed_update_step, write_update_log
from modules.update_services.strategy_dispatcher import UpdateStrategyDispatcher
from modules.update_services.package_naming import resolve_rename_source, validate_rename_sources
from modules.update_services.script_completion import reset_script_completion, completed_before_interrupt


# 补丁脚本单次 DISM 最多运行 2 小时；提权宿主必须再多留出时间，避免脚本正常处理
# 解压、多个 CAB 或收尾阶段时被 WinBox 提前终止。
PATCH_SCRIPT_ELEVATED_TIMEOUT_MS = 3 * 60 * 60 * 1000


def _installer_failure_message(name, exit_code, install_dir="", output="", elevated=False):
    """把安装器非零退出码转换为用户可处理的失败说明。

    参数：name 是软件名称；exit_code 是安装器进程返回的退出码；install_dir 是配置的安装目录；
    output 是非提权安装器输出的末尾文本；elevated 表示 WinBox 是否已成功通过 UAC 启动
    安装器。返回值为可直接展示在更新页面的失败消息。
    副作用：无。安装器退出码由不同厂商定义，只有 Windows 已有明确含义的代码才说明具体原因；
    其他代码会保留原始数值，避免把厂商私有代码误解释为系统错误。
    """
    try:
        exit_code = int(exit_code)
    except (TypeError, ValueError):
        exit_code = -1

    if exit_code == 5:
        target = f"“{install_dir}”" if install_dir else "目标安装目录"
        stage = (
            f"{DISPLAY_NAME} 已成功启动管理员安装程序，失败发生在安装程序写入文件或启动附属组件时。"
            if elevated else
            "安装程序在写入文件或启动附属组件时被系统拒绝访问。"
        )
        return (
            f"{name} 安装程序被拒绝访问（退出码：5）。"
            f"{stage}"
            f"请确认 {target} 没有被设为只读、没有被其他账户占用；关闭正在运行的 {name} 及其后台进程，"
            "优先检查 Windows 安全中心或第三方安全软件的隔离/拦截记录，确认来源可信后恢复或添加允许项；"
            "再检查受控文件夹访问是否阻止了安装程序或子进程。"
            "处理后重新下载并安装。"
        )

    explained = explain_standard_exit_code(exit_code)
    if explained:
        reason, suggestion = explained
        return f"{name} 安装失败（安装程序退出码：{exit_code}）：{reason}。{suggestion}"

    details = f"{name} 安装失败（安装程序退出码：{exit_code}）"
    output = str(output or "").strip()
    if output:
        details += f"：{output[-1000:]}"
    return details + "。请检查安装包是否完整、目标目录是否可写，然后重试。"


def _permission_failure_message(target, exc):
    """把复制时的 PermissionError 转成准确、可操作的失败提示。

    参数：
        target: 目标目录或软件名称，用于提示上下文。
        exc: 捕获到的 PermissionError。
    返回值：包含被占用文件名和持有进程的提示文本。
    副作用：无；只查询进程和拼接文本，不修改任何状态。

    注意：复制失败时 PermissionError 通常是"文件被其它进程作为可执行映像映射"
    （例如 Proxifier 把引擎 DLL 注入到联网程序），而不是真的权限不足。这里把
    持有进程查出来提示用户关闭，避免误导用户去提权运行 WinBox。
    """

    filename = str(getattr(exc, "filename", "") or "")
    if filename:
        file_name = Path(filename).name
        holders = find_processes_holding_file(filename)
        if holders:
            if all(h.get("own_image") for h in holders):
                return (
                    f"{target}: 复制失败：{file_name} 无法覆盖，"
                    f"因为该软件正在运行（{group_process_holders(holders)}）。\n"
                    "请先关闭该软件（包括系统托盘中的图标或后台服务）后重试。"
                )
            return (
                f"{target}: 复制失败：{file_name} 被以下进程占用"
                f"（可能是软件运行时注入到这些程序的网络引擎钩子）："
                f"{group_process_holders(holders)}。\n"
                "请关闭这些程序后重试；若已关闭仍失败，请重启电脑后再更新。"
            )
        return (
            f"{target}: 复制失败：{file_name} 无法写入，可能被其它程序占用。\n"
            "如果该软件正在运行，请先关闭它（包括系统托盘中的图标）后重试；"
            f"若确认是权限问题，可尝试以管理员身份运行 {DISPLAY_NAME}。"
        )
    return (
        f"{target}: 复制失败：文件被其它进程占用或权限不足，"
        f"请关闭相关程序后重试，或尝试以管理员身份运行 {DISPLAY_NAME}。"
    )


def _exit_code_details(exit_code):
    """返回标准退出码可展示的解释，未知代码保留为不可解释提示。

    参数：exit_code 是外部脚本或 UAC 宿主的退出码。返回值为带原始数字的说明文本。
    副作用：无。用于自定义脚本等没有输出错误详情的路径，避免界面只显示退出码。
    """
    explained = explain_standard_exit_code(exit_code)
    if explained:
        reason, suggestion = explained
        return f"退出码：{exit_code}（{reason}）；{suggestion}"
    return (
        f"退出码：{exit_code}（该代码由脚本或安装器自行定义，未提供可解释的错误详情）。"
        "请查看脚本窗口、安装器日志或失败日志中的原始输出。"
    )


def _custom_script_failure_details(exit_code, workspace: Path, script: Path):
    """返回自定义更新脚本的真实失败信息。

    参数 exit_code 是脚本退出码；workspace 是本次更新的独立工作目录；script 是实际执行的
    脚本路径。返回值是可展示给用户的失败详情。副作用：只读取脚本专属说明文件或 Windows
    补丁日志。普通脚本不能读取 ``WinBox_WindowsPatch.log``，否则 VMware 等失败会被错误
    标记成 Windows 补丁失败。
    """

    note = _custom_script_success_note(workspace)
    if note:
        # 脚本已写出具体失败原因时，以该说明为主，避免 1603 等通用退出码误导用户去卸载旧版本。
        return f"退出码：{exit_code}；{note}"
    from user_scripts.update.diagnostic_paths import diagnostic_log_path
    output_log = diagnostic_log_path(workspace, "elevated-script.log")
    if output_log.is_file():
        try:
            with output_log.open("rb") as file:
                file.seek(max(0, output_log.stat().st_size - 12000))
                output = file.read().decode("utf-8", errors="replace").strip()
            import json
            for line in reversed(output.splitlines()):
                try:
                    payload = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if isinstance(payload, dict) and payload.get("error"):
                    return f"退出码：{exit_code}；{payload['error']}；脚本输出日志：{output_log}"
            if output:
                return f"退出码：{exit_code}；{output[-1600:]}；脚本输出日志：{output_log}"
        except OSError:
            pass
    if script.name != "安装Windows补丁.py":
        return _exit_code_details(exit_code)

    from user_scripts.update.diagnostic_paths import diagnostic_log_path
    log_path = diagnostic_log_path(workspace, "WinBox_WindowsPatch.log")
    if not log_path.is_file():
        return _exit_code_details(exit_code)
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return f"{_exit_code_details(exit_code)}；日志：{log_path}"
    useful = [line.strip() for line in lines if line.strip()]
    tail = "；".join(useful[-6:])
    return f"{_exit_code_details(exit_code)}；日志：{log_path}" + (f"；{tail}" if tail else "")


def _custom_script_success_note(workspace: Path) -> str:
    """读取自定义脚本写入工作目录的成功补充说明。

    参数 workspace 是本次更新可写工作目录。返回值为最多 1000 个字符的说明文本；没有
    说明或无法读取时返回空字符串。副作用：只读 ``winbox-update-note.txt``，不会影响
    脚本的成功退出码。该约定用于“主程序成功、可选附属资源跳过”的更新结果提示。
    """

    note_path = Path(workspace) / "winbox-update-note.txt"
    return _read_update_note(note_path)


def _read_update_note(note_path: Path) -> str:
    """读取更新脚本写入的补充说明，并兼容 UTF-8 与中文 BAT 的 GBK 编码。

    参数：note_path 是本次更新工作目录内的 ``winbox-update-note.txt``。
    返回值：最多 1000 个字符的说明文本；文件不存在或无法读取时返回空字符串。
    副作用：只读取文件。PowerShell/Python 脚本通常写 UTF-8，使用 ``chcp 936`` 的 BAT
    会按 GBK 写入；必须按顺序尝试解码，不能让失败原因在界面显示为乱码。
    """
    try:
        raw = note_path.read_bytes()
    except OSError:
        return ""
    # PowerShell 的 -Encoding UTF8 可能写 BOM；中文 BAT 在 chcp 936 下使用 GBK。
    for encoding in ("utf-8-sig", "gbk"):
        try:
            return raw.decode(encoding).strip()[-1000:]
        except UnicodeDecodeError:
            continue
    # 文件已损坏时仍给出可见内容，不能因读取说明失败覆盖原更新结果。
    return raw.decode("utf-8", errors="replace").strip()[-1000:]

def apply_extra_copy_rules(config, target_dir):
    r"""根据 extra_copy_rules 复制额外文件到目标目录。

    extra_copy_rules 格式：一行一条 "源路径 => 目标路径"
    目标路径可以是：
      - 可移植路径（如 {USER_HOME}\Desktop），会自动解析为真实路径
      - 绝对路径（如 C:\Users\XXX\Desktop），直接使用
      - 相对路径（如 my_folder），拼接到 target_dir 下（兼容旧配置）

    安全限制：target_dir 必须是已存在的目录，否则直接跳过。
    这防止了在核心策略跳过某目标（如用户取消创建）时，
    copy2 意外把目标路径创建为文件而非目录。

    返回：警告消息列表（空列表表示全部正常执行）。
    """
    warnings: list = []
    if not config.get("copy_extra_files"):
        return warnings
    target_dir = Path(str(target_dir)) if target_dir else None
    if not target_dir or not target_dir.is_dir():
        return warnings
    rules = config.get("extra_copy_rules", [])
    if not rules:
        return warnings
    auto_create = config.get("extra_copy_auto_create", True)
    configured_targets = {
        str(resolve_portable_path(value)).casefold()
        for value in config.get("target_dirs", [])
        if str(value or "").strip()
    }
    skipped_targets = {str(value).casefold() for value in config.get("_create_skip_dirs", [])}
    for rule in rules:
        if "=>" not in rule:
            continue
        source_raw, target_raw = rule.split("=>", 1)
        source_path = Path(str(source_raw).strip())
        target_str = str(target_raw).strip()
        if not source_path.exists() or not target_str:
            continue
        # 先解析可移植令牌
        resolved_target = resolve_portable_path(target_str)
        # 绝对路径直接使用，否则拼接到 target_dir 下
        if resolved_target.is_absolute():
            dest = resolved_target
        else:
            dest = Path(target_dir) / target_str
        dest_key = str(dest).casefold()
        # 如果额外复制目标正好是更新目标目录或用户本次跳过的目标，且它不是目录，
        # 说明核心策略没有成功创建该目标，不能让 copy2 把目标目录路径创建成文件。
        if dest_key in configured_targets and not dest.is_dir():
            continue
        if dest_key in skipped_targets and not dest.is_dir():
            continue
        # 常见误配：把目录路径写成无后缀绝对路径，但目录本次没有创建成功。
        # 这种情况下 copy2 会生成一个同名空文件，必须跳过并让用户先创建目录。
        if not source_path.is_dir() and not dest.exists() and not dest.suffix:
            continue
        # 目标目录不存在时的处理
        if not dest.exists() and not auto_create:
            warnings.append(f"目标目录 {dest} 不存在，已跳过复制（extra_copy_auto_create=False）")
            continue
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            if source_path.is_dir():
                copytree(source_path, dest, dirs_exist_ok=True)
            else:
                copy2(source_path, dest)
        except Exception:
            pass
    return warnings


def apply_rename_rules(config, package_path=None):
    """更新后重命名指定文件。

    读取 config 中的 rename_enabled 和 rename_rules 字段。
    rename_rules 是 [{"source": "...", "name": "..."}, ...] 格式的列表。
    每条规则把 source 文件重命名为同目录下的 name。

    源文件不存在时跳过该条规则并记录警告，不中断管线。
    改名操作在防火墙规则之前执行，确保防火墙规则指向最终文件名。

    返回：警告消息列表（空列表表示全部正常执行）。
    """
    warnings: list = []
    if not config.get("rename_enabled"):
        return warnings
    rules = config.get("rename_rules", [])
    if not rules:
        return warnings
    for rule in rules:
        raw_source = str(rule.get("source", "") or "").strip()
        new_name = str(rule.get("name", "") or "").strip()
        if not raw_source or not new_name:
            continue
        source_path = resolve_portable_path(resolve_rename_source(raw_source, package_path))
        if not source_path.exists():
            warnings.append(f"重命名失败：文件不存在 {source_path}")
            continue
        dest_path = source_path.with_name(new_name)
        # 源和目标同名时跳过。不能调用 samefile()，因为首次更新时目标文件
        # 还不存在，samefile() 会因此抛出 FileNotFoundError。
        if source_path == dest_path:
            continue
        # 如果目标文件已存在，先删除旧文件再重命名，实现替换效果
        if dest_path.exists():
            try:
                dest_path.unlink()
            except (OSError, PermissionError) as exc:
                warnings.append(f"重命名失败：无法删除已存在的目标文件 {dest_path}：{exc}")
                continue
        try:
            source_path.rename(dest_path)
        except (OSError, PermissionError) as exc:
            warnings.append(f"重命名失败 {source_path} → {new_name}：{exc}")
    return warnings


def create_desktop_shortcut(config):
    """根据配置在桌面创建快捷方式。

    读取 config 中的 create_shortcut 和 shortcut_exe 字段。
    如果 create_shortcut 为 True 且 shortcut_exe 不为空，
    则在用户桌面目录创建同名 .lnk 快捷方式。
    如果快捷方式已存在则跳过，不重复创建。

    此函数不创建任何弹窗，可在工作线程安全调用。
    """
    if not config.get("create_shortcut"):
        return
    target = config.get("shortcut_exe", "").strip()
    if not target:
        return
    resolved_target = resolve_portable_path(target)
    target_path = Path(resolved_target)
    if not target_path.exists():
        return

    desktop = Path.home() / "Desktop"
    if not desktop.is_dir():
        return

    # 自定义快捷方式名称：用户指定则使用指定名称，否则用 exe 文件名
    shortcut_name = config.get("shortcut_name", "").strip()
    if not shortcut_name:
        shortcut_name = target_path.stem

    link_path = desktop / f"{shortcut_name}.lnk"
    if link_path.exists():
        return

    try:
        # 使用 PowerShell 创建快捷方式
        ps_cmd = (
            f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut("
            f"'{link_path}');"
            f"$s.TargetPath='{target_path}';"
            f"$s.WorkingDirectory='{target_path.parent}';"
            f"$s.Save()"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
            capture_output=True, timeout=15,
            creationflags=subprocess_creationflags(),
        )
    except Exception:
        pass


# ======================================================================
# 更新策略执行器
# ======================================================================

class StrategyExecutor:
    """执行具体的更新策略。"""

    @staticmethod
    def execute(strategy, update_config, download_path, target_dir, software_name, software_id="", parent=None):
        """根据策略执行更新操作。

        download_only 直接返回成功；其余策略走统一更新管线。
        """
        if strategy == "download_only":
            message = f"{software_name} 已下载到 {download_path}"
            write_update_log(update_config, message)
            return True, message
        write_update_log(update_config, f"开始执行更新管线：策略={strategy}")
        success, message = StrategyExecutor._execute_update_pipeline(
            strategy, update_config, download_path, software_name, software_id, parent
        )
        write_update_log(update_config, f"更新管线结果：{'成功' if success else '失败'}；{message}")
        return success, message

    @staticmethod
    def _execute_update_pipeline(strategy, config, download_path, name, software_id="", parent=None):
        """统一执行管线（所有非 download_only 策略共享）。

        管线步骤：
          PRE:  验证签名 → 关闭进程 → 备份配置 → 更新前删除(非解压类)
          CORE: 策略核心逻辑（extract_copy 在解压成功后内部执行 pre-delete）
          POST: 更新后删除(逐目标) → 额外复制(逐目标) → 重命名文件 → 生成快捷方式 → 添加防火墙规则 → 恢复备份
          （清理下载包由 UpdateWorker 在后置脚本成功后再执行，确保收尾失败时保留安装包）

        重要顺序保证：
        - 备份在任何破坏性操作之前执行
        - extract_copy 的 pre-delete / delete_old_dir 在确认解压成功后执行
        - 核心执行失败时恢复备份，不会静默丢弃
        """
        try:
            validate_rename_sources(config, download_path)
        except ValueError as exc:
            return False, str(exc)
        # ========== PRE ==========
        if config.get("verify_signature"):
            write_update_log(config, "预处理：验证下载包数字签名")
            with timed_update_step(config, "数字签名验证"):
                ok, msg = StrategyExecutor._verify_signature(download_path, config, name)
            if not ok:
                return False, msg

        if config.get("_process_preflight_completed"):
            # 页面主线程已完成用户确认、关闭和重检。worker 不得重复结束进程，
            # 否则同一轮更新会再次触发 taskkill 或 UAC。
            write_update_log(config, "预处理：进程已由主线程预检关闭并重检，跳过重复关闭")
        else:
            write_update_log(config, "预处理：检查并关闭配置的运行进程")
            with timed_update_step(config, "兼容调用方进程关闭"):
                kill_configured_processes(config)
                # 兼容没有经页面预检的调用方：普通权限失败时仍可提权重试。
                kill_configured_processes_elevated(config)

        targets = StrategyExecutor._resolve_pipeline_targets(strategy, config)

        # 备份配置（在任何破坏性操作之前执行，确保更新失败时可恢复）
        write_update_log(config, "预处理：备份用户保留的配置路径")
        with timed_update_step(config, "配置备份"):
            backup_context = backup_keep_paths(config)

        # 更新前删除：非解压类策略在此执行（下载包已验证且确认可用）
        # 解压类策略（extract_copy）在 _extract_copy 内部解压成功后执行，
        # 避免解压失败时已删除文件导致数据丢失
        if strategy != "extract_copy":
            with timed_update_step(config, "更新前删除"):
                for target in targets:
                    ok, msg = apply_delete_paths(config, target, "pre")
                    if not ok:
                        return False, msg

        # ========== CORE ==========
        write_update_log(config, f"核心步骤：执行 {strategy}")
        with timed_update_step(config, f"核心策略 {strategy}"):
            success, message = StrategyExecutor._run_core_strategy(
                strategy, config, download_path, name, parent, targets
            )
        if not success:
            # 核心执行失败：恢复备份再清理，避免数据丢失
            # 注意：此 return 在 try 之前，finally 不会执行，需要手动处理备份
            if backup_context:
                restore_keep_paths(backup_context)
            cleanup_backup_context(backup_context)
            return False, message

        # ========== POST ==========
        write_update_log(config, "后处理：执行删除、额外复制、快捷方式与防火墙规则")
        restored_backup = False
        post_messages = []
        try:
            with timed_update_step(config, "更新后删除与额外复制"):
                for target in targets:
                    ok, msg = apply_delete_paths(config, target, "post")
                    if not ok:
                        return False, msg
                    if config.get("copy_extra_files"):
                        copy_warnings = apply_extra_copy_rules(config, target)
                        if copy_warnings:
                            post_messages.extend(copy_warnings)

            with timed_update_step(config, "重命名与快捷方式"):
                post_messages.extend(apply_rename_rules(config, download_path))
                create_desktop_shortcut(config)

            with timed_update_step(config, "LocalGPO 防火墙"):
                firewall_success, firewall_messages = apply_firewall_rules(config, targets, software_id)
            post_messages.extend(firewall_messages)
            if not firewall_success:
                detail = "；".join(firewall_messages) or "防火墙规则创建失败"
                write_update_log(config, f"后处理失败：LocalGPO 防火墙规则未完成；{detail}")
                return False, f"{message}；程序文件已更新，但防火墙规则未完成：{detail}"

            if backup_context:
                with timed_update_step(config, "配置恢复"):
                    ok, msg = restore_keep_paths(backup_context)
                restored_backup = True
                if not ok:
                    return False, msg

            if post_messages:
                message += " | " + "；".join(post_messages)
            return True, message
        finally:
            if backup_context and not restored_backup:
                restore_keep_paths(backup_context)
            cleanup_backup_context(backup_context)

    @staticmethod
    def _resolve_pipeline_targets(strategy, config):
        """解析管线中需要的目标目录列表（用于 pre/post-delete / extra-copy 逐目标处理）。"""
        targets = []
        if strategy in {"extract_copy", "multi_target_copy"}:
            for value in config.get("target_dirs", []):
                text = str(value or "").strip()
                if text:
                    targets.append(resolve_portable_path(text))
        elif strategy == "run_installer":
            install_dir = config.get("install_dir", "")
            if install_dir:
                targets.append(resolve_portable_path(install_dir))
        elif strategy == "custom_script":
            target_dir = config.get("target_dir", "")
            if target_dir:
                targets.append(resolve_portable_path(target_dir))
        return targets

    @staticmethod
    def _run_core_strategy(strategy, config, download_path, name, parent=None, targets=None):
        """策略核心逻辑（差异化部分，不包含 PRE/POST 公共步骤）。"""
        return UpdateStrategyDispatcher.run(
            StrategyExecutor,
            strategy,
            config,
            download_path,
            name,
            parent,
            targets,
        )

    @staticmethod
    def _run_installer(download_path, config, name, parent=None):
        args = StrategyExecutor._expand_update_text(config.get("installer_args", ""), config, download_path, None, name)
        require_admin = config.get("require_admin", False)
        install_dir_value = str(config.get("install_dir", "")).strip()
        install_dir = str(resolve_portable_path(install_dir_value)) if install_dir_value else ""
        write_update_log(
            config,
            f"安装器：{download_path}；提权={'是' if require_admin else '否'}；参数：{args or '(无)'}",
        )

        if require_admin:
            success, exit_code = run_elevated(
                str(download_path), args
            )
            if not success:
                write_update_log(config, f"安装器提权启动失败：{_exit_code_details(exit_code)}")
                return False, f"{name} 提权启动安装程序失败（{_exit_code_details(exit_code)}）"
            write_update_log(config, f"安装器退出码：{exit_code}")
            if exit_code == 0:
                return True, f"{name} 安装完成"
            if exit_code in (3010, 1641):
                return True, f"{name} 安装完成，需要重启 Windows 后才能完全生效"
            return False, _installer_failure_message(name, exit_code, install_dir, elevated=True)

        try:
            cmd = [str(download_path)]
            if args:
                cmd.extend(shlex.split(args, posix=False))
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600,
                creationflags=subprocess_creationflags(),
            )
            write_update_log(config, f"安装器退出码：{result.returncode}")
            if result.stderr or result.stdout:
                write_update_log(config, f"安装器输出：{(result.stderr or result.stdout).strip()[-2000:]}")
            if result.returncode == 0:
                return True, f"{name} 安装完成"
            if result.returncode in (3010, 1641):
                return True, f"{name} 安装完成，需要重启 Windows 后才能完全生效"
            return False, _installer_failure_message(
                name,
                result.returncode,
                install_dir,
                result.stderr or result.stdout,
            )
        except subprocess.TimeoutExpired:
            return False, f"{name} 安装超时"
        except (OSError, subprocess.SubprocessError) as exc:
            return False, f"{name} 安装异常：{exc}"

    @staticmethod
    def _extract_copy(download_path, config, name, parent=None, targets=None):
        """解压后复制到目录（合并版，支持 1 个或多个目标）。

        PRE（签名验证、进程关闭、备份）由管线统一处理。
        pre-delete 在此方法内部解压成功后执行，确保压缩包可用后再删除旧文件。
        此方法负责：解压 -> 更新前删除 -> 逐目标处理（弹窗确认创建、清空旧目录、复制）。
        """
        if not targets:
            return False, "未指定目标目录"
        safe_base = download_path.stem if download_path.stem else "extracted"
        workspace = ensure_update_workspace(config, name)
        temp_dir = workspace / f"__extract_{safe_base}__"
        try:
            with timed_update_step(config, "压缩包临时解压"):
                extracted = extract_archive(download_path, temp_dir)
            if not extracted:
                suffix = download_path.suffix.lower()
                if suffix not in (".zip", ".tar", ".gz", ".tgz") and not resolve_7zip_executable():
                    return False, required_7zip_message(name)
                return False, f"{name} 解压失败"

            # 解压后检查所有文件是否可读（杀毒软件可能在解压后拦截隔离 exe/dll）
            with timed_update_step(config, "解压文件可读性检查"):
                ok, msg = verify_extracted_files_readable(temp_dir)
            if not ok:
                rmtree(temp_dir, ignore_errors=True)
                return False, msg

            # 解压成功后执行更新前删除（此时压缩包已验证可用）。
            # 先在 pre-delete 前记录所有 target 的存在状态，用于判断哪些被主动清除。
            target_existed_before = {}
            for target in targets:
                target_existed_before[str(target)] = target.exists()
            with timed_update_step(config, "解压策略更新前删除"):
                for target in targets:
                    ok, msg = apply_delete_paths(config, target, "pre")
                    if not ok:
                        return False, msg

            success_count = 0
            errors = []
            # 预检阶段用户取消或超时跳过的目录大小写归一后在本策略同样生效。
            skip_key = {str(value).casefold() for value in config.get("_create_skip_dirs", [])}
            for target in targets:
                target_str = str(target)
                if target_str.casefold() in skip_key:
                    errors.append(f"{target}: 用户已取消创建，已跳过")
                    continue
                if not target.exists():
                    if target_existed_before.get(target_str):
                        # 该目录被更新前删除策略主动清除（用户有意删除后重新解压），直接重建
                        pass
                    elif not StrategyExecutor._confirm_create_target(target, parent, config):
                        errors.append(f"{target}: 目标目录不存在且未勾选自动创建，已跳过")
                        continue
                elif not target.is_dir():
                    # 存在但不是目录（如零字节文件），delete_old_dir=True 时先删除再创建
                    if config.get("delete_old_dir"):
                        try:
                            target.unlink()
                        except OSError as exc:
                            errors.append(f"{target}: 存在非目录文件且无法删除：{exc}")
                            continue
                    else:
                        errors.append(f"{target}: 该路径已存在且不是目录，无法继续（可勾选\"删除旧目录\"自动清理）")
                        continue
                # delete_old_dir 只对已有目录有效
                if config.get("delete_old_dir") and target.is_dir():
                    rmtree(target)
                target.mkdir(parents=True, exist_ok=True)
                try:
                    with timed_update_step(config, f"复制到目标 {target}"):
                        copy_extracted_contents(temp_dir, target, config.get("extract_mode"))
                    success_count += 1
                except PermissionError as exc:
                    errors.append(_permission_failure_message(target, exc))
                except (OSError, shutil.Error) as exc:
                    errors.append(f"{target}: {exc}")

            rmtree(temp_dir, ignore_errors=True)
            if success_count > 0:
                msg = f"{name} 已复制到 {success_count}/{len(targets)} 个位置"
                if errors:
                    msg += "；" + "；".join(errors)
                return True, msg
            return False, f"{name} 复制失败：{'；'.join(errors)}"
        except PermissionError as exc:
            return False, f"{name} 复制失败：{_permission_failure_message(name, exc)}"
        except (OSError, shutil.Error) as exc:
            return False, f"{name} 复制失败：{exc}"
        finally:
            rmtree(temp_dir, ignore_errors=True)

    @staticmethod
    def _confirm_create_target(target_path, parent=None, config=None):
        """返回是否应该创建目标目录，支持 auto_create_dirs 和 _create_skip_dirs 配置。

        如果 config 中 auto_create_dirs=True，且目标不在 _create_skip_dirs 中，则直接返回 True。
        此执行器不创建 UI。需要用户决定时，页面层必须先确认并把
        auto_create_dirs 或 _create_skip_dirs 写入 config。
        实际创建目录的操作由调用方统一处理。
        """
        if config and str(target_path) in config.get("_create_skip_dirs", []):
            return False
        if config and config.get("auto_create_dirs"):
            return True
        return False

    @staticmethod
    def _multi_target_copy(download_path, config, name, parent=None):
        targets = [str(value).strip() for value in config.get("target_dirs", []) if str(value).strip()]
        if not targets:
            return False, "未指定目标位置列表"
        # 预检阶段用户取消或超时跳过的目录必须在此生效，避免跳过确认后又创建目录。
        # 大小写归一化比较，与预检写入的 str(Path) 保持兼容。
        skip_key = {str(value).casefold() for value in config.get("_create_skip_dirs", [])}
        success_count = 0
        errors = []
        for target in targets:
            target_path = StrategyExecutor._resolve_file_copy_target(target, download_path)
            # 目标可能写成纯目录（复制到目录内保留原名）或完整文件路径：
            # 跳过列表里可能是目录本身，也可能是该目录下的文件路径，两种都视为已跳过。
            if any(key in skip_key for key in (str(target_path).casefold(), str(target_path.parent).casefold())):
                errors.append(f"{target}: 用户已取消创建，已跳过")
                continue
            try:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                copy2(download_path, target_path)
                success_count += 1
            except PermissionError:
                errors.append(f"{target}: 权限不足，请以管理员身份运行 {DISPLAY_NAME}")
            except (OSError, shutil.Error) as exc:
                errors.append(f"{target}: {exc}")
        if success_count > 0:
            msg = f"{name} 已复制到 {success_count}/{len(targets)} 个位置"
            if errors:
                msg += "；" + "；".join(errors)
            return True, msg
        return False, f"{name} 复制失败：{'；'.join(errors)}"

    @staticmethod
    def _resolve_file_copy_target(raw_target, download_path):
        """把“下载文件复制到指定位置”的目标行解析成最终文件路径。

        目标可以写目录或文件：目录会保留下载文件原名，文件路径会按用户指定文件名覆盖。
        不存在且没有扩展名的路径按目录处理，避免用户填写 `C:\tools` 时被当成无扩展名文件。
        """

        text = str(raw_target or "").strip()
        target = resolve_portable_path(text)
        if text.endswith(("\\", "/")) or (target.exists() and target.is_dir()) or not target.suffix:
            return target / Path(download_path).name
        return target

    @staticmethod
    def _run_bat_streaming(command, workspace, timeout=600, on_line=None):
        """后台运行 BAT 并流式回传标记行，窗口始终隐藏。

        参数 command 是 cmd.exe 完整命令字符串；workspace 是工作目录；timeout 是秒；
        on_line 是每行输出的回调，只接收以“[”开头的标记行（如 [update] extract done）。
        返回值是 CompletedProcess，stdout 为合并后的全部输出。超时抛
        subprocess.TimeoutExpired，由调用方按原有契约转成“自定义更新超时”。
        副作用：任何原因退出都回收子进程，不留 cmd 孤儿；输出超过 4MB 时主动终止。
        """

        try:
            preferred_encoding = locale.getpreferredencoding(False) or "utf-8"
        except Exception:
            preferred_encoding = "utf-8"
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, cwd=str(workspace),
            **background_script_options(), text=True, encoding=preferred_encoding,
            errors="replace", bufsize=1,
        )
        pending = queue.Queue()
        stopped = Event()

        def pump():
            try:
                for line in process.stdout:
                    if stopped.is_set():
                        return
                    pending.put(line)
            except (OSError, ValueError):
                pass
            finally:
                pending.put(None)

        reader = Thread(target=pump, daemon=True, name="WinBoxBatOutput")
        output_parts = []
        output_size = 0
        deadline = time.monotonic() + timeout
        eof = False
        try:
            reader.start()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, timeout)
                try:
                    item = pending.get(timeout=min(0.5, remaining))
                except queue.Empty:
                    if process.poll() is not None and eof:
                        break
                    continue
                if item is None:
                    eof = True
                    if process.poll() is not None:
                        break
                    continue
                text = str(item)
                output_parts.append(text)
                output_size += len(text)
                if output_size > 4 * 1024 * 1024:
                    raise ValueError("BAT 输出超过安全上限，拒绝继续。")
                stripped = text.strip()
                if stripped.startswith("[") and on_line is not None:
                    try:
                        on_line(stripped)
                    except Exception:
                        pass
            code = process.wait(timeout=5)
            return subprocess.CompletedProcess(command, code, "".join(output_parts), "")
        finally:
            stopped.set()
            if process.poll() is None:
                process.kill()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
            if process.stdout is not None:
                try:
                    process.stdout.close()
                except (OSError, ValueError):
                    pass
            if reader.ident is not None:
                reader.join(timeout=2)

    @staticmethod
    def _run_custom_script(download_path, config, name, parent=None):
        """运行自定义更新脚本（PRE/POST 步骤由管线统一处理）。"""
        script_path = config.get("update_script", "")
        script_type = config.get("update_script_type", "")
        require_admin = config.get("require_admin", False)
        if not script_path:
            return False, "未指定更新脚本"
        # 脚本路径允许使用 {APP_DIR} 这类可移植令牌，避免配置只能在当前电脑使用。
        script = resolve_portable_path(script_path)
        if not script.exists():
            return False, f"更新脚本不存在：{script}"

        workspace = ensure_update_workspace(config, name)
        reset_script_completion(workspace)
        write_update_log(
            config,
            f"自定义脚本：{script}；类型={script_type}；提权={'是' if require_admin else '否'}；工作目录：{workspace}",
        )
        script_arg_values = StrategyExecutor._script_arg_values(config, download_path, None, name)
        visible = script_needs_console(script)
        progress = config.get("_progress_callback")
        if callable(progress):
            progress(f"{name}：正在运行更新脚本，请等待安装器完成。")

        if require_admin:
            if script_type == "bat":
                # call 会让 cmd.exe 把带空格的 BAT 路径作为批处理文件执行，并把
                # exit /b 的返回码传回 UAC 宿主；不能直接把路径拼到 /c 后面。
                bat_command = "call " + subprocess.list2cmdline([str(script), *script_arg_values])
                success, exit_code = run_elevated(
                    "cmd.exe", f"/d /s /c {bat_command}",
                    working_directory=workspace,
                    show_window=visible
                )
            elif script_type == "python":
                # 提权运行 Python 脚本时不能直接用 sys.executable（打包后指向
                # WinBox.exe），必须用 build_python_script_command 获取正确的可执行文件。
                # 源码环境是 python.exe，打包环境是 WinBoxScriptHost.exe。
                if visible:
                    cmd_parts = build_python_script_command(script, script_arg_values)
                else:
                    from user_scripts.update.diagnostic_paths import diagnostic_log_path
                    output_log = diagnostic_log_path(workspace, "elevated-script.log")
                    cmd_parts = build_logged_python_script_command(script, script_arg_values, output_log)
                host_path = Path(cmd_parts[0])
                if not host_path.is_file():
                    return False, (
                        f"{name} 提权启动更新脚本失败：脚本宿主不存在：{host_path}；"
                        f"更新脚本：{script}"
                    )
                # run_elevated 的 args 参数是字符串。ShellExecuteExW 会自动处理引号。
                success, exit_code = run_elevated(
                    cmd_parts[0],
                    subprocess.list2cmdline(cmd_parts[1:]),
                    timeout_ms=PATCH_SCRIPT_ELEVATED_TIMEOUT_MS,
                    working_directory=workspace,
                    show_window=visible
                )
            else:
                return False, f"不支持的脚本类型：{script_type}"
            if not success:
                write_update_log(config, f"自定义脚本提权启动失败：{_exit_code_details(exit_code)}")
                return False, (
                    f"{name} 提权启动更新脚本失败（{_exit_code_details(exit_code)}）；"
                    f"宿主：{host_path if script_type == 'python' else 'cmd.exe'}；脚本：{script}。"
                    "请确认 UAC 未被取消，并且发布包未缺少 resources 文件夹。"
                )
            if exit_code in (0, 3010, 1641):
                write_update_log(config, f"自定义脚本退出码：{exit_code}")
                note = _custom_script_success_note(workspace)
                message = f"{name} 自定义更新完成"
                if exit_code in (3010, 1641):
                    message += "，需要重启 Windows 后才能完全生效"
                return True, message + (f"；{note}" if note else "")
            if completed_before_interrupt(exit_code, workspace):
                write_update_log(config, "脚本已确认本次更新完成，随后关闭窗口；继续完成更新后处理")
                return True, f"{name} 更新已完成，脚本窗口已关闭"
            details = _custom_script_failure_details(exit_code, workspace, script)
            write_update_log(config, f"自定义脚本退出码：{exit_code}；{details}")
            return False, f"{name} 自定义更新失败（{details}）"

        try:
            if visible:
                command = build_python_script_command(script, script_arg_values) if script_type == "python" else (
                    "cmd.exe /d /s /c call " + subprocess.list2cmdline([str(script), *script_arg_values]))
                result = run_visible_script(command, workspace, timeout=600)
            elif script_type == "python":
                result = subprocess.run(
                    background_script_command(build_python_script_command(script, script_arg_values)),
                    capture_output=True, text=True, timeout=600, encoding="utf-8", errors="replace",
                    cwd=str(workspace),
                    **background_script_options(),
                )
            elif script_type == "bat":
                # 无交互 BAT 保持后台流式输出；需要 choice/set /p 的脚本已走可见控制台。
                # call 处理带空格路径并保留 BAT 的真实退出码；工作目录由 WinBox
                # 提供，脚本中的相对临时文件不会写入 Program Files 安装目录。
                # cmd.exe 必须接收完整原始命令字符串。若把 call 子命令拆成参数列表，
                # subprocess 会把脚本路径的引号转义成 \"，cmd 会把它当作文件名字符。
                bat_command = "cmd.exe /d /s /c call " + subprocess.list2cmdline([str(script), *script_arg_values])
                result = StrategyExecutor._run_bat_streaming(
                    bat_command, workspace, timeout=600,
                    on_line=config.get("_progress_callback"),
                )
            else:
                return False, f"不支持的脚本类型：{script_type}"
            if result.returncode in (0, 3010, 1641):
                write_update_log(config, f"自定义脚本退出码：{result.returncode}")
                note = _custom_script_success_note(workspace)
                message = f"{name} 自定义更新完成"
                if result.returncode in (3010, 1641):
                    message += "，需要重启 Windows 后才能完全生效"
                return True, message + (f"；{note}" if note else "")
            if completed_before_interrupt(result.returncode, workspace):
                write_update_log(config, "脚本已确认本次更新完成，随后关闭窗口；继续完成更新后处理")
                return True, f"{name} 更新已完成，脚本窗口已关闭"
            message = result.stderr or result.stdout if hasattr(result, "stderr") else ""
            details = _exit_code_details(result.returncode)
            write_update_log(config, f"自定义脚本退出码：{result.returncode}；{details}")
            if message:
                write_update_log(config, f"自定义脚本输出：{message.strip()[-2000:]}")
            if message:
                return False, f"{name} 自定义更新失败（{details}）：{message[-1000:]}"
            return False, f"{name} 自定义更新失败（{details}）"
        except subprocess.TimeoutExpired:
            return False, f"{name} 自定义更新超时"
        except (OSError, subprocess.SubprocessError) as exc:
            return False, f"{name} 自定义更新异常：{exc}"

    @staticmethod
    def _expand_update_text(text, config, download_path, target_dir, name):
        """替换安装参数和脚本参数里的常用占位符。"""
        install_dir = str(resolve_portable_path(config.get("install_dir", ""))) if config.get("install_dir") else ""
        values = {
            "{DOWNLOAD_FILE}": str(download_path or ""),
            "{INSTALL_DIR}": install_dir,
            "{TARGET_DIR}": str(target_dir or config.get("target_dir", "") or ""),
            "{SOFTWARE_NAME}": str(name or ""),
        }
        result = str(text or "")
        for key, value in values.items():
            result = result.replace(key, value)
        return result

    @staticmethod
    def _script_arg_values(config, download_path, target_dir, name):
        """生成传给更新脚本/后置脚本的统一参数列表。

        cmd.exe 的批处理参数解析器不认连续多个 "" 为空参数 —— 它们会被静默
        吞掉，导致后续参数位置偏移。这里把空值替换为 .（标准 shell 占位符），
        避免传递 "" "" 这类 cmd.exe 无法正确解析的参数序列。
        """
        install_dir = str(resolve_portable_path(config.get("install_dir", ""))) if config.get("install_dir") else ""
        target_text = str(target_dir or config.get("target_dir", "") or "")
        workspace = str(config.get("workspace_dir", "") or ".")
        values = [
            str(download_path),
            install_dir or ".",
            target_text or ".",
            str(name or "."),
            workspace,
        ]
        return values

    @staticmethod
    def _script_args(config, download_path, target_dir, name):
        """返回兼容旧调用方的已引用脚本参数字符串。"""
        return subprocess.list2cmdline(
            StrategyExecutor._script_arg_values(config, download_path, target_dir, name)
        )

    @staticmethod
    def _verify_signature(download_path, config, name):
        """验证下载文件的 Authenticode 数字签名。"""
        from modules.update_services.signature_verification import verify_signature
        return verify_signature(download_path, config, name)
