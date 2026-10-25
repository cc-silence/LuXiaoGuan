"""版本检测协调器

主要作用：从 SoftwareDownloadPage 中提取版本检测相关的逻辑（单次检测、批量检测、
结果处理、失败处理、重试通知、完成通知），让页面类只负责 UI 展示和控件操作。

使用方式：页面创建 DownloadCheckCoordinator(page) 实例，将页面自身传入。
协调器通过 self._page 访问页面状态（row_by_id、rows、table 等）和 UI 回调方法。
页面收到版本检测器的信号后，直接调用协调器的同名方法即可。

注意事项：
- 协调器不持有独立的状态副本，直接读取和修改 page 的属性。
- 唯一的例外是 _batch_checking，它只在这个模块中使用，因此放在协调器内部。
- _check_results、_network_generation 等被页面其他方法多处使用，因此仍保留在 page 上。
- 不要在协调器内创建 Qt 控件或弹窗 —— 它运行在主线程，但不应绕过页面直接操控 UI。
"""

from modules.network_policy import configured_proxy_channels, blocked_proxy_message
from .download_size_cell import refresh_download_size
from modules.main_pages.download.download_page_utils import (
    current_download_mode,
    is_download_mode_allowed,
    looks_like_network_error,
    network_channels,
)
from modules.main_pages.download.multi_file_selection import install_multi_file_cell, is_multi_file_result
from .download_status import (
    STATUS_ARCH_MISMATCH,
    STATUS_CHECKING,
    STATUS_CHECK_FAILED,
    STATUS_DOWNLOADING,
    STATUS_MANUAL_DECISION,
    STATUS_NO_UPDATE,
    STATUS_NOT_CHECKED,
    STATUS_PAUSED,
    STATUS_PROXY_SKIPPED,
    STATUS_READY_DOWNLOAD,
    STATUS_WAITING,
)


class DownloadCheckCoordinator:
    """版本检测协调器

    负责：单个和批量版本检测的调度、结果处理、重试和完成通知。
    不负责：界面状态保存、UI 控件操作、页面初始化。

    输入参数：
        page: SoftwareDownloadPage 实例，协调器会读取和修改其属性。
    """

    def __init__(self, page):
        self._page = page
        # 批量检测状态，只在这个模块中使用
        self._batch_checking = False
        # 本轮批量检测实际包含的分类集合，用于完成时只统计参与检测的行
        self._checked_categories: set = set()
        # 检测优先级会话。多个单项/批量检测重叠时，只在第一项开始时暂停下载，
        # 最后一项结束后恢复，防止中途错误恢复下载再次挤占代理线路。
        self._network_priority_sessions = 0
        self._auto_paused_task_ids: set = set()
        self._single_network_checks: set = set()
        self._batch_network_priority = False

    # ── 对外接口 ──────────────────────────────────────────

    def check_single(self, software_id):
        """检测单个软件的更新（不阻塞 UI）。

        用户手动触发（点击按钮、右键菜单、架构切换）都会先清除版本检测缓存，
        确保脚本用当前架构重新执行。自动重试不经过这里，不会清缓存。
        """
        page = self._page
        row_data = page.row_by_id.get(software_id)
        if row_data is None:
            return
        if not network_channels(row_data, page.config_manager):
            name = row_data["node"].get("name", "这个软件")
            page._append_info(blocked_proxy_message(name), level="warning")
            page._set_status(row_data, STATUS_NOT_CHECKED)
            return
        # 用户主动触发检测，清除缓存确保脚本重新执行
        if page.version_checker:
            page.version_checker.invalidate_cache(software_id)
            # 清除上次取消遗留的标记，避免新检测被误丢弃。
            page.version_checker.clear_cancel(software_id)
        page._set_status(row_data, STATUS_CHECKING)
        node = row_data["node"]
        arch = row_data.get("current_arch")

        if page.version_checker:
            if software_id in self._single_network_checks:
                page._append_info(f"{node.get('name', '')}：正在检测中，请等待完成。", level="info")
                return
            check_node = dict(node)
            # 下载方式列是本次运行可临时修改的状态，检测脚本必须使用行内最新选择，
            # 不能继续读取软件配置 JSON 中的旧 download_mode。
            check_node["download_mode"] = current_download_mode(row_data)
            check_node["_network_generation"] = page._network_generation
            self._single_network_checks.add(software_id)
            self._begin_network_priority()
            page.version_checker.check_single_async(check_node, arch=arch)
        else:
            name = node.get("name", "")
            page._append_info(f"{name}：" + "版本检测器未就绪。", level="warning")
            page._set_status(row_data, STATUS_CHECK_FAILED)

    def cancel_check(self, software_id):
        """取消进行中的版本检测，让该行回到未检测状态。

        参数：software_id 是软件下载行 ID。
        返回值：无。
        副作用：通知 VersionChecker 丢弃该软件本次检测结果；结束对应的单项检测
        优先级会话；清空该软件旧的检测结果备份并回到“未检测”状态。
        调用限制：只应由状态列“取消检测”按钮触发；对批量检测中的单行同样生效，
        不会终止其他行的检测。
        """
        page = self._page
        row_data = page.row_by_id.get(software_id)
        if row_data is None:
            return
        if software_id in self._single_network_checks:
            self._finish_single_network_check(software_id)
        if page.version_checker is not None:
            page.version_checker.cancel_check(software_id)
        # 取消后旧的检测结果不再可信，必须清理，避免点击下载时误用过期链接。
        page._check_results.pop(software_id, None)
        page._set_status(row_data, STATUS_NOT_CHECKED)
        page._append_info(
            f"{row_data['node'].get('name', '')}：已取消本次检测，可重新检测更新。",
            level="info",
        )

    def check_all(self):
        """检测所有已展开文件夹中启用的软件。收起的文件夹不参与检测。"""
        page = self._page
        if not page.rows:
            page._append_info("没有可检测的软件。", level="warning")
            return
        if self._batch_checking:
            page._append_info("正在检测中，请等待完成。", level="warning")
            return

        # 所有文件夹都收起时，直接提示
        all_collapsed = all(
            row_data.get("category") in page.collapsed_categories
            for row_data in page.rows
            if row_data.get("type") == "software"
        )
        if all_collapsed:
            page._append_info("所有文件夹都已收起，没有可检测的软件。", level="warning")
            return

        allowed_rows = []
        blocked_names = []
        busy_names = []
        collapsed_names = []
        busy_statuses = {STATUS_DOWNLOADING, STATUS_WAITING, STATUS_PAUSED}
        for row_data in page.rows:
            # 收起的文件夹不参与全部检测
            if row_data.get("category") in page.collapsed_categories:
                cat = row_data.get("category", "")
                if cat and cat not in collapsed_names:
                    collapsed_names.append(cat)
                continue
            status = row_data.get("status")
            if status in busy_statuses:
                busy_names.append(row_data["node"].get("name", "未命名软件"))
                continue
            if network_channels(row_data, page.config_manager):
                allowed_rows.append(row_data)
            else:
                blocked_names.append(row_data["node"].get("name", "未命名软件"))
                page._set_status(row_data, STATUS_PROXY_SKIPPED)
        if not allowed_rows:
            page._append_info("没有可检测的软件。代理开关关闭时，代理模式软件不会参与检测。", level="warning")
            return

        self._batch_checking = True
        # 记录本轮实际检测的分类，供完成时只统计这些分类的结果
        self._checked_categories = {
            row_data.get("category") for row_data in allowed_rows
        }
        # 保留忙碌项目的检测结果（下载链接），避免后续继续时找不到 URL
        page._check_results = {
            sid: r for sid, r in page._check_results.items()
            if page.row_by_id.get(sid, {}).get("status") in busy_statuses
        }
        from .download_debug import log as _debug_log
        _debug_log(f"check_all 清空检测结果：剩余 {len(page._check_results)} 条")
        for row_data in allowed_rows:
            page._set_status(row_data, STATUS_CHECKING)

        # 全部检测时清除版本检测缓存，确保所有待检项目重新执行脚本
        if page.version_checker:
            page.version_checker.invalidate_cache()
            page.version_checker.clear_cancel()

        if page.version_checker:
            nodes = []
            for row_data in allowed_rows:
                node = dict(row_data["node"])  # 复制避免修改原始配置
                node["download_mode"] = current_download_mode(row_data)
                arch = row_data.get("current_arch")
                if arch:
                    node["_selected_arch"] = arch
                node["_network_generation"] = page._network_generation
                nodes.append(node)
            self._batch_network_priority = True
            self._begin_network_priority()
            page.version_checker.batch_check(nodes)
            page._append_info(f"开始检测 {len(nodes)} 个软件...", level="success")
            if busy_names:
                page._append_info(
                    "正在下载或暂停中，跳过检测：" + "、".join(busy_names),
                    level="info",
                )
            if blocked_names:
                page._append_info(
                    "代理开关关闭，已跳过代理模式软件：" + "、".join(blocked_names),
                    level="warning",
                )
            if collapsed_names:
                page._append_info(
                    "收起的文件夹不参与检测：" + "、".join(collapsed_names),
                    level="info",
                )
        else:
            page._append_info("版本检测器未就绪。", level="warning")
            self._batch_checking = False
            self._checked_categories.clear()
            for row_data in allowed_rows:
                page._set_status(row_data, STATUS_CHECK_FAILED)

    def check_category(self, category):
        """通过 VersionChecker 的受限并发队列检测一个分类。

        参数：category 是文件夹对应的分类名称。
        返回值：无。
        副作用：清除本分类的检测缓存、更新可检测行状态并提交批量脚本任务；下载中、等待中
        和暂停的行会跳过，代理不可用的行会标记为跳过。
        调用限制：同一时间只允许一个批量检测，避免文件夹右键菜单反复触发多个批量队列。
        """
        category_rows = [
            row_data
            for row_data in self._page.rows
            if row_data.get("type") == "software" and row_data.get("category") == category
        ]
        self._check_rows_batch(category_rows, f"分类“{category}”")

    def check_selected(self, selected):
        """通过 VersionChecker 的受限并发队列检测右键菜单选中的软件。

        参数：selected 是表格当前选中的软件下载行列表。
        返回值：无。
        副作用：会清除选中项的检测缓存、更新行状态并提交后台检测队列；不会检测未选中项。
        调用限制：只能由多选菜单入口调用，检测运行中会拒绝新批次，避免创建重叠队列。
        """
        rows = [
            row_data
            for row_data in selected
            if isinstance(row_data, dict) and row_data.get("type") == "software"
        ]
        self._check_rows_batch(rows, "选中软件")

    def _check_rows_batch(self, candidate_rows, label):
        """将一组已选软件下载行提交给 VersionChecker 的受限并发队列。

        参数：candidate_rows 是分类或多选入口提供的软件行；label 是用户可见的批次名称。
        返回值：无。
        副作用：清除非忙碌项的检测结果、设置检测状态、清除缓存并调用 batch_check。
        调用限制：所有多项目检测入口必须经由此方法，不能循环调用 check_single，否则会绕过
        VersionChecker 的并发上限并造成界面调度拥塞。
        """
        page = self._page
        if self._batch_checking:
            page._append_info("正在检测中，请等待完成。", level="warning")
            return
        if not candidate_rows:
            page._append_info(f"{label}没有可检测的软件。", level="warning")
            return
        busy_statuses = {STATUS_DOWNLOADING, STATUS_WAITING, STATUS_PAUSED}
        allowed_rows = []
        blocked_names = []
        busy_names = []
        for row_data in candidate_rows:
            if row_data.get("status") in busy_statuses:
                busy_names.append(row_data["node"].get("name", "未命名软件"))
                continue
            if network_channels(row_data, page.config_manager):
                allowed_rows.append(row_data)
            else:
                blocked_names.append(row_data["node"].get("name", "未命名软件"))
                page._set_status(row_data, STATUS_PROXY_SKIPPED)
        if not allowed_rows:
            page._append_info(f"{label}没有可检测的软件。", level="warning")
            return
        if page.version_checker is None:
            page._append_info("版本检测器未就绪。", level="warning")
            for row_data in allowed_rows:
                page._set_status(row_data, STATUS_CHECK_FAILED)
            return

        self._batch_checking = True
        self._checked_categories = {row_data.get("category") for row_data in allowed_rows}
        # 保留正在下载项的旧链接，避免批量检测清空其继续下载所需状态。
        page._check_results = {
            software_id: result
            for software_id, result in page._check_results.items()
            if page.row_by_id.get(software_id, {}).get("status") in busy_statuses
        }
        from .download_debug import log as _debug_log
        _debug_log(f"_check_rows_batch 清空检测结果：剩余 {len(page._check_results)} 条")
        for row_data in allowed_rows:
            page._set_status(row_data, STATUS_CHECKING)
        page.version_checker.invalidate_cache()
        page.version_checker.clear_cancel()
        nodes = []
        for row_data in allowed_rows:
            node = dict(row_data["node"])
            # 临时切换的下载方式存于行状态，不能继续使用配置文件中的旧值。
            node["download_mode"] = current_download_mode(row_data)
            arch = row_data.get("current_arch")
            if arch:
                node["_selected_arch"] = arch
            node["_network_generation"] = page._network_generation
            nodes.append(node)
        self._batch_network_priority = True
        self._begin_network_priority()
        page.version_checker.batch_check(nodes)
        page._append_info(f"开始检测{label}的 {len(nodes)} 个软件...", level="success")
        if busy_names:
            page._append_info("正在下载或暂停中，跳过检测：" + "、".join(busy_names), level="info")
        if blocked_names:
            page._append_info("代理开关关闭，已跳过代理模式软件：" + "、".join(blocked_names), level="warning")

    def retry_check_with_proxy(self, software_id):
        """由用户明确选择后临时改用代理重新检测。

        参数：software_id 是软件下载行 ID。
        返回值：无。
        副作用：只修改当前页面行的临时下载方式并立即启动检测，不会写入软件配置。
        仅供直连检测失败状态栏的“切换代理”调用，避免直连失败后后台自动改用代理。
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
        page._column_handler.set_temporary_download_mode(row_data, "proxy")
        page._append_info(
            f"{row_data['node'].get('name', '')}：已按你的选择临时切换为代理模式，重新检测更新。",
            level="warning",
        )
        self.check_single(software_id)

    # ── 信号处理 ──────────────────────────────────────────

    def on_check_result(self, software_id, result):
        """处理版本检测结果。"""
        page = self._page
        row_data = page.row_by_id.get(software_id)
        if row_data is None:
            self._finish_single_network_check(software_id)
            return
        if result.get("network_generation") != page._network_generation:
            # 联网方式切换后，旧通道的结果不能覆盖新选择；但旧实现只丢弃结果，
            # 会把状态永久留在“检测中”。将该行还原为可重新检测状态，等待用户
            # 按当前通道主动发起新检测。
            if row_data.get("status") == STATUS_CHECKING:
                page._set_status(row_data, STATUS_NOT_CHECKED)
            self._finish_single_network_check(software_id)
            return

        # 每个新检测结果都获得独立代次；旧倒计时即使已进入事件队列也不能作用到新结果。
        page._auto_action_coordinator.prepare_new_result(row_data)

        name = result.get("name", "")
        if result.get("success", False):
            has_update = result.get("has_update", False)
            latest_version = result.get("latest_version") or "--"
            current_version = result.get("current_version") or "--"
            download_url = result.get("download_url", "") or ""
            needs_manual_decision = bool(download_url) and (
                str(current_version).strip() in (
                    "--",
                    "无法使用exe读取",
                    "未检测到本地安装的exe文件",
                )
                or str(latest_version).strip() in ("--", "latest")
            )

            row = page._row_for_data(row_data)
            if row is not None:
                page._set_row_text(row, 1, str(current_version), row_data)
                page._set_row_text(row, 2, str(latest_version), row_data)
                filename = result.get("filename")
                filename_tooltip = ""
                downloads = result.get("downloads") or None
                if downloads is not None and isinstance(downloads, list) and len(downloads) > 1:
                    # 每次重新检测都必须清空旧选择。下载脚本可能返回新的文件顺序或文件集，
                    # 不能让上次勾选的索引自动指向本次的其他文件。
                    row_data.pop("_selected_multi_download_indexes", None)
                    # 新检测结果不是上次恢复的批次。只有当前仍有 aria2 任务时保留批次内存，
                    # 其他情况必须清掉旧批次，确保点击下载时重新弹出文件选择框。
                    task = page._download_tasks.get(software_id)
                    if task is None or task.status not in ("active", "waiting", "paused"):
                        for key in (
                            "_multi_downloads",
                            "_multi_all_downloads",
                            "_multi_download_index",
                            "_multi_completed_filenames",
                            "_multi_download_results",
                            "_multi_selection_is_complete",
                        ):
                            row_data.pop(key, None)
                    row_data["_multi_selection_confirmed"] = False
                    filename = f"共 {len(downloads)} 个文件"
                    full_names = []
                    for download in downloads:
                        if not isinstance(download, dict):
                            continue
                        full_name = str(download.get("filename") or download.get("name") or "").strip()
                        if not full_name:
                            download_item_url = str(download.get("url") or "").strip()
                            full_name = download_item_url.rsplit("/", 1)[-1] if download_item_url else ""
                        if full_name:
                            full_names.append(full_name)
                    filename_tooltip = "\n".join(full_names)
                else:
                    if not filename and download_url:
                        filename = download_url.split("/")[-1]
                    filename_tooltip = str(filename or "")
                if is_multi_file_result(result):
                    install_multi_file_cell(page, row_data, result)
                else:
                    page._set_row_text(
                        row,
                        3,
                        str(filename or "--"),
                        row_data,
                        tooltip=filename_tooltip,
                    )

            if needs_manual_decision:
                page._set_status(row_data, STATUS_MANUAL_DECISION)
                page._append_info(
                    f"{name}：无法可靠判断是否需要更新。当前版本：{current_version}，"
                    f"网上版本：{latest_version}。请根据软件来源、文件名和实际需要自行决定是否下载。",
                    level="warning",
                )
            elif has_update:
                page._set_status(row_data, STATUS_READY_DOWNLOAD)
                page._append_info(f"{name}：" + f'有新版本 {latest_version}（当前：{current_version}）', level="success")
            else:
                page._set_status(row_data, STATUS_NO_UPDATE)
                page._append_info(f"{name}：" + f'已是最新版本 {current_version}', level="success")
        else:
            page._set_status(row_data, STATUS_CHECK_FAILED)
            page._append_info(
                self._check_failed_message(name, result.get("error", "未知错误")),
                level="error",
            )

        page._check_results[software_id] = result
        refresh_download_size(page, row_data, result=result)
        from .download_debug import log as _debug_log
        _debug_log(
            f"on_check_result 存储 | sid={software_id} name={result.get('name')} "
            f"success={result.get('success')} url={result.get('download_url')!r} "
            f"gen={result.get('network_generation')!r} page_gen={page._network_generation}"
        )

        # 检测本地安装架构，如果和当前选中架构不一致，在状态列提示用户选择
        local_arch = result.get("local_arch")
        if (
            local_arch
            and local_arch in ("x64", "x86")
            and not row_data.get("_arch_auto_switched")
        ):
            current_arch = row_data.get("current_arch", "")
            if local_arch != current_arch:
                row_data["_detected_local_arch"] = local_arch
                page._set_status(row_data, STATUS_ARCH_MISMATCH)
                page._append_info(
                    f"{name}：检测到本地安装架构为 {local_arch}"
                    f"（当前选择 {current_arch}），请在状态列选择切换或保持当前设置。",
                    level="info",
                )
        self._finish_single_network_check(software_id)

    def on_check_failed(self, software_id, error):
        """处理版本检测失败。"""
        page = self._page
        row_data = page.row_by_id.get(software_id)
        if row_data:
            page._auto_action_coordinator.prepare_new_result(row_data)
            page._set_status(row_data, STATUS_CHECK_FAILED)
            name = row_data["node"].get("name", "")
            page._append_info(self._check_failed_message(name, error), level="error")
        # ScriptRunner 在进程启动失败等异常路径会直接发送 checkFailed。必须结束
        # 单项优先级会话，否则同一软件后续点击会一直被判定为“正在检测中”。
        self._finish_single_network_check(software_id)

    def on_check_retrying(self, software_id, name, attempt):
        """检测失败自动重试时通知用户。"""
        page = self._page
        page._append_info(f"{name}：检测失败，{attempt} 秒后自动重试（第 {attempt} 次）...", level="warning")

    def on_all_checks_completed(self):
        """所有版本检测完成。只统计本轮实际参与检测的分类。"""
        page = self._page
        self._batch_checking = False
        checked = self._checked_categories
        ready_count = sum(
            1 for r in page.rows
            if r.get("status") == STATUS_READY_DOWNLOAD
            and (not checked or r.get("category") in checked)
        )
        manual_count = sum(
            1 for r in page.rows
            if r.get("status") == STATUS_MANUAL_DECISION
            and (not checked or r.get("category") in checked)
        )
        failed_count = sum(
            1 for r in page.rows
            if r.get("status") == STATUS_CHECK_FAILED
            and (not checked or r.get("category") in checked)
        )
        self._checked_categories.clear()
        parts = []
        if ready_count:
            parts.append(f"{ready_count} 个可下载")
        if manual_count:
            parts.append(f"{manual_count} 个版本无法比较但可下载")
        if failed_count:
            parts.append(f"{failed_count} 个检测失败")
        if parts:
            level = "warning" if failed_count and not ready_count else "success"
            page._append_info(f"全部检测完成。{'，'.join(parts)}。", level=level)
        else:
            page._append_info("全部检测完成。没有可更新的软件。", level="success")
        if self._batch_network_priority:
            self._batch_network_priority = False
            self._end_network_priority()

    # ── 辅助方法 ──────────────────────────────────────────

    def _begin_network_priority(self):
        """开始检测优先级会话，暂停当前活跃下载以释放代理线路。

        只记录本方法成功暂停的任务；用户原本暂停的任务不在记录中，结束时不会被误恢复。
        """
        self._network_priority_sessions += 1
        if self._network_priority_sessions != 1:
            return
        page = self._page
        service = page.download_service
        if service is None:
            return
        for software_id, task in list(page._download_tasks.items()):
            if task.status == "active" and service.pause(task.gid):
                self._auto_paused_task_ids.add(software_id)
        if self._auto_paused_task_ids:
            page._append_info("检测更新期间已临时暂停下载，检测结束后会自动恢复。", level="info")
            from .download_debug import log as _debug_log
            _debug_log(f"net_priority暂停 | ids={sorted(self._auto_paused_task_ids)}")

    def _end_network_priority(self):
        """结束一个检测优先级会话，最后一个会话结束时恢复自动暂停的下载。"""
        self._network_priority_sessions = max(0, self._network_priority_sessions - 1)
        if self._network_priority_sessions:
            return
        page = self._page
        service = page.download_service
        auto_paused_ids = list(self._auto_paused_task_ids)
        self._auto_paused_task_ids.clear()
        if service is None:
            return
        resumed = 0
        for software_id in auto_paused_ids:
            task = page._download_tasks.get(software_id)
            if task is not None and task.status == "paused" and service.resume(task.gid):
                resumed += 1
        if resumed:
            page._append_info(f"检测完成，已自动恢复 {resumed} 个下载任务。", level="info")
        from .download_debug import log as _debug_log
        _debug_log(
            f"net_priority恢复 | ids={auto_paused_ids} resumed={resumed} "
            f"sessions={self._network_priority_sessions}"
        )

    def _finish_single_network_check(self, software_id):
        """结束单项检测对应的优先级会话，重复结果不会重复恢复下载。"""
        if software_id in self._single_network_checks:
            self._single_network_checks.remove(software_id)
            self._end_network_priority()

    def _proxy_enabled_for_check(self):
        """返回是否有任意一级代理的 HTTP 地址已配置，用于给检测失败提示补充排查方向。"""
        from modules.config_services.proxy_config_manager import ProxyConfigManager
        return ProxyConfigManager().has_any_proxy()

    def _check_failed_message(self, name, error):
        """生成版本检测失败提示；含诊断信息时直接展示，不含时追加代理说明。"""
        error_text = str(error or "")
        # 如果错误消息已包含诊断信息（来自 version_checker 的三步诊断），直接展示
        if "【诊断】" in error_text:
            return f"{name}：检测失败 - {error_text}"
        base = f"{name}：检测失败 - {error_text}"
        if not looks_like_network_error(error_text):
            return base
        if self._proxy_enabled_for_check():
            return base + "。请检查代理地址、端口是否正确，或确认代理程序正在运行。"
        return base + "。当前代理已关闭，请检查网络；如果该网站直连不可用，请打开代理后重试。"
