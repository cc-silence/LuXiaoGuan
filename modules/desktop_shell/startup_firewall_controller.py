"""启动防火墙审计的桌面运行时协调器。

平台层只负责检查和创建防火墙规则；本模块负责把耗时操作放入后台线程，并通过 Qt 信号
把活动状态和失败结果交给 ResidentRuntime。它不创建窗口、不显示弹窗，也不依赖页面层。
"""

import threading
import time

from PySide6.QtCore import QObject, Signal

from modules.platform_services.startup_firewall import ensure_startup_firewall_rules


class StartupFirewallController(QObject):
    """异步执行一次启动联网组件防火墙审计。

    返回值：start() 返回 True 表示本次已开始，False 表示已在执行或已完成。
    副作用：后台线程可能请求 UAC 并创建持久防火墙规则；失败信息通过 failed 信号发出。
    调用限制：由 ResidentRuntime 长期持有。调用方必须在 Qt 主线程连接信号，不能在工作线程
    中直接操作窗口或页面控件。
    """

    activity_changed = Signal()
    failed = Signal(str)
    finished = Signal(bool, str)
    stage_changed = Signal(str)

    def __init__(self, parent=None):
        """初始化一次性审计状态，不执行平台操作。"""
        super().__init__(parent)
        self._lock = threading.Lock()
        self._active = False
        self._completed = False
        self._ready = threading.Event()
        self._error = ""
        self._stage = "checking"

    @property
    def active(self):
        """返回防火墙审计线程是否仍在运行，仅供运行时判断空闲释放。"""
        with self._lock:
            return self._active

    @property
    def stage(self):
        with self._lock:
            return self._stage

    def _report_stage(self, stage):
        if stage not in {"checking", "adding"}:
            raise ValueError("Unknown startup firewall stage")
        with self._lock:
            self._stage = stage
        self.stage_changed.emit(stage)

    def start(self):
        """在后台启动一次防火墙审计。

        返回值：True 表示已创建后台线程；False 表示同一进程已经执行或正在执行。
        副作用：触发后台平台调用，并先后发出活动开始和结束信号。
        """
        with self._lock:
            if self._active or self._completed:
                return False
            self._active = True
            self._ready.clear()
            self._error = ""
        self.activity_changed.emit()
        threading.Thread(target=self._run_audit, daemon=True, name="WinBoxStartupFirewallAudit").start()
        return True

    def wait_ready(self, cancel_event, timeout=180):
        """后台登录等待审计完成，支持取消和超时。"""
        self.start()
        deadline = time.monotonic() + timeout
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("登录已取消")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("防火墙检查超时，尚未连接路由器；请检查管理员授权窗口后重试。")
            if self._ready.wait(min(remaining, 0.1)):
                break
        if self._error:
            raise OSError(self._error)

    def _run_audit(self):
        """执行平台审计并把结果转换为主线程可消费的信号。

        返回值：无。
        副作用：调用平台服务；控制台记录排查信息，失败时发出用户可读提示。
        """
        completed = False
        try:
            messages = ensure_startup_firewall_rules(progress=self._report_stage)
            for message in messages:
                print(f"[防火墙] {message}")
            if any("失败" in message for message in messages):
                self._error = "防火墙放行未完成；" + "；".join(messages)
                self.failed.emit("防火墙放行规则未创建。下载时请确认管理员授权或检查 Windows 防火墙设置。")
            else:
                completed = True
        except Exception as exc:
            self._error = "防火墙检查失败：" + str(exc)
            print(f"[防火墙] 启动防火墙规则失败：{exc}")
            self.failed.emit("启动防火墙规则检查失败。下载受阻时请检查 Windows 防火墙设置。")
        finally:
            with self._lock:
                self._active = False
                # UAC 取消或平台失败时允许同一进程后续再次尝试。
                self._completed = completed
                self._ready.set()
            self.finished.emit(completed, self._error)
            self.activity_changed.emit()
