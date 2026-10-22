"""下载页面跨重启状态恢复协调器。

主要作用：把 download_state.json 中的单文件和多文件任务重新映射到当前下载列表。
状态文件读写仍属于 download_services，页面状态和多文件控件恢复属于本模块，避免服务层
反向依赖 Qt 控件。
"""

from pathlib import Path

from .download_status import STATUS_DONE, STATUS_PAUSED


class DownloadStateRestoreCoordinator:
    """协调下载状态恢复、文件完整性检查和页面行状态更新。"""

    def __init__(self, page):
        """保存下载页面引用，不在构造阶段读取文件或修改界面。

        参数：page 是 SoftwareDownloadPage，提供状态管理器、行数据和状态栏更新方法。
        返回值：无。副作用：无。
        """

        self._page = page

    def restore(self):
        """将已保存下载状态恢复到当前已加载的软件行。

        返回值：无。副作用：读取 download_state.json，更新页面行状态、检测结果和多文件选择
        控件；确认完整的任务会移除过期恢复记录。不提交 aria2 任务，用户仍需主动继续下载。
        调用限制：必须在下载列表行和表格控件创建完成后调用。
        """

        page = self._page
        all_entries = page.state_manager.get_all_entries()
        if not all_entries:
            return

        for software_id, info in all_entries.items():
            row_data = page.row_by_id.get(software_id)
            if row_data is None:
                continue
            self._restore_entry(software_id, row_data, info)

    def _restore_entry(self, software_id, row_data, info):
        """恢复一个单文件或多文件任务，并按本地文件情况更新页面状态。"""

        page = self._page
        info = dict(info)
        self._restore_multi_batch(software_id, row_data, info)
        if row_data.get("_multi_downloads") and row_data.get("_multi_download_index", 0) >= len(row_data["_multi_downloads"]):
            row_data["progress_percent"] = 100.0
            page._set_status(row_data, STATUS_DONE)
            page.state_manager.remove(software_id)
            return

        save_dir = Path(info.get("save_dir", ""))
        filename = info.get("filename", "")
        target_path = (save_dir / filename).resolve() if save_dir and filename else None
        file_exists = bool(target_path and target_path.exists())
        if not file_exists and target_path and save_dir and save_dir.is_dir():
            # 某些服务器返回的文件名 Unicode 形式不同，按不区分大小写的实际文件名兜底匹配。
            for file_path in save_dir.iterdir():
                if file_path.is_file() and file_path.name.lower() == filename.lower():
                    target_path = file_path.resolve()
                    file_exists = True
                    break
        aria2_exists = bool(
            file_exists
            and target_path
            and target_path.with_suffix(target_path.suffix + ".aria2").exists()
        )

        if file_exists and aria2_exists:
            row_data["progress_percent"] = info.get("progress", 0.0)
            page._set_status(row_data, STATUS_PAUSED)
            page._check_results.setdefault(
                software_id,
                {"download_url": info.get("url", ""), "filename": info.get("filename", "")},
            )
        elif file_exists:
            page._set_status(row_data, STATUS_DONE)
        else:
            # 目标文件被用户移动或删除时仍保留可重试链接，不能把任务误标为已完成。
            page._check_results[software_id] = {
                "download_url": info.get("url", ""),
                "filename": info.get("filename", ""),
            }

    def _restore_multi_batch(self, software_id, row_data, info):
        """恢复多文件选择、已完成索引和当前子文件的下载链接。

        返回值：无。副作用：修改 row_data、检测结果和文件选择控件；不写入状态文件。
        只有保存数据确实表示多文件任务时才执行，单文件任务保持原有恢复流程。
        """

        multi_downloads = info.get("multi_downloads")
        all_saved_downloads = info.get("multi_all_downloads")
        if not isinstance(multi_downloads, list) or not (
            len(multi_downloads) > 1
            or isinstance(all_saved_downloads, list) and len(all_saved_downloads) > 1
        ):
            return

        page = self._page
        all_downloads = all_saved_downloads or multi_downloads
        row_data["_multi_downloads"] = list(multi_downloads)
        row_data["_multi_all_downloads"] = list(all_downloads)
        row_data["_multi_download_index"] = max(0, int(info.get("multi_download_index", 0) or 0))
        row_data["_multi_completed_filenames"] = list(info.get("multi_completed_filenames", []))
        row_data["_multi_selection_is_complete"] = bool(info.get("multi_selection_is_complete", False))
        row_data["_multi_selection_confirmed"] = True
        # 重启后不恢复旧勾选：断点续传只用上面的 _multi_downloads 批次，与复选框
        # 索引无关；恢复旧勾选会让用户误以为本次已选择。下拉格因此显示总文件数，
        # 用户需要时重新勾选，重新检测时同样从空选择开始。
        row_data.pop("_selected_multi_download_indexes", None)
        row_data["_multi_download_results"] = {
            "completed": [
                {"name": filename, "detail": "重启前已完成"}
                for filename in row_data["_multi_completed_filenames"]
            ],
            "existing": [],
            "failed": [],
            "invalid": [],
        }
        multi_result = {"success": True, "downloads": list(all_downloads)}
        page._check_results[software_id] = multi_result
        from modules.main_pages.download.multi_file_selection import install_multi_file_cell

        install_multi_file_cell(page, row_data, multi_result)
        self._skip_completed_multi_files(row_data, info)
        index = row_data["_multi_download_index"]
        if index >= len(row_data["_multi_downloads"]):
            return
        next_download = row_data["_multi_downloads"][index]
        if isinstance(next_download, dict):
            page._check_results[software_id].update(
                {
                    "download_url": next_download.get("url", ""),
                    "filename": next_download.get("filename", ""),
                }
            )

    @staticmethod
    def _skip_completed_multi_files(row_data, info):
        """处理崩溃发生在 aria2 完成和下一项状态写入之间的多文件任务。"""

        save_dir = Path(info.get("save_dir", ""))
        downloads = row_data["_multi_downloads"]
        while row_data["_multi_download_index"] < len(downloads):
            current = downloads[row_data["_multi_download_index"]]
            filename = str(current.get("filename", "")) if isinstance(current, dict) else ""
            current_path = save_dir / filename if filename else None
            control = current_path.with_suffix(current_path.suffix + ".aria2") if current_path else None
            if not current_path or not current_path.is_file() or (control and control.exists()):
                return
            if filename not in row_data["_multi_completed_filenames"]:
                row_data["_multi_completed_filenames"].append(filename)
            row_data["_multi_download_index"] += 1
