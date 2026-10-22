"""Windows 本机 Wi-Fi 握手抓取后端骨架。"""

from modules.capture_services.backends.base import CaptureBackend
from modules.platform_services.windows_wifi_capture import probe_windows_wifi_capture


class WindowsWifiCaptureBackend(CaptureBackend):
    """封装 Windows 能力探测，并为后续 dumpcap 会话保留稳定边界。

    当前实现只执行无副作用的环境探测，不切换网卡模式、不启动抓包。后续加入实际
    dumpcap 进程时，进程句柄和正常停止逻辑必须继续留在本类，不能进入页面。
    """

    key = "windows_wifi"

    def __init__(self):
        self._running = False

    def probe(self, cancel_event=None):
        """读取 Windows 驱动、Npcap 和 Wireshark 工具状态并返回能力快照。

        cancel_event 由常驻服务在退出时设置，使正在运行的 netsh 可以及时终止。
        """

        return probe_windows_wifi_capture(cancel_event=cancel_event)

    def is_running(self):
        """返回是否存在本机抓包进程；当前探测阶段恒为 False。"""

        return self._running

    def stop(self):
        """幂等停止入口；当前没有进程，只清除运行标记。"""

        self._running = False
