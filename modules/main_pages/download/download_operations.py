"""下载操作协调器

主要作用：从 SoftwareDownloadPage 中提取下载生命周期相关的逻辑（排队、下载、
暂停、恢复、取消、多文件下载、任务状态回调、通道回退），让页面类只负责 UI 展示。

使用方式：页面创建 DownloadOperationsCoordinator(page) 实例，将页面自身传入。
协调器通过 self._page 访问页面状态和方法。

注意事项：
- 协调器不持有独立的状态副本，直接读取和修改 page 的属性（如 _download_tasks、
  _check_results、_network_generation 等）。
- 不要在协调器内创建 Qt 控件或弹窗。
- 下载任务的状态管理（_download_tasks、state_manager）完全在协调器内处理。
"""

from pathlib import Path

from PySide6.QtWidgets import QApplication

from modules.network_policy import (
    CHANNEL_DIRECT,
    configured_proxy_channels,
    aria2_options_for_channel,
    blocked_proxy_message,
)
from modules.download_services.download_paths import software_download_directory
from modules.download_services.download_file_session import DownloadFileSession
from modules.update_services.windows_update_package_metadata import (
    is_windows_monthly_update,
    write_catalog_metadata,
)
from modules.update_services.multi_download_manifest import (
    remove_multi_download_manifest,
    write_multi_download_manifest,
    write_pending_multi_download_manifest,
)
from modules.main_pages.download.download_page_utils import (
    active_network_channel,
    current_download_mode,
    is_download_mode_allowed,
    network_channels,
    task_mode,
    task_network_channel,
)
from .download_status import (
    STATUS_DIRECT_FAILED,
    STATUS_DONE,
    STATUS_DOWNLOAD_FAILED,
    STATUS_DOWNLOADING,
    STATUS_EXISTING_FILE,
    STATUS_NOT_CHECKED,
    STATUS_PAUSED,
    STATUS_PROXY_SKIPPED,
    STATUS_READY_DOWNLOAD,
)
from .download_task_status import DownloadTaskStatusCoordinator
from .multi_file_selection import choose_multi_downloads, is_multi_file_result, selected_multi_downloads


class DownloadOperationsCoordinator:
    """下载操作协调器

    负责：排队、下载、暂停、恢复、取消、多文件下载的调度和生命周期管理，
    以及 aria2 任务状态变化的回调处理。
    不负责：UI 控件创建、页面布局、列渲染。

    输入参数：
        page: SoftwareDownloadPage 实例。
    """

    def __init__(self, page):
        self._page = page
        self._file_session = DownloadFileSession(page.state_manager)
        self._task_status = DownloadTaskStatusCoordinator(self)

    # ── 队列操作 ──────────────────────────────────────────



    def copy_download_link(self, software_id):
        """复制检测出的下载链接到系统剪贴板。"""
        page = self._page
        check_result = page._check_results.get(software_id, {})
        url = check_result.get("download_url", "")
        row_data = page.row_by_id.get(software_id)
        name = row_data["node"].get("name", "") if row_data else ""
        if url:
            QApplication.clipboard().setText(url)
            page._append_info(f"{name}：下载链接已复制到剪贴板。", level="success")
        else:
            page._append_info(f"{name}：没有可用的下载链接，请先检测更新。", level="warning")

    # ── 分类批量操作 ──────────────────────────────────────

    def _rows_in_category(self, category):
        page = self._page
        return [r for r in page.rows if r.get("category") == category]

    def check_category(self, category):
        """委托检测协调器以受限并发队列检测指定分类。

        参数：category 是当前文件夹分类名称。
        返回值：无。
        副作用：检测协调器会更新行状态并启动后台脚本；这里不逐项调用 check_single，避免
        分类中多个软件同时创建独立进程造成主界面无响应。
        """
        self._page._check_coordinator.check_category(category)

    def download_category(self, category):
        """下载指定分类下所有可更新或等待中的软件。"""
        page = self._page
        for row_data in self._rows_in_category(category):
            status = row_data.get("status")
            if status == STATUS_READY_DOWNLOAD:
                sid = row_data.get("id", "")
                if sid:
                    page.download_single(sid)


    # ── 选中项批量操作 ────────────────────────────────────

    def check_selected(self, selected):
        """委托检测协调器以受限并发队列检测选中软件。

        参数：selected 是右键菜单收集的软件下载行列表。
        返回值：无。
        副作用：检测协调器会更新行状态并启动后台脚本，避免逐项提交独立检测进程。
        """
        self._page._check_coordinator.check_selected(selected)


    def download_selected(self, selected):
        """立即提交选中的可下载项，并发数量继续由下载引擎限制。"""
        for row_data in selected:
            if row_data.get("status") in (STATUS_READY_DOWNLOAD, STATUS_MANUAL_DECISION):
                self._page.download_single(row_data.get("id", ""))

    def pause_selected(self, selected):
        """暂停所有选中软件的下载任务。"""
        page = self._page
        for row_data in selected:
            sid = row_data.get("id", "")
            if sid:
                page.pause_single(sid)

    def resume_selected(self, selected):
        """恢复所有选中软件的下载任务。"""
        page = self._page
        for row_data in selected:
            sid = row_data.get("id", "")
            if sid:
                page.resume_single(sid)

    def cancel_selected(self, selected):
        """取消所有选中软件的下载任务。"""
        page = self._page
        for row_data in selected:
            sid = row_data.get("id", "")
            if sid:
                page.cancel_single(sid)

    # ── 单文件下载 ────────────────────────────────────────

    def download_single(self, software_id, automatic=False):
        """下载单个软件；automatic=True 时由倒计时触发，不重绘中间按钮状态。"""
        page = self._page
        if not automatic:
            page._auto_action_coordinator.cancel(software_id)
        row_data = page.row_by_id.get(software_id)
        if row_data is None:
            return

        from .download_debug import log as _debug_log
        cr = page._check_results.get(software_id, {})
        _debug_log(
            f"下载点击 | sid={software_id} name={row_data['node'].get('name', '')} "
            f"status={row_data.get('status')} "
            f"has_result={software_id in page._check_results} "
            f"has_url=bool(cr.get('download_url')) "
            f"result_keys={sorted(cr.keys())}"
        )

        if not page.download_service:
            page._append_info("下载引擎未配置。", level="warning")
            return
        if not network_channels(row_data, page.config_manager):
            page._append_info(
                blocked_proxy_message(row_data["node"].get("name", "这个软件")),
                level="warning",
            )
            return
        if not self._ensure_multi_file_selection(software_id, row_data):
            return

        self._do_download(software_id, row_data)

    def _ensure_multi_file_selection(self, software_id, row_data):
        """在下载或入队前确认多文件结果至少选择了一个文件。

        参数：software_id 是下载行 ID；row_data 是行状态。返回值：单文件或已有选择时为 True；
        用户取消选择弹窗时为 False。副作用：需要时显示多选弹窗并把当前选择写入 row_data，
        不保存软件配置。所有下载和队列入口都必须经过此方法，避免空选择进入 aria2。
        """

        page = self._page
        result = page._check_results.get(software_id, {})
        return choose_multi_downloads(page, row_data, result)

    def _ensure_engine_ready(self, software_id):
        """确认已有实际下载需求后再按需启动 aria2。

        参数 software_id 是等待引擎就绪后需要重新执行的下载项 ID。
        返回值为真表示可继续调用 aria2；为假表示引擎正在或即将后台启动，调用方必须
        立即返回。
        副作用：引擎未就绪时将软件 ID 加入去重等待队列；仅第一个请求会实际启动 aria2。
        """
        page = self._page
        if page.download_service.is_ready:
            return True
        pending_ids = page._pending_download_ids
        is_new_pending = software_id not in pending_ids
        if is_new_pending:
            pending_ids.append(software_id)
        if page.download_service.is_starting:
            if is_new_pending:
                page._append_info("下载引擎正在启动，就绪后会自动开始下载。", level="warning")
            return False
        # RPC 就绪后会重走 _do_download，因此已有文件仍会再次优先检查。
        if page.download_service.start_async():
            page._append_info("正在启动下载引擎...", level="warning")
        elif is_new_pending:
            # start_async() 在检查 is_starting 后抢先被其他线程调用时也不会重复启动。
            page._append_info("下载引擎正在启动，就绪后会自动开始下载。", level="warning")
        return False

    def _do_download(self, software_id, row_data):
        """下载后续步骤：检查链接、添加任务。

        支持多文件下载（脚本返回 downloads 列表时），每个已选文件依次下载。
        多文件项在 row_data 中记录 _multi_downloads（用户选择的文件列表）和
        _multi_download_index（当前下载索引），下载完一个后自动启动下一个。
        """
        page = self._page
        node = row_data["node"]
        name = node.get("name", "")
        check_result = page._check_results.get(software_id, {})
        download_url = check_result.get("download_url", "")
        all_downloads = check_result.get("downloads") or None
        downloads = all_downloads
        resuming_multi = bool(row_data.get("_multi_downloads"))

        # 多文件下载：只处理用户勾选的文件。没有选择时不允许从 download_url 回退为单文件，
        # 否则会绕过“至少选择一个文件”的交互约束。
        if is_multi_file_result(check_result):
            if resuming_multi:
                # 重启恢复时使用状态文件保存的完整批次，不能再次按当前行的临时选择重建，
                # 否则已完成文件和后续文件会丢失。
                downloads = list(row_data.get("_multi_downloads", []))
            else:
                downloads = selected_multi_downloads(row_data, check_result)
            if not downloads:
                page._append_info(f"{name}：请先选择至少一个要下载的文件。", level="warning")
                return

        # 多文件下载：记录用户选择的列表，从第 0 个开始依次下载。
        if downloads is not None and isinstance(downloads, list) and len(downloads) > 0:
            # 先等待引擎就绪，再初始化批次。否则 RPC 就绪后的重试会再次初始化并重复
            # 显示“开始下载第 1 个文件”。
            if not self._ensure_engine_ready(software_id):
                return
            if not resuming_multi:
                # 新一轮多文件下载必须丢弃上次完整清单，并立即写入未完成计划。即使程序
                # 重启，扫描器也能知道主包还依赖哪些附属文件，不能把半套文件当成可更新包。
                download_dir = self._software_download_dir(row_data)
                download_dir.mkdir(parents=True, exist_ok=True)
                # 只有完整选择脚本返回的全部文件时，才能声明主包和附属文件关系。
                complete_selection = (
                    isinstance(all_downloads, list)
                    and len(downloads) == len(all_downloads)
                    and all(item in downloads for item in all_downloads)
                )
                remove_multi_download_manifest(download_dir, all_downloads or downloads)
                if complete_selection:
                    try:
                        write_pending_multi_download_manifest(download_dir, downloads)
                    except (OSError, ValueError) as exc:
                        page._set_status(row_data, STATUS_DOWNLOAD_FAILED)
                        page._append_info(
                            f"{name}：无法写入多文件下载计划清单：{exc}",
                            level="error",
                        )
                        return
                row_data.pop("_multi_download_failure", None)
                row_data.pop("_multi_missing_artifact_retries", None)
                row_data["_multi_downloads"] = list(downloads)
                row_data["_multi_all_downloads"] = list(all_downloads or downloads)
                row_data["_multi_selection_is_complete"] = complete_selection
                row_data["_multi_download_index"] = 0
                row_data["_multi_completed_filenames"] = []
                row_data["_multi_selection_confirmed"] = True
                # 多文件批次必须分别记录成功、已有文件、缺链接和最终失败，不能只靠索引
                # 到达末尾判断“完成”。否则每个文件都失败时仍会错误显示完成状态。
                row_data["_multi_download_results"] = {
                    "completed": [],
                    "existing": [],
                    "failed": [],
                    "invalid": [],
                }
            else:
                row_data.setdefault("_multi_completed_filenames", [])
                row_data.setdefault(
                    "_multi_download_results",
                    {"completed": [], "existing": [], "failed": [], "invalid": []},
                )
            total = len(downloads)
            page._append_info(f"{name}：共 {total} 个文件，开始下载第 1 个。", level="success")
            self._start_multi_download_file(software_id, row_data)
            return

        if not download_url:
            from .download_debug import log as _debug_log
            _debug_log(
                f"_do_download 缺链接 | sid={software_id} name={name} "
                f"status={row_data.get('status')} "
                f"check_result_keys={sorted(check_result.keys())}"
            )
            page._append_info(f"{name}：" + "没有获取到下载链接。请先检测更新。", level="warning")
            return

        save_dir = self._software_download_dir(row_data)
        save_dir.mkdir(parents=True, exist_ok=True)
        filename = check_result.get("filename") or download_url.split("/")[-1]
        target_path = (save_dir / filename).resolve()
        self._report_stale_download_source_reset(software_id, download_url, target_path)

        if not (target_path.exists() and target_path.is_file()):
            # 兜底：如果精确路径查找失败，遍历目录匹配文件名
            if save_dir.is_dir():
                for f in save_dir.iterdir():
                    if f.is_file() and f.name.lower() == filename.lower():
                        target_path = f.resolve()
                        break

        if target_path.exists() and target_path.is_file():
            aria2_control = target_path.with_suffix(target_path.suffix + ".aria2")
            if aria2_control.exists():
                page._append_info(f"{name}：检测到未完成的下载，尝试断点续传 -> {target_path}", level="success")
            else:
                # 用户重新检测后确认 URL/文件名仍与 Catalog 条目一致时，可以为
                # 已存在文件补写清单；后续更新页仍会验证签名和元数据完整性。
                if is_windows_monthly_update(node):
                    write_catalog_metadata(target_path, check_result)
                row_data["progress_percent"] = 100.0
                page._set_status(row_data, STATUS_EXISTING_FILE)
                page._append_info(f"{name}：检测到已经有下载好的文件，跳过重复下载 -> {target_path}", level="success")
                return

        if not self._ensure_engine_ready(software_id):
            return

        row_data["network_channel_index"] = 0
        aria2_options = self._aria2_proxy_options(row_data)
        if aria2_options is None:
            page._set_status(row_data, STATUS_DOWNLOAD_FAILED)
            return

        task = page.download_service.download(
            url=download_url,
            software_id=software_id,
            software_name=name,
            filename=filename,
            save_dir=save_dir,
            options=aria2_options,
            max_retries=0 if active_network_channel(row_data, page.config_manager).name == CHANNEL_DIRECT else 3,
        )
        if task and task.gid:
            page._download_tasks[software_id] = task
            row_data["progress_percent"] = 0.0
            page._set_status(row_data, STATUS_DOWNLOADING, 0)
            page._append_info(f"{name}：" + f"开始下载 {filename}", level="success")
            data = page.state_manager.load()
            data.setdefault("active", {})[software_id] = {
                "url": download_url,
                "filename": filename,
                "save_dir": str(save_dir),
            }
            page.state_manager.save(data)
        else:
            page._append_info(f"{name}：" + "添加下载任务失败", level="error")

    def _start_multi_download_file(self, software_id, row_data):
        """启动多文件下载中的当前文件。"""
        page = self._page
        downloads = row_data.get("_multi_downloads", [])
        index = row_data.get("_multi_download_index", 0)
        if index >= len(downloads):
            return
        dl = downloads[index]
        url = dl.get("url", "")
        dl_filename = dl.get("filename", "") or url.split("/")[-1]
        dl_name = dl.get("name", dl_filename)
        name = row_data["node"].get("name", "")

        if not url:
            page._append_info(f"{name}：第 {index+1} 个文件缺少下载链接，跳过。", level="warning")
            self._record_multi_result(row_data, "invalid", dl_name, "缺少下载链接")
            self._advance_multi_download(software_id, row_data)
            return

        save_dir = self._software_download_dir(row_data)
        save_dir.mkdir(parents=True, exist_ok=True)
        target_path = (save_dir / dl_filename).resolve()
        self._report_stale_download_source_reset(software_id, url, target_path)

        if target_path.exists() and target_path.is_file():
            aria2_control = target_path.with_suffix(target_path.suffix + ".aria2")
            if aria2_control.exists():
                page._append_info(f"{name}（{dl_name}）：检测到未完成的下载，尝试断点续传 -> {target_path}", level="success")
            else:
                page._append_info(f"{name}（{dl_name}）：文件已存在，跳过。", level="success")
                completed = row_data.setdefault("_multi_completed_filenames", [])
                if dl_filename not in completed:
                    completed.append(dl_filename)
                self._record_multi_result(row_data, "existing", dl_name)
                self._advance_multi_download(software_id, row_data)
                return

        if not self._ensure_engine_ready(software_id):
            return

        row_data["network_channel_index"] = 0
        aria2_options = self._aria2_proxy_options(row_data)
        if aria2_options is None:
            page._set_status(row_data, STATUS_DOWNLOAD_FAILED)
            return

        # 任务提交失败也会异步走统一 taskFailed 回调。先登记当前子文件，避免回调发生时
        # 仍读取到上一个子文件名称而把失败结果记到错误文件上。
        row_data["_multi_download_filename"] = dl_filename
        row_data["_multi_download_name"] = dl_name
        task = page.download_service.download(
            url=url,
            software_id=software_id,
            software_name=f"{name}（{dl_name}）",
            filename=dl_filename,
            save_dir=save_dir,
            options=aria2_options,
            max_retries=0 if active_network_channel(row_data, page.config_manager).name == CHANNEL_DIRECT else 3,
        )
        if task and task.gid:
            page._download_tasks[software_id] = task
            row_data["progress_percent"] = 0.0
            page._set_status(row_data, STATUS_DOWNLOADING, 0)
            page._append_info(f"{name}（{dl_name}）：开始下载 {dl_filename}", level="success")
            page.state_manager.save_active(
                software_id,
                self._multi_state_info(task, row_data, url, dl_filename, save_dir),
            )
        else:
            page._append_info(f"{name}（{dl_name}）：添加下载任务失败", level="error")

    def _advance_multi_download(self, software_id, row_data):
        """多文件下载：前进到下一个文件，或按真实结果结束批次。

        参数：software_id 是下载项 ID；row_data 保存本批次文件列表、索引和结果。
        返回值：无。
        副作用：可能提交下一个 aria2 任务，或更新表格状态和信息面板；不会把失败文件
        伪装成完成。调用方必须先通过 _record_multi_result() 写入当前文件的最终结果。
        """
        page = self._page
        downloads = row_data.get("_multi_downloads", [])
        index = row_data.get("_multi_download_index", 0) + 1
        row_data["_multi_download_index"] = index
        name = row_data["node"].get("name", "")
        if index < len(downloads):
            page._append_info(f"{name}：第 {index+1}/{len(downloads)} 个文件开始下载。", level="success")
            self._start_multi_download_file(software_id, row_data)
        else:
            results = row_data.pop("_multi_download_results", {})
            completed_count = len(results.get("completed", []))
            existing_count = len(results.get("existing", []))
            failed = list(results.get("failed", [])) + list(results.get("invalid", []))
            successful_count = completed_count + existing_count
            row_data.pop("_multi_downloads", None)
            row_data.pop("_multi_download_index", None)
            row_data.pop("_multi_download_filename", None)
            row_data.pop("_multi_download_name", None)
            row_data.pop("_multi_missing_artifact_retries", None)
            complete_selection = bool(row_data.pop("_multi_selection_is_complete", False))
            final_channel = row_data.pop("_multi_last_failed_channel", "")
            row_data["download_speed"] = 0
            if not failed:
                missing_files = []
                download_dir = self._software_download_dir(row_data)
                for item in downloads:
                    if not isinstance(item, dict):
                        missing_files.append("无效下载条目")
                        continue
                    filename = str(item.get("filename", "") or "").strip()
                    path = download_dir / filename if filename else None
                    control_path = path.with_suffix(path.suffix + ".aria2") if path else None
                    if not path or not path.is_file() or (control_path and control_path.exists()):
                        missing_files.append(str(item.get("name", filename or "未命名文件")))
                if missing_files:
                    page._set_status(row_data, STATUS_DOWNLOAD_FAILED)
                    page._append_info(
                        f"{name}：aria2 已报告批次完成，但下列文件未完整落盘：{'、'.join(missing_files)}。",
                        level="error",
                    )
                    return
                manifest_path = None
                if complete_selection:
                    try:
                        manifest_path = write_multi_download_manifest(
                            download_dir, downloads
                        )
                    except (OSError, ValueError) as exc:
                        page._set_status(row_data, STATUS_DOWNLOAD_FAILED)
                        page._append_info(
                            f"{name}：文件下载完成，但无法写入多文件关系清单：{exc}",
                            level="error",
                        )
                        return
                row_data["progress_percent"] = 100.0
                page._set_status(row_data, STATUS_DONE)
                page._append_info(
                    f"{name}：全部 {len(downloads)} 个文件下载完成。",
                    level="success",
                )
                if manifest_path is not None:
                    page._append_info(
                        f"{name}：已记录主安装包与附属文件关系。",
                        level="success",
                    )
                return

            row_data["progress_percent"] = successful_count * 100.0 / len(downloads) if downloads else 0.0
            row_data["_multi_download_failure"] = {
                "total": len(downloads),
                "successful": successful_count,
                "failed": [item.get("name", "未命名文件") for item in failed],
            }
            channels = network_channels(row_data, page.config_manager)
            if final_channel == CHANNEL_DIRECT and len(channels) <= 1:
                page._set_status(row_data, STATUS_DIRECT_FAILED)
            else:
                page._set_status(row_data, STATUS_DOWNLOAD_FAILED)
            failed_names = "、".join(item.get("name", "未命名文件") for item in failed)
            if successful_count:
                summary = f"{successful_count} 个成功或已存在，{len(failed)} 个失败"
            else:
                summary = f"全部 {len(failed)} 个文件下载失败"
            page._append_info(
                f"{name}：多文件下载未完成，{summary}。失败文件：{failed_names}",
                level="error",
            )

    @staticmethod
    def _record_multi_result(row_data, result_type, download_name, detail=""):
        """记录一个多文件子任务的最终结果。

        参数：row_data 是下载行；result_type 为 completed/existing/failed/invalid；
        download_name 是用户可见文件名称；detail 是失败原因或补充信息。
        返回值：无。
        副作用：修改 row_data 中的批次结果，仅供 _advance_multi_download() 汇总使用。
        """

        results = row_data.setdefault(
            "_multi_download_results",
            {"completed": [], "existing": [], "failed": [], "invalid": []},
        )
        results.setdefault(result_type, []).append({"name": download_name, "detail": detail})

    def _multi_state_info(self, task, row_data, url=None, filename=None, save_dir=None):
        """构造可跨重启恢复的多文件下载状态。

        参数：task 是当前 aria2 任务；row_data 保存整个多文件批次；url/filename/save_dir
        可传当前子文件的实际值。返回值：可写入 download_state.json 的普通字典。副作用：无。
        该状态包含完整选择列表、当前索引和已完成文件，不能只保存当前 aria2 子任务。
        """

        current_url = url if url is not None else getattr(task, "url", "")
        current_filename = filename if filename is not None else getattr(task, "filename", "")
        current_dir = save_dir if save_dir is not None else getattr(task, "save_dir", "")
        return {
            "url": current_url,
            "filename": current_filename or "",
            "save_dir": str(current_dir) if current_dir else "",
            "progress": getattr(task, "progress_percent", 0.0),
            "multi_downloads": list(row_data.get("_multi_downloads", [])),
            "multi_all_downloads": list(row_data.get("_multi_all_downloads", row_data.get("_multi_downloads", []))),
            "multi_download_index": int(row_data.get("_multi_download_index", 0)),
            "multi_total": len(row_data.get("_multi_downloads", [])),
            "multi_selected_indexes": sorted(row_data.get("_selected_multi_download_indexes", set())),
            "multi_completed_filenames": list(row_data.get("_multi_completed_filenames", [])),
            "multi_selection_is_complete": bool(row_data.get("_multi_selection_is_complete", False)),
        }

    def _report_stale_download_source_reset(self, software_id, current_url, target_path):
        """调用文件服务清理旧断点，并在页面中展示清理结果。

        参数：software_id 是下载项 ID；current_url 是本次下载地址；target_path 是目标文件。
        返回值：无。
        副作用：文件服务可能删除旧的未完成文件和状态记录；本方法只负责追加提示文本。
        """
        result = self._file_session.reset_stale_download_source(
            software_id,
            current_url,
            target_path,
        )
        if result is None:
            return
        name = self._page.row_by_id.get(software_id, {}).get("node", {}).get("name", "下载项")
        detail = "；".join(result.removed_paths) if result.removed_paths else "旧控制文件或临时文件未找到"
        self._page._append_info(
            f"{name}：检测到下载地址已变化，已清理旧断点状态（{detail}），重新开始下载。",
            level="warning",
        )

    def _aria2_proxy_options(self, row_data):
        """根据当前联网通道生成传给 aria2 的网络参数。"""
        page = self._page
        options = {
            "check-certificate": "false",
            "connect-timeout": "10",
            "timeout": "60",
        }
        channel = active_network_channel(row_data, page.config_manager)
        options.update(aria2_options_for_channel(channel))
        options["winbox-network-channel"] = channel.name
        row_data["active_network_channel"] = channel.name
        if channel.name == CHANNEL_DIRECT:
            # 直连仍快速建连，传输阶段允许短暂停顿和慢速。
            # 重试仅由 aria2 在原任务内执行，调用方保持 max_retries=0，避免次数叠加。
            options["max-tries"] = "3"
            options["retry-wait"] = "5"
            options["lowest-speed-limit"] = "0"
        # 下载脚本可以为单个软件声明 HTTP 请求头，例如 VPN Gate 的 Referer 和浏览器
        # User-Agent。请求头只随当前任务传给 aria2，不写入全局配置，避免污染其他下载。
        check_result = page._check_results.get(row_data.get("id", ""), {})
        headers = check_result.get("download_headers", [])
        if isinstance(headers, str):
            headers = [headers]
        if isinstance(headers, list):
            valid_headers = [str(header).strip() for header in headers if str(header).strip()]
            if valid_headers:
                options["header"] = valid_headers
        return options

    def _retry_pending_download(self):
        """引擎就绪后提交启动期间积压的全部下载请求。

        返回值：无。
        副作用：清空页面待下载队列，并对仍存在的行重新执行下载前检查；已被用户删除的
        行会被跳过。该方法由 aria2 engineReady 信号在主线程调用。
        """
        page = self._page
        pending_ids = list(page._pending_download_ids)
        page._pending_download_ids.clear()
        for sid in pending_ids:
            row_data = page.row_by_id.get(sid)
            if row_data:
                self._do_download(sid, row_data)

    def on_engine_start_failed(self, message):
        """结束因 aria2 启动失败而滞留的待下载项。

        参数 message 是进程管理器提供的失败原因。返回值：无。
        副作用：清空页面待启动下载队列，将仍未创建任务的项目标为下载失败并写入信息框。
        用户可直接点击重试，不会再无限显示“正在启动下载引擎”。
        """

        page = self._page
        pending_ids = list(page._pending_download_ids)
        page._pending_download_ids.clear()
        for software_id in pending_ids:
            row_data = page.row_by_id.get(software_id)
            if row_data is None or software_id in page._download_tasks:
                continue
            page._set_status(row_data, STATUS_DOWNLOAD_FAILED)
            name = row_data.get("node", {}).get("name", "未命名软件")
            page._append_info(
                f"{name}：下载引擎启动失败：{message}。请检查下载设置后点击重试。",
                level="error",
            )

    def retry_missing_completed_multi_file(self, task, row_data):
        """对“aria2 报完成但文件缺失”的当前多文件子任务补偿提交一次。

        参数 task 是已报告 complete 的任务；row_data 是所属下载行。返回值为 True 表示已
        重新提交，False 表示已达到一次补偿上限。副作用：会创建一个新的 aria2 子任务并
        更新下载状态；不会推进多文件索引，确保同一文件不会被跳过。
        """

        filename = str(task.filename or "").strip()
        retries = row_data.setdefault("_multi_missing_artifact_retries", {})
        retry_count = int(retries.get(filename, 0) or 0)
        if retry_count >= 1:
            return False
        retries[filename] = retry_count + 1
        name = row_data.get("node", {}).get("name", "未命名软件")
        page = self._page
        page._append_info(
            f"{name}：aria2 报告 {filename or '当前文件'} 完成但文件未落盘，正在重新提交一次。",
            level="warning",
        )
        self._start_multi_download_file(task.software_id, row_data)
        return True

    # ── 批量下载入口 ──────────────────────────────────────

    def download_updates(self):
        """下载所有可更新的软件（需人工判断的项不包含在内）。"""
        page = self._page
        download_rows = [
            r for r in page.rows
            if r.get("status") == STATUS_READY_DOWNLOAD
            and network_channels(r, page.config_manager)
        ]
        if not download_rows:
            page._append_info("没有可下载的更新。请先检测更新。", level="warning")
            return
        for row_data in download_rows:
            page.download_single(row_data["node"].get("id", ""))
        page._append_info(f"开始下载 {len(download_rows)} 个可更新项。", level="success")

    # ── 暂停 / 恢复 / 取消 ────────────────────────────────

    def pause_single(self, software_id):
        """暂停单个软件的下载，同步保存暂停状态到文件。"""
        page = self._page
        task = page._download_tasks.get(software_id)
        from .download_debug import log as _debug_log
        _debug_log(
            f"pause_single | sid={software_id} "
            f"has_task={task is not None} task_status={task.status if task else None}"
        )
        if task and page.download_service:
            if not page.download_service.pause(task.gid):
                if task.status == "complete":
                    page._append_info(f"{task.software_name}：下载已完成，无需暂停。", level="info")
                elif task.status in ("error", "removed"):
                    page._append_info(f"{task.software_name}：下载状态已变化为 {task.status}，无需暂停。", level="warning")
                else:
                    page._append_info(f"{task.software_name}：暂停重试后仍未成功，请稍后再试。", level="warning")
                return
            task_info = {
                "url": task.url,
                "filename": task.filename or "",
                "save_dir": str(task.save_dir) if task.save_dir else "",
                "progress": task.progress_percent,
            }
            row_data = page.row_by_id.get(software_id)
            if row_data and row_data.get("_multi_downloads"):
                task_info = self._multi_state_info(task, row_data)
            page.state_manager.save_paused(task.software_id, task_info)
            page._append_info(f"{task.software_name}：" + "已暂停", level="info")

    def resume_single(self, software_id):
        """恢复被暂停的下载。"""
        page = self._page
        task = page._download_tasks.get(software_id)
        from .download_debug import log as _debug_log
        _debug_log(
            f"resume_single | sid={software_id} "
            f"has_task={task is not None} task_status={task.status if task else None}"
        )
        if task and page.download_service:
            row_data = page.row_by_id.get(software_id)
            if row_data and task.status == "paused" and task_mode(task) != current_download_mode(row_data):
                page.download_service.cancel(task.gid)
                page._download_tasks.pop(software_id, None)
                page.state_manager.remove(software_id)
                page._append_info(
                    f"{task.software_name}：下载方式已改为"
                    f"{page._download_mode_text(current_download_mode(row_data))}，重新提交下载任务。",
                    level="warning",
                )
                page.download_single(software_id)
                return
            if page.download_service.resume(task.gid):
                page._append_info(f"{task.software_name}：" + "已恢复下载", level="success")
            else:
                page._append_info(f"{task.software_name}：恢复失败，下载引擎不可用。", level="warning")
        else:
            page.state_manager.remove(software_id)
            page.download_single(software_id)

    def retry_with_proxy(self, software_id):
        """用户在直连失败后手动选择临时代理重试。

        参数：software_id 是下载行 ID。返回值：无。
        副作用：仅修改当前页面临时下载方式，成功时会创建新的 aria2 任务；不会保存到
        软件配置。此入口不能由失败回调自动调用，避免直连失败后未经用户确认切换代理。
        """
        page = self._page
        row_data = page.row_by_id.get(software_id)
        if row_data is None:
            return
        if not is_download_mode_allowed(row_data, "proxy"):
            return
        if not configured_proxy_channels(page.config_manager):
            page._append_info(
                blocked_proxy_message(row_data["node"].get("name", "这个软件")),
                level="warning",
            )
            return
        if not page.confirm_download_mode_switch(row_data, "proxy"):
            return
        task = page._download_tasks.get(software_id)
        if task is not None:
            if self._try_fallback_to_proxy(task, row_data):
                return
        page._column_handler.set_temporary_download_mode(row_data, "proxy")
        page._append_info(
            f"{row_data['node'].get('name', '')}：已临时切换为代理模式，重新提交下载任务。",
            level="warning",
        )
        page.download_single(software_id)

    def cancel_single(self, software_id):
        """取消下载并清理。"""
        page = self._page
        page._auto_action_coordinator.cancel(software_id)
        page._pending_download_ids = [
            pending_id for pending_id in page._pending_download_ids
            if pending_id != software_id
        ]
        task = page._download_tasks.pop(software_id, None)
        row_data = page.row_by_id.get(software_id)
        if task and page.download_service:
            page.download_service.cancel(task.gid)
        page.state_manager.remove(software_id)
        if row_data:
            page._set_status(row_data, STATUS_NOT_CHECKED)
            page._append_info(f"{row_data['node'].get('name', '')}：" + "已取消下载", level="warning")

    def pause_all(self):
        """暂停所有下载中或等待中的任务，并持久化断点续传状态。

        返回值：无。
        副作用：逐项复用 ``pause_single``，因此 aria2 状态、download_state.json
        和页面提示始终保持一致。不能直接调用下载服务 pause，否则程序重启后
        暂停任务没有保存记录，会被错误地当作普通下载重新恢复。
        """

        page = self._page
        paused = 0
        for task in list(page._download_tasks.values()):
            if task and task.gid and task.status in ("active", "waiting"):
                self.pause_single(task.software_id)
                if task.status == "paused":
                    paused += 1
        if paused == 0:
            page._append_info("没有正在下载的任务。", level="warning")

    def cancel_all(self):
        """取消所有下载任务（包括已持久化的暂停状态）。"""
        page = self._page
        cancelled = 0
        handled = set()
        # 先处理活跃任务
        for sid in list(page._download_tasks.keys()):
            self.cancel_single(sid)
            handled.add(sid)
            cancelled += 1
        # 再处理已持久化的暂停状态
        state = page.state_manager.load()
        for sid in list(state.get("paused", {}).keys()):
            if sid in handled:
                continue
            row_data = page.row_by_id.get(sid)
            self.cancel_single(sid)
            if row_data:
                cancelled += 1
        if cancelled == 0:
            page._append_info("没有需要取消的下载任务。", level="warning")

    def resume_all(self):
        """恢复所有已暂停的下载任务。"""
        page = self._page
        resumed = 0
        for sid, row_data in list(page.row_by_id.items()):
            status = row_data.get("status")
            if status == STATUS_PAUSED:
                page.resume_single(sid)
                resumed += 1
        if resumed == 0:
            page._append_info("没有已暂停的下载任务。", level="warning")

    # ── 任务状态回调 ──────────────────────────────────────

    def on_task_status_changed(self, task):
        """aria2 任务状态变更时更新 UI。"""
        self._task_status.on_task_status_changed(task)

    def refresh_proxy_state(self):
        """代理开关变化后刷新按钮状态，并取消不匹配的活跃、等待或暂停任务。"""
        page = self._page
        page._network_generation += 1
        for row_data in list(page.rows):
            sid = row_data.get("id", "")
            task = page._download_tasks.get(sid)
            channels = network_channels(row_data, page.config_manager)
            if task and task.status in ("active", "waiting", "paused"):
                valid_names = {channel.name for channel in channels}
                task_channel = task_network_channel(task)
                if task_channel not in valid_names:
                    if page.download_service:
                        page.download_service.cancel(task.gid)
                    page._download_tasks.pop(sid, None)
                    row_data["download_speed"] = 0
                    page._set_status(row_data, STATUS_NOT_CHECKED)
                    page._append_info(
                        f"{row_data['node'].get('name', '')}：代理开关已变化，已取消使用旧联网通道的下载任务。",
                        level="warning",
                    )
                    continue
            if channels and row_data.get("status") == STATUS_PROXY_SKIPPED:
                row_data["status"] = STATUS_NOT_CHECKED
            page._set_status(row_data, row_data.get("status", STATUS_NOT_CHECKED))

    def on_task_completed(self, task):
        """下载完成处理。"""
        self._task_status.on_task_completed(task)

    def on_task_failed(self, task):
        """下载失败处理。"""
        self._task_status.on_task_failed(task)

    def _try_next_network_channel(self, task, row_data):
        """代理通道失败后继续下一条线路，最后尝试直连。

        参数：task 是失败的 aria2 任务；row_data 是所属下载行。
        返回值：True 表示已提交下一通道任务，False 表示不应自动重试。
        副作用：代理模式可能提交新 aria2 任务。直连模式必须立即返回 False，保证失败
        后只能由用户通过状态栏的“切换代理”明确触发代理下载。
        """
        page = self._page
        if current_download_mode(row_data) == "direct":
            return False
        channels = network_channels(row_data, page.config_manager)
        current_name = task_network_channel(task)
        current_index = next((idx for idx, channel in enumerate(channels) if channel.name == current_name), -1)
        next_index = current_index + 1
        if current_index < 0 or next_index >= len(channels):
            return False

        next_channel = channels[next_index]
        check_result = page._check_results.get(task.software_id, {})
        download_url = check_result.get("download_url") or task.url
        if not download_url:
            return False

        row_data["network_channel_index"] = next_index
        save_dir = self._software_download_dir(row_data)
        save_dir.mkdir(parents=True, exist_ok=True)
        filename = check_result.get("filename") or task.filename or download_url.split("/")[-1]
        aria2_options = self._aria2_proxy_options(row_data)
        if aria2_options is None:
            page._set_status(row_data, STATUS_DOWNLOAD_FAILED)
            return False

        page._append_info(
            f"{task.software_name}：{channels[current_index].label}下载失败，改用{next_channel.label}重试。",
            level="warning",
        )
        new_task = page.download_service.download(
            url=download_url,
            software_id=task.software_id,
            software_name=task.software_name,
            filename=filename,
            save_dir=save_dir,
            options=aria2_options,
            max_retries=0 if next_channel.name == CHANNEL_DIRECT else 3,
        )
        if new_task and new_task.gid:
            page._download_tasks[task.software_id] = new_task
            row_data["progress_percent"] = 0.0
            page._set_status(row_data, STATUS_DOWNLOADING, 0)
            return True
        page._set_status(row_data, STATUS_DOWNLOAD_FAILED)
        page._append_info(f"{task.software_name}：切换到{next_channel.label}时添加任务失败", level="error")
        return False

    def _try_fallback_to_proxy(self, task, row_data):
        """如果原任务使用直连，则用已配置的代理重试。返回 True 表示已重试。"""
        page = self._page
        node = row_data["node"]
        row_temp_mode = row_data.get("temp_download_mode")
        original_mode = str(row_temp_mode or node.get("download_mode", "direct") or "direct")
        if original_mode == "proxy":
            return False

        check_result = page._check_results.get(task.software_id, {})
        download_url = check_result.get("download_url") or task.url
        if not download_url:
            return False

        page._append_info(f"{task.software_name}：已按用户选择改用已配置的代理下载。", level="warning")

        page._column_handler.set_temporary_download_mode(row_data, "proxy")
        row_data["network_channel_index"] = 0
        if not network_channels(row_data, page.config_manager):
            page._append_info(blocked_proxy_message(task.software_name), level="warning")
            return False
        save_dir = self._software_download_dir(row_data)
        save_dir.mkdir(parents=True, exist_ok=True)
        filename = check_result.get("filename") or download_url.split("/")[-1]

        aria2_options = self._aria2_proxy_options(row_data)
        if aria2_options is None:
            page._set_status(row_data, STATUS_DOWNLOAD_FAILED)
            return False

        new_task = page.download_service.download(
            url=download_url,
            software_id=task.software_id,
            software_name=task.software_name,
            filename=filename,
            save_dir=save_dir,
            options=aria2_options,
            max_retries=0 if active_network_channel(row_data, page.config_manager).name == CHANNEL_DIRECT else 3,
        )
        if new_task and new_task.gid:
            page._download_tasks[task.software_id] = new_task
            row_data["progress_percent"] = 0.0
            page._set_status(row_data, STATUS_DOWNLOADING, 0)
            page._append_info(f"{task.software_name}：已切换至代理下载", level="success")
            return True

        page._set_status(row_data, STATUS_DOWNLOAD_FAILED)
        page._append_info(f"{task.software_name}：代理重试时添加任务失败", level="error")
        return False

    # ── 辅助方法 ──────────────────────────────────────────

    def _software_download_dir(self, row_data):
        page = self._page
        name = row_data["node"].get("name", "未命名软件")
        return software_download_directory(page.download_manager, name)
