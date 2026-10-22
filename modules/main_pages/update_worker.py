# update_worker.py
# 在后台线程执行单个软件的更新方案，避免运行安装包、解压或脚本时卡住 PySide6 主线程。

from __future__ import annotations

import subprocess
from pathlib import Path
import time

from PySide6.QtCore import QObject, Signal

from modules.platform_services.update_elevation import run_elevated_update as run_elevated
from modules.platform_services.runtime import background_script_command, background_script_options
from modules.platform_services.windows_restart import pending_reboot_reasons, pending_reboot_state
from modules.update_services.update_logging import create_update_log, write_update_log
from modules.update_services.update_workspace import cleanup_update_workspace, ensure_update_workspace, release_update_workspace
from scripts.python_script_host import build_python_script_command
from modules.platform_services.update_script_runtime import script_needs_console, run_visible_script


def _post_script_log_detail(log_path):
    """读取提权后置脚本写入工作目录的末尾日志。

    参数 log_path 是重定向后的 stdout/stderr 日志路径。返回值是最多 8000 个
    字符的末尾内容；日志不存在或无法读取时返回空字符串。副作用：只读文件。
    """

    try:
        return Path(log_path).read_text(encoding="utf-8", errors="replace")[-8000:].strip()
    except OSError:
        return ""


class UpdateWorker(QObject):
    """在后台线程执行单个软件的更新方案。

    主要作用：避免运行安装包、解压或脚本时卡住 PySide6 主线程。
    输入参数：row_data 是更新界面当前行数据，config_manager 用来解析可移植路径。
    返回值：通过 finished 信号返回结果。
    副作用：会按软件更新方案执行文件复制、安装包或自定义脚本等实际更新动作。
    """

    finished = Signal(str, bool, str, str, list)
    progress = Signal(str, str)

    def __init__(self, row_data, config_manager):
        super().__init__()
        self.row_data = row_data
        self.config_manager = config_manager
        from modules.error_log_bridge import connect_update_errors
        connect_update_errors(self)

    def run(self):
        worker_started_at = time.monotonic()
        update_config = None
        log_path = None
        name = ""
        try:
            sid = self.row_data.get("id", "")
            node = self.row_data.get("node", {})
            name = node.get("name", "")
            package_path = Path(self.row_data.get("package_path", ""))
            update_config = self._resolved_update_config(
                self.config_manager.normalize_update_config(node.get("update", {}))
            )
            # 进度回调只活在本次运行时配置里，不写回软件 JSON；脚本标记行由此实时进界面。
            update_config["_progress_callback"] = lambda text: self.progress.emit(sid, text)
            strategy = update_config.get("strategy", "download_only")
            is_vmware_update = self._is_vmware_update(update_config)
            reboot_state_before = pending_reboot_state() if is_vmware_update else {}
            target_dir_raw = update_config.get("target_dir", "")
            target_dir = self.config_manager.resolve_path(target_dir_raw) if target_dir_raw else None
            log_path = create_update_log(update_config, name, strategy, package_path)
            self._write_preflight_timings(update_config)
            if strategy == "custom_script" or update_config.get("run_post_script"):
                ensure_update_workspace(update_config, name)
            from modules.update_services.strategy_executor import StrategyExecutor

            success, message = StrategyExecutor.execute(
                strategy,
                update_config,
                package_path,
                target_dir,
                name,
                software_id=sid,
            )
            if success and update_config.get("run_post_script"):
                post_success, post_message = self._run_post_script(update_config, package_path)
                if not post_success:
                    success = False
                    message = f"{message}；{post_message}"
                else:
                    message = f"{message}；{post_message}"
                write_update_log(update_config, f"更新后脚本结果：{post_message}")
            restart_reasons = pending_reboot_reasons(reboot_state_before) if success and is_vmware_update else []
            if restart_reasons:
                message += "；检测到 Windows 待重启状态：" + "、".join(restart_reasons)
                write_update_log(update_config, "VMware 安装后检测到待重启状态：" + "、".join(restart_reasons))

            # 清理下载包必须等后置脚本成功后执行：后置脚本失败时保留安装包便于排查，
            # 也避免后置脚本需要读取安装包时文件已被删除。
            if (
                success
                and strategy != "download_only"
                and update_config.get("cleanup_archive")
                and package_path.is_file()
            ):
                try:
                    package_path.unlink()
                    write_update_log(update_config, "更新成功，已删除下载包。")
                except OSError as exc:
                    write_update_log(update_config, f"删除下载包失败：{exc}")
                    message += f"；删除下载包失败：{exc}"

            elapsed = time.monotonic() - worker_started_at
            write_update_log(update_config, f"安装 worker 总耗时：{elapsed:.3f} 秒")
            write_update_log(update_config, f"最终结果：{'成功' if success else '失败'}；{message}")
            if success:
                cleanup_update_workspace(update_config)
            if log_path:
                message += f"；更新日志：{log_path}"
            self.finished.emit(sid, success, message, str(log_path or ""), restart_reasons)
        except Exception as exc:
            import logging
            logging.getLogger("winbox.update").exception("Update worker failed")
            message = f"更新异常：{exc}"
            if update_config is not None:
                elapsed = time.monotonic() - worker_started_at
                write_update_log(update_config, f"安装 worker 总耗时：{elapsed:.3f} 秒")
                write_update_log(update_config, f"最终结果：失败；{message}")
            if log_path:
                message += f"；更新日志：{log_path}"
            self.finished.emit(self.row_data.get("id", ""), False, message, str(log_path or ""), [])
        finally:
            release_update_workspace(update_config)

    @staticmethod
    def _write_preflight_timings(update_config):
        """把主线程预检阶段暂存的耗时写入本次更新日志。

        参数 update_config 是本次运行配置。返回值：无。副作用：仅追加更新日志；
        私有耗时字段只存在内存，不会写回 software_items.json。
        """

        timing_fields = (
            ("签名后台预检", "_timing_signature_seconds"),
            ("进程与文件占用扫描（含签名预检）", "_timing_process_scan_seconds"),
            ("关闭进程并最终重检", "_timing_process_close_seconds"),
            ("全部安装预检（含用户确认）", "_timing_preflight_seconds"),
        )
        for label, key in timing_fields:
            value = update_config.get(key)
            if isinstance(value, (int, float)):
                write_update_log(update_config, f"阶段耗时：{label}={value:.3f} 秒")

    @staticmethod
    def _is_vmware_update(update_config):
        """判断本次是否使用 VMware 专用更新脚本。

        参数 update_config 是已解析路径的运行时更新配置。返回值为布尔值。
        副作用：无；仅根据脚本文件名决定是否需要检测 VC++ 等 Windows 待重启状态。
        """

        return Path(str(update_config.get("update_script", "") or "")).name == "VMware_更新.bat"

    def _resolved_update_config(self, update_config):
        """把更新配置里的可移植路径解析为真实路径，避免执行器拿到 {APP_DIR} 字符串。"""
        config = dict(update_config)
        for key in ("install_dir", "target_dir", "target_file", "update_script", "post_script", "restart_exe", "shortcut_exe"):
            value = str(config.get(key, "")).strip()
            if value:
                config[key] = str(self.config_manager.resolve_path(value))
        config["target_dirs"] = [
            str(self.config_manager.resolve_path(value)) for value in config.get("target_dirs", []) if str(value).strip()
        ]
        # Windows 补丁页面会在启动 worker 前创建独立工作目录，供进度监控、取消标记和
        # 失败日志共享。该字段仅存在于本次 row_data，不能写回软件配置 JSON。
        workspace_dir = str(self.row_data.get("_update_workspace_dir", "") or "").strip()
        if workspace_dir:
            config["workspace_dir"] = workspace_dir
        return config

    def _run_post_script(self, update_config, package_path):
        """同步运行更新后脚本，并把脚本失败返回给更新主流程。

        参数 update_config 是已解析路径的更新配置，package_path 是下载包路径。
        返回值是 (是否成功, 提示文本)。脚本获得与自定义更新脚本相同的 5 个参数，
        第 5 个参数为 WinBox 创建的可写工作目录。
        副作用：启动后置脚本；require_admin 开启时会触发 UAC。不会创建界面控件。
        """

        script_path = update_config.get("post_script", "")
        script_type = update_config.get("post_script_type", "")
        if not script_path:
            return False, "未指定更新后脚本"
        script = self.config_manager.resolve_path(script_path)
        if not script.is_file():
            return False, f"更新后脚本不存在：{script}"

        workspace = ensure_update_workspace(update_config, self.row_data.get("node", {}).get("name", ""))
        from user_scripts.update.diagnostic_paths import diagnostic_log_path
        log_path = diagnostic_log_path(workspace, "post-script.log")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        # extract_copy/multi_target_copy 的目标目录保存在 target_dirs，不在 target_dir。
        # 单目标场景把真实目录传给第 3 个参数，避免后置脚本收到 "." 后误写工作目录。
        script_target_dir = str(update_config.get("target_dir", "") or ".").strip() or "."
        if script_target_dir == ".":
            target_dirs = [str(value).strip() for value in update_config.get("target_dirs", []) if str(value).strip()]
            if len(target_dirs) == 1:
                script_target_dir = target_dirs[0]
        args = [
            str(package_path),
            str(update_config.get("install_dir", "") or "."),
            script_target_dir,
            str(self.row_data.get("node", {}).get("name", "") or "."),
            str(workspace),
        ]
        try:
            if script_needs_console(script):
                command = build_python_script_command(script, args) if script_type == "python" else (
                    "cmd.exe /d /s /c call " + subprocess.list2cmdline([str(script), *args]))
                if update_config.get("require_admin"):
                    parts = command if isinstance(command, list) else ["cmd.exe", "/d /s /c call " + subprocess.list2cmdline([str(script), *args])]
                    launched, code = run_elevated(parts[0], subprocess.list2cmdline(parts[1:]) if isinstance(command, list) else parts[1],
                                                  working_directory=workspace, show_window=True)
                    if not launched:
                        return False, f"更新后脚本授权未完成（错误码 {code}）"
                else:
                    code = run_visible_script(command, workspace, timeout=600).returncode
                return (True, "更新后脚本完成") if code == 0 else (False, f"交互更新后脚本失败（退出码 {code}），请查看脚本窗口。")
            if update_config.get("require_admin"):
                if script_type == "bat":
                    command = (
                        "call " + subprocess.list2cmdline([str(script), *args])
                        + " > " + subprocess.list2cmdline([str(log_path)]) + " 2>&1"
                    )
                    launched, exit_code = run_elevated(
                        "cmd.exe", f"/d /s /c {command}", working_directory=workspace, show_window=False
                    )
                elif script_type == "python":
                    cmd = build_python_script_command(script, args)
                    command = subprocess.list2cmdline(cmd) + " > " + subprocess.list2cmdline([str(log_path)]) + " 2>&1"
                    launched, exit_code = run_elevated(
                        "cmd.exe", f"/d /s /c {command}", working_directory=workspace, show_window=False
                    )
                else:
                    return False, f"不支持的更新后脚本类型：{script_type}"
                if not launched:
                    detail = _post_script_log_detail(log_path)
                    return False, f"更新后脚本提权启动失败（Win32 错误码：{exit_code}）{('：' + detail) if detail else ''}"
                if exit_code:
                    detail = _post_script_log_detail(log_path)
                    return False, f"更新后脚本失败（退出码：{exit_code}）{('：' + detail) if detail else ''}"
                return True, "更新后脚本完成"

            if script_type == "python":
                command = background_script_command(build_python_script_command(script, args))
            elif script_type == "bat":
                # 传入完整命令字符串，避免 subprocess 再次转义 BAT 路径上的引号。
                command = "cmd.exe /d /s /c call " + subprocess.list2cmdline([str(script), *args])
            else:
                return False, f"不支持的更新后脚本类型：{script_type}"
            result = subprocess.run(
                command, cwd=str(workspace), capture_output=True, text=True, timeout=600,
                encoding="utf-8", errors="replace",
                **background_script_options(),
            )
            if result.returncode:
                detail = (result.stderr or result.stdout or "").strip()
                return False, f"更新后脚本失败（退出码：{result.returncode}）{('：' + detail) if detail else ''}"
            return True, "更新后脚本完成"
        except subprocess.TimeoutExpired:
            return False, "更新后脚本超时"
        except (OSError, subprocess.SubprocessError) as exc:
            return False, f"更新后脚本执行异常：{exc}"
