"""OpenWrt 设备探测结果展示。"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class DeviceStatusPanel(QWidget):
    """显示设备版本、架构、频段、内存、存储和 SFTP 状态。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("captureDeviceStatusPanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.value_label = QLabel("尚未连接 OpenWrt。", self)
        self.value_label.setObjectName("captureDescription")
        self.value_label.setWordWrap(True)
        self.value_label.setTextFormat(Qt.PlainText)
        # 允许鼠标/键盘选中文本，方便用户复制设备信息发给技术支持。
        self.value_label.setTextInteractionFlags(
            Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard
        )
        layout.addWidget(self.value_label)

    def apply_snapshot(self, snapshot):
        """根据快照更新设备摘要，不执行任何探测。"""

        device = snapshot.device
        if snapshot.connection_state.value != "connected":
            self.value_label.setText(snapshot.connection_detail or "尚未连接 OpenWrt。")
            return
        radios_lines = []
        for r in device.radios:
            band_text = "/".join(r.bands) or "未知频段"
            if r.free:
                state = "空闲"
            elif r.free is None:
                state = "状态未知"
            elif r.channel:
                state = f"占用(信道 {r.channel})"
            else:
                state = "占用"
            monitor = "支持监听" if r.monitor_supported is True else "当前驱动不支持监听" if r.monitor_supported is False else "监听能力未确认"
            driver = r.driver or "驱动未知"
            radios_lines.append(f"{r.name}({band_text})：{state}；{driver}；{monitor}")
        radios = "\n".join(radios_lines) or "未知"
        memory = "未知" if device.free_memory_mb is None else f"{device.free_memory_mb} MB 可用"
        storage = "未知" if device.free_storage_mb is None else f"{device.free_storage_mb} MB 可用"
        self.value_label.setText(
            f"设备型号：{getattr(device, 'model', '') or '未知'}\n"
            f"硬件标识：{getattr(device, 'board_name', '') or '未知'}\n"
            f"固件发行：{getattr(device, 'distribution', '') or '未知'}\n"
            f"系统版本：{device.version or '未知'}\n"
            f"固件目标：{device.target or '未知'}\n"
            f"CPU 架构：{device.architecture or '未知'}\n"
            f"软件包架构：{device.package_architecture or '未知'}\n"
            f"包管理器：{device.package_manager or '未知'}\n"
            f"内核：{device.kernel_version or '未知'}\n"
            f"无线设备：\n{radios}\n"
            f"内存：{memory}\n存储：{storage}\n"
            f"SFTP：{'可用' if device.sftp_available else '未知/不可用'}"
        )
