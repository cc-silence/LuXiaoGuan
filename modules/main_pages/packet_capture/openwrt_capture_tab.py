"""OpenWrt 远程 Wi-Fi 抓包标签页。"""

from modules.common_widgets.spin_box import CommonSpinBox as QSpinBox
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from modules.capture_services.backends import CaptureBackendError
from modules.common_widgets.combo_box import CommonComboBox as QComboBox
from modules.common_widgets.bounce_scroll_area import BounceScrollArea
from modules.common_widgets.common_dialogs import MessageDialog
from modules.main_pages.page_action_buttons import create_page_text_button
from .ui_helpers import create_capture_card, create_form_row


class OpenWrtCaptureTab(QWidget):
    """收集并持久化 OpenWrt 连接和抓包策略设置。

    参数：capture_service 负责校验及后续 SSH 后端；settings_manager 负责持久化；
    settings 是已标准化配置。密码只保存在当前控件内，不写入 JSON。
    信号 settingsChanged 在普通设置修改后发出，让主页面统一保存。
    """

    settingsChanged = Signal()

    def __init__(self, capture_service, settings_manager, settings, parent=None):
        super().__init__(parent)
        self.capture_service = capture_service
        self.settings_manager = settings_manager
        self.setObjectName("openwrtCaptureTab")
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll_area = BounceScrollArea(self)
        self.scroll_area.setObjectName("captureScrollArea")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(BounceScrollArea.NoFrame)
        content = QWidget(self.scroll_area)
        content.setObjectName("openwrtCaptureContent")
        root = QVBoxLayout(content)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        self.scroll_area.setWidget(content)
        outer_layout.addWidget(self.scroll_area)

        connection_card, connection_layout = create_capture_card(
            self,
            "SSH 连接",
            "Windows 只负责控制和保存结果；扫描、PMKID、握手与 WPS 操作在已授权的 OpenWrt 设备上执行。密码不会保存到配置文件。",
        )
        self.host_edit = QLineEdit(connection_card)
        self.host_edit.setObjectName("captureLineEdit")
        self.port_spin = QSpinBox(connection_card)
        self.port_spin.setObjectName("captureSpinBox")
        self.port_spin.setRange(1, 65535)
        self.user_edit = QLineEdit(connection_card)
        self.user_edit.setObjectName("captureLineEdit")
        self.password_edit = QLineEdit(connection_card)
        self.password_edit.setObjectName("captureLineEdit")
        self.password_edit.setEchoMode(QLineEdit.Password)
        self.password_edit.setPlaceholderText("仅保留到当前 \u8def\u5c0f\u7ba1 进程退出")
        self.password_edit.setText(self.capture_service.openwrt_password())
        connection_layout.addLayout(create_form_row(connection_card, "地址", self.host_edit, "OpenWrt 的 LAN IP 或可解析主机名。"))
        connection_layout.addLayout(create_form_row(connection_card, "SSH 端口", self.port_spin, "SSH 端口范围 1-65535，默认 22。"))
        connection_layout.addLayout(create_form_row(connection_card, "用户名", self.user_edit, "推荐使用具备抓包工具和无线配置权限的账户。"))
        connection_layout.addLayout(create_form_row(connection_card, "密码", self.password_edit, "密码不写入 capture_settings.json，退出后自动丢弃。"))
        self.validate_button = create_page_text_button(self, "检查连接设置", "只校验地址、端口和用户名，不连接路由器。")
        connection_layout.addWidget(self.validate_button)
        root.addWidget(connection_card)

        policy_card, policy_layout = create_capture_card(
            self,
            "抓包策略",
            "默认只被动等待握手。主动断连会中断目标客户端网络，必须在自有或明确授权的网络中手动开启。",
        )
        self.band_combo = QComboBox(policy_card)
        self.band_combo.setObjectName("captureComboBox")
        self.band_combo.addItems(["2.4G", "5G"])
        self.scan_spin = QSpinBox(policy_card)
        self.scan_spin.setObjectName("captureSpinBox")
        self.scan_spin.setRange(5, 120)
        self.scan_spin.setSuffix(" 秒")
        self.timeout_spin = QSpinBox(policy_card)
        self.timeout_spin.setObjectName("captureSpinBox")
        self.timeout_spin.setRange(0, 3600)
        self.timeout_spin.setSuffix(" 秒")
        self.deauth_check = QCheckBox("允许主动断连（默认开启）", policy_card)
        self.deauth_check.setObjectName("captureRiskCheckBox")
        self.deauth_check.setToolTip("开启后可能中断目标客户端网络；后续执行前仍会要求再次确认授权和影响。")
        self.install_check = QCheckBox("允许通过 opkg 在线安装缺失依赖", policy_card)
        self.install_check.setObjectName("captureCheckBox")
        self.install_check.setToolTip("登录后先探测系统版本、架构和软件源；真正安装前必须再次确认。")
        policy_layout.addLayout(create_form_row(policy_card, "工作频段", self.band_combo, "路由器必须有支持该频段和监控模式的 radio。"))
        policy_layout.addLayout(create_form_row(policy_card, "信号扫描时间", self.scan_spin, "扫描周边 Wi-Fi 的持续时间，推荐 24 秒。"))
        policy_layout.addLayout(create_form_row(policy_card, "无新结果超时", self.timeout_spin, "指定时间内没有新握手时自动结束；0 表示不自动结束。"))
        policy_layout.addWidget(self.deauth_check)
        policy_layout.addWidget(self.install_check)
        root.addWidget(policy_card)

        actions_card, actions_layout = create_capture_card(
            self,
            "功能阶段",
            "设备自动探测完成后，将按顺序开放信号扫描、PMKID、定向握手、WPS Pin 和结果同步。",
        )
        action_row = QHBoxLayout()
        self.detect_device_button = create_page_text_button(self, "连接并检测设备", "SSH 后端接入后用于读取 OpenWrt 版本、架构、radio 和驱动。")
        self.scan_button = create_page_text_button(self, "扫描 Wi-Fi", "设备检测和扫描后端完成后启用。")
        self.capture_button = create_page_text_button(self, "抓取握手", "选择扫描目标后启用。")
        self.wps_button = create_page_text_button(self, "WPS Pin", "设备和依赖支持时启用。")
        for button in (self.detect_device_button, self.scan_button, self.capture_button, self.wps_button):
            button.setEnabled(False)
            action_row.addWidget(button)
        actions_layout.addLayout(action_row)
        root.addWidget(actions_card)
        root.addStretch(1)

        self.load_settings(settings)
        self.validate_button.clicked.connect(self._validate_settings)
        for widget in (self.host_edit, self.user_edit):
            widget.editingFinished.connect(self.settingsChanged)
        # Qt 数值/文本信号会携带参数，统一丢弃后发出无参数业务信号，避免不同 PySide
        # 版本对 signal-to-signal 参数兼容规则不同。
        self.port_spin.lineEdit().editingFinished.connect(self.settingsChanged)
        self.band_combo.currentTextChanged.connect(lambda _text: self.settingsChanged.emit())
        self.scan_spin.lineEdit().editingFinished.connect(self.settingsChanged)
        self.timeout_spin.lineEdit().editingFinished.connect(self.settingsChanged)
        self.deauth_check.toggled.connect(lambda _checked: self.settingsChanged.emit())
        self.install_check.toggled.connect(lambda _checked: self.settingsChanged.emit())
        self.password_edit.textChanged.connect(self.capture_service.set_openwrt_password)

    def load_settings(self, settings):
        """从标准化配置回显控件；不会回显或读取密码。"""

        data = settings if isinstance(settings, dict) else {}
        self.host_edit.setText(str(data.get("host", "")))
        self.port_spin.setValue(int(data.get("port", 22)))
        self.user_edit.setText(str(data.get("user", "root")))
        self.band_combo.setCurrentText(str(data.get("band", "2.4G")))
        self.scan_spin.setValue(int(data.get("scan_seconds", 24)))
        self.timeout_spin.setValue(int(data.get("idle_timeout_seconds", 180)))
        self.deauth_check.setChecked(bool(data.get("active_deauth", False)))
        self.install_check.setChecked(bool(data.get("online_dependency_install", True)))

    def collect_settings(self):
        """返回可落盘的普通设置字典，明确排除密码字段。"""

        return {
            "host": self.host_edit.text().strip(),
            "port": self.port_spin.value(),
            "user": self.user_edit.text().strip(),
            "band": self.band_combo.currentText(),
            "scan_seconds": self.scan_spin.value(),
            "idle_timeout_seconds": self.timeout_spin.value(),
            "active_deauth": self.deauth_check.isChecked(),
            "online_dependency_install": self.install_check.isChecked(),
        }

    def runtime_password(self):
        """返回当前内存中的 SSH 密码；调用方不得写入配置或日志。"""

        return self.password_edit.text()

    def _validate_settings(self):
        """在主线程展示本地校验结果，不联网、不执行 SSH。"""

        try:
            result = self.capture_service.validate_openwrt_settings(self.collect_settings())
        except CaptureBackendError as exc:
            MessageDialog("OpenWrt 设置有误", str(exc), self).exec()
            return
        MessageDialog(
            "OpenWrt 设置可用",
            f"地址：{result['host']}\n端口：{result['port']}\n用户：{result['user']}\n\n当前只完成本地配置校验，尚未连接或修改路由器。",
            self,
        ).exec()
