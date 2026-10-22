"""下载任务状态回调协调器。

主要作用：处理 aria2 任务暂停、完成、失败等状态回调，把状态展示、暂停记录和多文件
下载收尾从 DownloadOperationsCoordinator 中分离出来。
输入参数：operations 是下载操作协调器实例，用于访问页面状态和复用重试/多文件方法。
返回值：回调方法不返回业务结果，直接更新页面行状态和下载状态记录。
副作用：修改页面行数据、写入暂停状态、追加信息日志；不创建 Qt 控件、不读写界面布局。
调用限制：只能通过 DownloadOperationsCoordinator 的信号入口调用，不能在页面初始化阶段主动执行。
"""

from pathlib import Path
from .download_size_cell import refresh_download_size

from .download_status import (
    STATUS_DIRECT_FAILED,
    STATUS_DONE,
    STATUS_DOWNLOAD_FAILED,
    STATUS_DOWNLOADING,
    STATUS_EXISTING_FILE,
    STATUS_NOT_CHECKED,
    STATUS_PAUSED,
    STATUS_WAITING,
)
from modules.network_policy import CHANNEL_DIRECT
from modules.main_pages.download.download_page_utils import task_network_channel
from modules.update_services.windows_update_package_metadata import (
    is_windows_monthly_update,
    write_catalog_metadata,
)


class DownloadTaskStatusCoordinator:
    """集中处理下载任务的状态变化、完成和失败回调。"""

    def __init__(self, operations):
        self._operations = operations

    @property
    def page(self):
        return self._operations._page

    def on_task_status_changed(self, task):
        """aria2 任务状态变更时更新页面状态。"""
        page = self.page
        row_data = page.row_by_id.get(task.software_id)
        if row_data is None:
            return
        from .download_debug import log as _debug_log
        _debug_log(
            f"task状态 | sid={task.software_id} task.status={task.status} "
            f"row_status={row_data.get('status')} prog={getattr(task, 'progress_percent', 0):.1f}"
        )
        refresh_download_size(page, row_data, task=task)
        current_status = row_data.get("status")
        if task.status == "active":
            row_data["progress_percent"] = getattr(task, "progress_percent", float(task.progress))
            # 限速时进度差分会受 aria2 的 TCP 缓冲批量写入影响而短暂虚高；使用 aria2
            # 返回的 downloadSpeed 才能如实展示当前任务实际受控的下载速率。
            row_data["download_speed"] = task.download_speed
            page._set_status(row_data, STATUS_DOWNLOADING, task.progress)
        elif task.status == "waiting":
            if current_status in (STATUS_DOWNLOADING, STATUS_WAITING):
                row_data["download_speed"] = 0
        elif task.status == "paused":
            if current_status != STATUS_PAUSED:
                row_data["download_speed"] = 0
                page._set_status(row_data, STATUS_PAUSED)
                task_info = {
                    "url": task.url,
                    "filename": task.filename or "",
                    "save_dir": str(task.save_dir) if task.save_dir else "",
                    "progress": task.progress_percent,
                }
                if row_data.get("_multi_downloads"):
                    task_info = self._operations._multi_state_info(task, row_data)
                page.state_manager.save_paused(task.software_id, task_info)

    def on_task_completed(self, task):
        """处理单文件或多文件下载完成。"""
        page = self.page
        row_data = page.row_by_id.get(task.software_id)
        if row_data:
            refresh_download_size(page, row_data, task=task)
            from .download_debug import log as _debug_log
            _debug_log(
                f"task完成 | sid={task.software_id} status={task.status} "
                f"multi={bool(row_data.get('_multi_downloads'))} "
                f"file={self._completed_task_path(task)}"
            )
            page._download_tasks.pop(task.software_id, None)
            name = row_data["node"].get("name", "")
            if row_data.get("_multi_downloads"):
                download_name = row_data.get("_multi_download_name", "")
                complete_path = self._completed_task_path(task)
                if complete_path is None:
                    detail = "aria2 报告完成，但目标文件不存在或仍有 .aria2 控制文件"
                    # 先删除旧活动记录，再让补偿提交写入新记录；如果反过来删除，会把
                    # 新任务刚保存的断点续传状态一并清空。
                    page.state_manager.remove(task.software_id)
                    if self._operations.retry_missing_completed_multi_file(task, row_data):
                        return
                    page._append_info(
                        f"{name}（{download_name}）：{detail}，已按失败处理。",
                        level="error",
                    )
                    self._operations._record_multi_result(row_data, "failed", download_name, detail)
                    self._operations._advance_multi_download(task.software_id, row_data)
                    return
                page._append_info(f"{name}（{download_name}）：下载完成 -> {task.save_dir}", level="success")
                completed = row_data.setdefault("_multi_completed_filenames", [])
                if task.filename and task.filename not in completed:
                    completed.append(task.filename)
                # 先清理当前子任务的旧状态，再让 _advance 写入下一个子任务状态。
                # 不能在 _advance 之后 remove，否则会把刚写入的后续任务一起删除。
                page.state_manager.remove(task.software_id)
                self._operations._record_multi_result(row_data, "completed", download_name)
                self._operations._advance_multi_download(task.software_id, row_data)
                return
            row_data["progress_percent"] = 100.0
            row_data["download_speed"] = 0
            if self._completed_task_path(task) is None:
                page._set_status(row_data, STATUS_DOWNLOAD_FAILED)
                page._append_info(
                    f"{name}：aria2 报告完成，但目标文件不存在或仍有 .aria2 控制文件。",
                    level="error",
                )
                page.state_manager.remove(task.software_id)
                return
            # 只有 aria2 确认文件完整后才写入 Windows 更新归属清单，避免中断的
            # 下载或目录中残留的旧补丁被更新页当成已验证包。
            if is_windows_monthly_update(row_data.get("node", {})) and task.save_dir and task.filename:
                package_path = Path(task.save_dir) / task.filename
                write_catalog_metadata(
                    package_path,
                    page._check_results.get(task.software_id, {}),
                )
            page._set_status(row_data, STATUS_DONE)
            page._append_info(f"{name}：下载完成 -> {task.save_dir}", level="success")
            page._append_info("软件下载页面只负责下载，不会自动安装。后续请在更新页面执行更新。", level="warning")
        page.state_manager.remove(task.software_id)

    @staticmethod
    def _completed_task_path(task):
        """确认 aria2 完成事件对应的目标文件确实已落盘。

        参数 task 是 aria2 报告 complete 的 DownloadTask。返回值为完整文件 Path；文件名
        缺失、文件不存在或同名 ``.aria2`` 控制文件仍存在时返回 None。副作用：只读文件
        系统。不能只相信 RPC 状态，否则多文件下载可能把缺失的附属文件误标为完成。
        """

        if not task.save_dir or not task.filename:
            return None
        path = Path(task.save_dir) / task.filename
        if not path.is_file():
            return None
        control_path = path.with_suffix(path.suffix + ".aria2")
        return path if not control_path.exists() else None

    def on_task_failed(self, task):
        """处理下载失败、已有文件和多文件跳过。"""
        page = self.page
        row_data = page.row_by_id.get(task.software_id)
        if row_data:
            diagnostic = getattr(task, "engine_diagnostic", "")
            page._append_info(
                f"aria2 gid={task.gid} code={task.error_code} "
                f"received={task.completed_length}/{task.total_length}\n"
                + (diagnostic or "[no matching engine output captured]"),
                level="error",
            )
            page._download_tasks[task.software_id] = task
            row_data["progress_percent"] = getattr(task, "progress_percent", 0.0)
            row_data["download_speed"] = 0

            if task.error_message.startswith(("下载引擎连接中断：", "下载引擎暂时无响应：")):
                # 引擎进程/RPC 已失效，换下载代理没有意义；直接结束整个下载项，等待用户
                # 重新点击下载后由页面走完整的 aria2 启动流程。
                row_data.pop("_multi_downloads", None)
                row_data.pop("_multi_download_index", None)
                row_data.pop("_multi_download_results", None)
                row_data.pop("_multi_download_filename", None)
                row_data.pop("_multi_download_name", None)
                row_data.pop("_multi_selection_is_complete", None)
                page._set_status(row_data, STATUS_DOWNLOAD_FAILED)
                page._append_info(f"{task.software_name}：{task.error_message}", level="error")
                page.state_manager.remove(task.software_id)
                return

            if task.error_message and "control file" in task.error_message.lower() and "does not exist" in task.error_message.lower():
                target_path = Path(task.save_dir) / task.filename if task.save_dir and task.filename else None
                if target_path and target_path.exists():
                    row_data["progress_percent"] = 100.0
                    page._set_status(row_data, STATUS_EXISTING_FILE)
                    page._append_info(
                        f"{task.software_name}：检测到已经有下载好的文件，跳过重复下载 -> {target_path}",
                        level="success",
                    )
                    page.state_manager.remove(task.software_id)
                    return

            if row_data.get("_multi_downloads"):
                download_name = row_data.get("_multi_download_name", "")
                name = row_data["node"].get("name", "")
                # 多文件代理模式会在当前代理通道失败后尝试下一个代理通道。直连模式由
                # _try_next_network_channel() 明确拒绝自动切换，失败后保留给用户手动处理。
                if self._operations._try_next_network_channel(task, row_data):
                    return
                page._append_info(
                    f"{name}（{download_name}）：下载失败，跳过。原因：{task.error_message}",
                    level="error",
                )
                self._operations._record_multi_result(
                    row_data,
                    "failed",
                    download_name,
                    task.error_message,
                )
                row_data["_multi_last_failed_channel"] = task_network_channel(task)
                page.state_manager.remove(task.software_id)
                self._operations._advance_multi_download(task.software_id, row_data)
                return

            if self._operations._try_next_network_channel(task, row_data):
                return

            if task_network_channel(task) == CHANNEL_DIRECT:
                page._set_status(row_data, STATUS_DIRECT_FAILED)
                page._append_info(
                    f"{task.software_name}：直连下载失败。可打开代理开关后重新检测或下载。"
                    f"失败原因：{task.error_message}",
                    level="warning",
                )
            else:
                page._set_status(row_data, STATUS_DOWNLOAD_FAILED)
                page._append_info(f"{task.software_name}：下载失败 - {task.error_message}", level="error")
        page.state_manager.remove(task.software_id)
