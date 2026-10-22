"""抓包服务层使用的稳定数据模型。"""

from enum import Enum

from modules.platform_services.windows_wifi_capture import (
    WirelessAdapterCapability,
    WindowsCaptureCapabilities,
)


class CaptureSessionState(str, Enum):
    """抓包会话状态。

    页面和常驻运行时只依赖这些稳定状态，不读取具体后端的进程对象。当前架构阶段
    实现环境检测状态；后续实际抓包后端继续复用 STARTING、CAPTURING、STOPPING。
    """

    IDLE = "idle"
    CHECKING = "checking"
    STARTING = "starting"
    CAPTURING = "capturing"
    STOPPING = "stopping"
    FAILED = "failed"



__all__ = [
    "CaptureSessionState",
    "WirelessAdapterCapability",
    "WindowsCaptureCapabilities",
]
