"""软件更新主线程预检控制器。

主要作用：按固定顺序编排进程确认、签名验证和目标目录确认。
输入参数：构造时传入 SoftwareUpdatePage；begin() 接收当前行、更新包、配置和策略。
返回值：begin() 不同步返回结果；预检完成后调用传入的 continuation。
副作用：会显示主线程模态弹窗、启动进程关闭 worker，并把本次临时配置同步到 row_data。
调用限制：所有 Qt 弹窗和 continuation 必须在主线程运行；耗时的进程关闭在 QThread 中运行。
"""

import time
import logging
from functools import wraps

from PySide6.QtCore import QObject, QThread, Slot

from modules.main_pages.update_confirmations import (
    confirm_firewall_rules,
    confirm_process_close_plan,
    _remaining_processes_message,
)
from modules.main_pages.update_directory_preflight import confirm_update_target_dirs
from modules.main_pages.update_preflight_config import sync_preflight_update_config
from modules.main_pages.update_process_preflight_worker import UpdateProcessPreflightWorker
from modules.main_pages.update_signature_preflight import confirm_update_signature
from modules.main_pages.update_target_dirs import collect_update_target_dirs
from modules.dialog_theme import build_current_base_stylesheet


def _guard_preflight_result(method):
    """预检回调异常统一转为失败状态，避免名额永久占用。"""
    @wraps(method)
    def guarded(self, software_id, *args):
        try:
            return method(self, software_id, *args)
        except Exception as exc:
            logging.getLogger('winbox.update').exception('Update preflight callback failed')
            self._pending_process_preflights.pop(software_id, None)
            self._page._on_update_finished(software_id, False, f'更新预检失败：{exc}')
    return guarded


class UpdatePreflightController(QObject):
    """编排软件更新启动前必须在主线程完成的确认步骤。

    参数：page 是 SoftwareUpdatePage 实例。
    返回值：构造函数无返回值；begin() 通过 continuation 启动后续更新。
    副作用：详见模块说明；控制器不会保存配置文件。
    """

    def __init__(self, page):
        super().__init__(page)
        self._page = page
        self._pending_process_preflights = {}

    def begin(self, row_data, package_path, update_config, strategy, continuation):
        """启动本次更新预检，并在通过后调用 continuation。

        参数：row_data 是更新行数据；package_path 是已确认存在的更新包；
        update_config 是标准化配置；strategy 是更新策略名称。
        continuation 是只在全部预检通过后于主线程调用的函数，参数依次为
        row_data、update_config、strategy。返回值：无。
        副作用：可能弹窗；有需要关闭的进程时启动 QThread，页面立即恢复响应。
        """
        page = self._page
        software_name = row_data.get("name", "")
        stylesheet = build_current_base_stylesheet(
            page.config_manager,
            page.light_theme,
            page.scale_manager,
        )

        update_config.pop("_signature_receipt", None)
        update_config.pop("_signature_preflight_result", None)
        update_config["_preflight_software_name"] = software_name
        update_config.pop("_process_preflight_failure", None)
        update_config.pop("_process_preflight_completed", None)
        update_config["_timing_preflight_started_at"] = time.monotonic()
        self._start_process_plan_scan(
            row_data,
            package_path,
            update_config,
            strategy,
            continuation,
            stylesheet,
        )

    def _start_process_plan_scan(
        self, row_data, package_path, update_config, strategy, continuation, stylesheet
    ):
        """在线程中扫描实际进程和文件占用，再回主线程显示关闭确认。

        参数来自 begin()。返回值：无。副作用：创建只读扫描 worker，把当前行改为
        checking_processes，避免目录递归和 tasklist 查询阻塞点击“更新”后的界面。
        """

        page = self._page
        software_id = str(row_data.get("id", ""))
        software_name = row_data.get("name", "")
        thread = QThread(page)
        worker = UpdateProcessPreflightWorker(
            software_id, update_config, package_path=package_path
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.scan_finished.connect(self._on_process_plan_scanned)
        worker.scan_finished.connect(thread.quit)
        worker.scan_finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)

        self._pending_process_preflights[software_id] = (
            row_data,
            package_path,
            update_config,
            strategy,
            continuation,
            stylesheet,
        )
        page._preflight_workers[software_id] = worker
        page._preflight_threads[software_id] = thread
        page._set_rows_status(software_id, "checking_processes")
        page._append_info(f"{software_name}：正在检查更新前进程和文件占用，请稍候。")
        update_config["_timing_process_scan_started_at"] = time.monotonic()
        thread.start()

    @Slot(str, object)
    @_guard_preflight_result
    def _on_process_plan_scanned(self, software_id, process_plan):
        """在主线程处理后台扫描结果，并按用户选择继续、取消或关闭进程。

        参数：software_id 标识更新行；process_plan 是 worker 收集的实际进程与锁文件。
        返回值：无。副作用：显示一次关闭确认；必要时启动下一阶段关闭 worker。
        """

        context = self._pending_process_preflights.get(software_id)
        page = self._page
        page._preflight_workers.pop(software_id, None)
        page._preflight_threads.pop(software_id, None)
        if context is None:
            return

        row_data, package_path, update_config, strategy, continuation, stylesheet = context
        software_name = row_data.get("name", "")
        scan_started = update_config.pop("_timing_process_scan_started_at", None)
        if scan_started is not None:
            update_config["_timing_process_scan_seconds"] = max(0.0, time.monotonic() - scan_started)
        error = str(process_plan.get("error", "") or "").strip()
        if error:
            self._pending_process_preflights.pop(software_id, None)
            page._set_rows_status(software_id, "ready")
            page._append_info(f"{software_name}：检查更新前进程失败：{error}", level="warning")
            return

        for warning in process_plan.get("warnings", []):
            page._append_info(f"{software_name}：{warning}", level="warning")

        for key in ("_signature_receipt", "_signature_preflight_result", "_timing_signature_seconds"):
            if key in process_plan:
                update_config[key] = process_plan[key]
        decision = confirm_process_close_plan(process_plan, page, stylesheet)
        if decision == "cancel":
            self._pending_process_preflights.pop(software_id, None)
            page._set_rows_status(software_id, "ready")
            page._append_info(f"{software_name}：已取消终止进程，更新被取消。", level="warning")
            return
        if decision == "continue":
            # 没有进程，或用户选择对系统占用仍要尝试。本轮 worker 不得再按配置
            # 盲目关闭进程；该标记仅存在于当前内存行，不会写回 JSON。
            update_config["_process_preflight_completed"] = True
            sync_preflight_update_config(row_data, update_config)
            self._pending_process_preflights.pop(software_id, None)
            self._continue_after_process_preflight(
                row_data, package_path, update_config, strategy, continuation, stylesheet
            )
            return

        self._start_process_close_worker(
            row_data,
            package_path,
            update_config,
            strategy,
            continuation,
            stylesheet,
            process_plan,
        )

    def _start_process_close_worker(
        self, row_data, package_path, update_config, strategy, continuation, stylesheet, process_plan
    ):
        """启动后台进程关闭任务并保存预检上下文。

        参数来自 begin()，process_plan 是用户刚刚确认的 PID 集合。返回值：无。
        副作用：创建 QThread、将当前行改为 preparing，并禁用重复更新按钮。
        """

        page = self._page
        software_id = str(row_data.get("id", ""))
        software_name = row_data.get("name", "")
        thread = QThread(page)
        worker = UpdateProcessPreflightWorker(
            software_id,
            update_config,
            process_plan["all_pids"],
            package_path,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_process_close_finished)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)

        self._pending_process_preflights[software_id] = (
            row_data,
            package_path,
            update_config,
            strategy,
            continuation,
            stylesheet,
        )
        page._preflight_workers[software_id] = worker
        page._preflight_threads[software_id] = thread
        page._set_rows_status(software_id, "preparing")
        page._append_info(f"{software_name}：正在关闭更新前进程，请稍候。")
        update_config["_timing_process_close_started_at"] = time.monotonic()
        thread.start()

    @Slot(str, bool, object)
    @_guard_preflight_result
    def _on_process_close_finished(self, software_id, success, final_plan):
        """接收后台关闭结果，并在主线程继续或恢复更新按钮。

        参数：software_id 标识更新行；success 表示最终重检是否通过；final_plan 是
        重检后的占用数据。返回值：无。副作用：更新状态控件、信息区并继续后续预检。
        """

        context = self._pending_process_preflights.pop(software_id, None)
        page = self._page
        page._preflight_workers.pop(software_id, None)
        page._preflight_threads.pop(software_id, None)
        if context is None:
            return

        row_data, package_path, update_config, strategy, continuation, stylesheet = context
        software_name = row_data.get("name", "")
        close_started = update_config.pop("_timing_process_close_started_at", None)
        if close_started is not None:
            update_config["_timing_process_close_seconds"] = max(0.0, time.monotonic() - close_started)
        for warning in final_plan.get("warnings", []):
            page._append_info(f"{software_name}：{warning}", level="warning")
        if not success:
            error = str(final_plan.get("error", "") or "").strip()
            message = error or _remaining_processes_message(
                final_plan.get("running_configured", []),
                final_plan.get("locked_files", []),
                final_plan.get("system_names", set()),
            )
            page._set_rows_status(software_id, "ready")
            page._append_info(f"{software_name}：{message}", level="warning")
            return

        # 只有最终进程与文件占用重检都通过，才允许 worker 跳过重复关闭。
        update_config["_process_preflight_completed"] = True
        sync_preflight_update_config(row_data, update_config)
        self._continue_after_process_preflight(
            row_data, package_path, update_config, strategy, continuation, stylesheet
        )

    def _continue_after_process_preflight(
        self, row_data, package_path, update_config, strategy, continuation, stylesheet
    ):
        """在主线程完成进程关闭之后的签名、目录和防火墙确认。

        参数同 begin()，continuation 由页面提供。返回值：无。副作用：可能继续显示
        Qt 确认框；全部通过后调用 continuation 启动正式更新 worker。
        """

        page = self._page
        software_name = row_data.get("name", "")

        signature_ok, signature_config_changed = confirm_update_signature(
            package_path,
            update_config,
            software_name,
            page,
            stylesheet,
        )
        if not signature_ok:
            page._append_info(f"{software_name}：数字签名验证失败，已取消更新。", level="warning")
            page._set_rows_status(str(row_data.get("id", "")), "ready")
            return
        if signature_config_changed:
            sync_preflight_update_config(row_data, update_config)

        if strategy != "download_only":
            self._confirm_target_dirs(row_data, update_config, strategy, stylesheet)

        # 防火墙确认：用户选“跳过”时本次不添加防火墙，但其余更新步骤照常执行。
        if not confirm_firewall_rules(update_config, page, stylesheet):
            update_config["add_firewall_rules"] = False
            sync_preflight_update_config(row_data, update_config)

        preflight_started = update_config.pop("_timing_preflight_started_at", None)
        if preflight_started is not None:
            update_config["_timing_preflight_seconds"] = max(0.0, time.monotonic() - preflight_started)
        sync_preflight_update_config(row_data, update_config)
        continuation(row_data, update_config, strategy)

    def _confirm_target_dirs(self, row_data, update_config, strategy, stylesheet):
        """确认目标目录，并把用户决定同步到本次运行配置。

        参数：row_data/update_config 是当前更新数据；strategy 是策略；stylesheet 是主题 QSS。
        返回值：无。
        副作用：显示目录确认弹窗，并可能修改 auto_create_dirs/_create_skip_dirs。
        """
        page = self._page
        check_dirs = collect_update_target_dirs(strategy, update_config, page.software_manager)
        confirmed_dirs, skip_dirs = confirm_update_target_dirs(
            page,
            check_dirs,
            stylesheet,
            lambda text: page._append_info(text, level="warning"),
        )
        if not confirmed_dirs and not skip_dirs:
            return

        # 确认项交给 worker 创建；取消或超时项必须写入跳过列表。
        if confirmed_dirs:
            update_config["auto_create_dirs"] = True
        update_config["_create_skip_dirs"] = list(skip_dirs)
        sync_preflight_update_config(row_data, update_config)
