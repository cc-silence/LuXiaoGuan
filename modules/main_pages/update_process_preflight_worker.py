"""更新前进程关闭后台 worker。

主要作用：在 QThread 中执行确认后的进程关闭、必要的 UAC 提权和文件占用重检，
避免主线程在等待进程退出时卡住。此模块不创建对话框或操作页面控件；结果必须
通过信号回到主线程，由预检控制器继续显示签名/目录等确认。
"""

import time
from PySide6.QtCore import QObject, Signal, Slot
from modules.update_services.signature_verification import RECEIPT_KEY, RESULT_KEY, verify_signature

from modules.update_services.process_preflight import build_process_close_plan, close_confirmed_processes


class UpdateProcessPreflightWorker(QObject):
    """异步关闭一轮已确认的更新前进程。

    参数：software_id 是更新行标识；config 是本次运行配置；pids 为 None 时只扫描
    进程和文件占用，传入 PID 列表时关闭已确认的进程。返回值通过 scan_finished 或
    finished 信号发送。副作用：关闭模式会结束目标进程、可能触发一次 Windows UAC；
    不会直接更新 Qt 界面。
    """

    finished = Signal(str, bool, object)
    scan_finished = Signal(str, object)

    def __init__(self, software_id, config, pids=None, package_path=None):
        """保存本轮预检参数，不修改页面持有的软件配置。

        参数：software_id 是更新行标识；config 是标准化运行配置；pids 为 None
        表示只扫描，否则关闭并重检；package_path 仅供 ZIP 清单精确锁扫描使用。
        返回值：无。副作用：只复制内存配置，不执行进程或文件操作。
        """

        super().__init__()
        self._software_id = str(software_id or "")
        self._config = dict(config)
        if package_path:
            self._config["_preflight_package_path"] = str(package_path)
        self._pids = None if pids is None else list(pids)

    @Slot()
    def run(self):
        """在线程中扫描或关闭进程，并发送结果。

        无输入参数。扫描模式通过 scan_finished 发送计划；关闭模式通过 finished
        发送最终重检结果。异常也转换为失败计划，保证 QThread 一定能退出，页面
        不会停留在“正在检查”或“正在关闭”状态。
        """

        try:
            if self._pids is None:
                plan = build_process_close_plan(self._config)
                if self._config.get("verify_signature"):
                    started = time.monotonic()
                    self._config.pop(RECEIPT_KEY, None)
                    plan[RESULT_KEY] = verify_signature(
                        self._config.get("_preflight_package_path", ""), self._config,
                        str(self._config.get("_preflight_software_name", "")),
                    )
                    plan[RECEIPT_KEY] = self._config.get(RECEIPT_KEY)
                    plan["_timing_signature_seconds"] = time.monotonic() - started
                self.scan_finished.emit(self._software_id, plan)
                return
            success, plan = close_confirmed_processes(self._config, self._pids)
        except Exception as exc:
            plan = {
                "running_configured": [],
                "locked_files": [],
                "all_pids": [],
                "system_names": set(),
                "error": str(exc),
            }
            if self._pids is None:
                self.scan_finished.emit(self._software_id, plan)
                return
            success = False
        self.finished.emit(self._software_id, success, plan)
