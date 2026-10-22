"""抓包后端的最小公共协议。"""

from abc import ABC, abstractmethod


class CaptureBackendError(RuntimeError):
    """后端无法完成探测、启动或停止操作时抛出的结构化业务错误。"""


class CaptureBackend(ABC):
    """Windows 本机和 OpenWrt 抓包后端必须遵守的生命周期协议。

    后端不创建 QWidget/QDialog，不保存页面引用。耗时方法由 CaptureService 放入后台
    线程执行；实现必须支持幂等 stop，避免退出 \u8def\u5c0f\u7ba1 时重复清理造成二次破坏。
    """

    key = ""

    @abstractmethod
    def is_running(self):
        """返回后端是否持有正在运行的抓包会话，无副作用。"""

    @abstractmethod
    def stop(self):
        """请求后端安全停止当前会话并完成必要清理。"""
