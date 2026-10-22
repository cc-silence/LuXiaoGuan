"""OpenWrt 高级 Pin 参数弹窗骨架。"""

from modules.common_widgets.spin_box import CommonSpinBox as QSpinBox
from modules.common_widgets.spin_box import CommonDoubleSpinBox as QDoubleSpinBox
from PySide6.QtWidgets import QCheckBox, QLineEdit, QPushButton
from modules.common_widgets.combo_box import CommonComboBox as QComboBox

from modules.common_widgets.frameless_dialog import FramelessDialog
from modules.main_pages.page_action_buttons import create_page_icon_button
from modules.main_pages.packet_capture.ui_helpers import create_capture_card, create_form_row, create_path_row
from .resource_picker import OpenWrtResourcePicker


class AdvancedWpsDialog(FramelessDialog):
    """收集 MintToolBox 高级 Pin 参数，不在弹窗内启动 Reaver。

    参数：settings 是标准化 wps_defaults 字典；parent 是常规抓包页。
    返回值：get_settings 返回本次弹窗的普通参数字典。
    副作用：只修改内存中的控件；用户确认前不会写配置，也不会访问路由器。
    """

    def __init__(self, settings, parent=None):
        super().__init__("高级 Pin 工具", parent, inherit_parent_stylesheet=True)
        self.setObjectName("advancedWpsDialog")
        self.resize(680, 460)
        self.resource_picker = OpenWrtResourcePicker(self)
        card, layout = create_capture_card(
            self,
            "Pin 参数",
            "不熟悉的选项请保留默认值。确认后保存设置并进入任务启动流程，取消则不保存。",
        )
        self.pin_speed = QDoubleSpinBox(card)
        self.pin_speed.setObjectName("captureSpinBox")
        self.pin_speed.setRange(0, 100)
        self.pin_speed.setDecimals(1)
        self.pin_speed.setSuffix(" 秒")
        self.pin_timeout = QDoubleSpinBox(card)
        self.pin_timeout.setObjectName("captureSpinBox")
        self.pin_timeout.setRange(0, 100)
        self.pin_timeout.setDecimals(1)
        self.pin_timeout.setSuffix(" 秒")
        self.attack_seconds = QSpinBox(card)
        self.attack_seconds.setObjectName("captureSpinBox")
        self.attack_seconds.setRange(0, 3600)
        self.attack_seconds.setSuffix(" 秒")
        self.attack_rounds = QSpinBox(card)
        self.attack_rounds.setObjectName("captureSpinBox")
        self.attack_rounds.setRange(1, 20)
        self.attack_rounds.setSuffix(" 轮")
        self.additional_args = QLineEdit(card)
        self.additional_args.setObjectName("captureLineEdit")
        self.spoof_mac = QLineEdit(card)
        self.spoof_mac.setObjectName("captureLineEdit")
        self.spoof_mac.setPlaceholderText("00:00:00:00:00:00")
        self.spoof_mac_enabled = QCheckBox("启用伪装 MAC", card)
        self.spoof_mac_enabled.setObjectName("captureRiskCheckBox")
        self.spoof_mac_enabled.setToolTip("通常不勾选。开启后本次任务使用你填写的网卡地址，填错可能导致任务失败；不会更改目标路由器的地址。")
        self.pin_order = QLineEdit(card)
        self.pin_order.setObjectName("captureLineEdit")
        self.random_order = QCheckBox("随机排序", card)
        self.random_order.setObjectName("captureCheckBox")
        self.random_order.setToolTip("通常保持关闭，按设置的顺序尝试。开启后打乱尝试顺序，不保证更快找到结果。")
        self.partial_pin = QLineEdit(card)
        self.partial_pin.setObjectName("captureLineEdit")
        self.partial_pin.setPlaceholderText("取值：[前4位，全部8位]")
        self.resume_file = QLineEdit(card)
        self.resume_file.setObjectName("captureLineEdit")
        self.resume_file.setPlaceholderText("选择 .wpc 文件")
        self.resume_file_button = create_page_icon_button(
            self,
            ":/icons/images/icons/cil-file.png",
            "选择 Windows 本地 .wpc 进度恢复文件。",
        )
        self.mode = QComboBox(card)
        self.mode.setObjectName("captureComboBox")
        self.mode.addItem("Pin", "normal")
        self.mode.addItem("秒 Pin", "quick")
        self.mode.addItem("离线 Pin", "offline")
        for label, control, tip in (
            ("Pin 速率", self.pin_speed, "两次尝试之间的等待时间，不是每秒尝试次数。建议保留 1 秒；不要盲目调小，尝试过快可能被目标限制。"),
            ("超时时间", self.pin_timeout, "等待一次回应的时间，建议保留 5 秒。信号弱、经常超时时可适当调大，但每次等待会更久。"),
            ("攻击时间", self.attack_seconds, "每轮最多尝试多久，建议先用 60 秒。调大可等待更久，也会更长时间占用网卡；不要把 0 当成无限运行。"),
            ("攻击轮数", self.attack_rounds, "建议先用 1 轮。增加轮数会重复尝试并延长总时间，不保证得到结果。"),
            ("附加参数", self.additional_args, "高级用户使用。普通用户保留原值，不要粘贴整条命令；不支持的参数会被拒绝。"),
            ("伪装 MAC", self.spoof_mac, "仅勾选“启用伪装 MAC”后使用。没有明确需要就不改，保持该开关关闭。"),
            ("排序 Pin 码", self.pin_order, "不清楚就保留 0123456789。只调整尝试顺序，不是填写已知的 Pin 码；已知部分号码请填在下面。"),
            ("部分 Pin 码", self.partial_pin, "已知 Pin 前 4 位或完整 8 位时填写；不知道就留空。这里不是 Wi-Fi 密码。"),
            ("模式", self.mode, "“秒 Pin”先尝试常见号码，不保证很快成功；“Pin”按顺序尝试，可能很久；“离线 Pin”仅部分设备支持。只用于自有或已授权网络。"),
        ):
            layout.addLayout(create_form_row(card, label, control, tip))
        layout.addLayout(create_path_row(card, "恢复进度", self.resume_file, self.resume_file_button,
            "需要接着上次任务继续时，选择同一目标保存的 .wpc 文件；第一次使用就留空，不要选其他目标的进度。"))
        layout.addWidget(self.spoof_mac_enabled)
        layout.addWidget(self.random_order)
        self.content_layout.addWidget(card, 1)
        self.ok_button = QPushButton("确认", self)
        self.ok_button.setObjectName("settingsPrimaryButton")
        self.cancel_button = QPushButton("取消", self)
        self.cancel_button.setObjectName("settingsSecondaryButton")
        self.ok_button.clicked.connect(self.accept)
        self.cancel_button.clicked.connect(self.reject)
        self.button_layout.addWidget(self.ok_button)
        self.button_layout.addWidget(self.cancel_button)
        self._load(settings)
        self.spoof_mac_enabled.toggled.connect(self.spoof_mac.setEnabled)
        self.resume_file_button.clicked.connect(
            lambda: self.resource_picker.choose_wpc_file(self.resume_file)
        )
        self.spoof_mac.setEnabled(self.spoof_mac_enabled.isChecked())

    def _load(self, settings):
        """回显 wps_defaults；参数已由配置管理器标准化。"""

        self.pin_speed.setValue(float(settings.get("pin_speed", 1.0)))
        self.pin_timeout.setValue(float(settings.get("pin_timeout", 5.0)))
        self.attack_seconds.setValue(int(settings.get("attack_seconds", 60)))
        self.attack_rounds.setValue(int(settings.get("attack_rounds", 1)))
        self.additional_args.setText(str(settings.get("additional_args", "-S -N -l 30")))
        self.spoof_mac.setText(str(settings.get("spoof_mac", "")))
        self.spoof_mac_enabled.setChecked(bool(settings.get("spoof_mac_enabled", False)))
        self.pin_order.setText(str(settings.get("pin_order", "0123456789")))
        self.random_order.setChecked(bool(settings.get("random_order", False)))
        self.partial_pin.setText(str(settings.get("partial_pin", "")))
        resume_value = str(settings.get("resume_file", "")).strip()
        self.resume_file.setText(
            str(self.resource_picker.settings_manager.resolve_path(resume_value)) if resume_value else ""
        )
        self.mode.setCurrentIndex(max(0, self.mode.findData(settings.get("mode", "normal"))))

    def get_settings(self):
        """返回未执行的高级 Pin 参数。"""

        return {
            "pin_speed": float(self.pin_speed.value()),
            "pin_timeout": float(self.pin_timeout.value()),
            "attack_seconds": self.attack_seconds.value(),
            "attack_rounds": self.attack_rounds.value(),
            "additional_args": self.additional_args.text().strip(),
            "spoof_mac_enabled": self.spoof_mac_enabled.isChecked(),
            "spoof_mac": self.spoof_mac.text().strip(),
            "pin_order": self.pin_order.text().strip(),
            "random_order": self.random_order.isChecked(),
            "partial_pin": self.partial_pin.text().strip(),
            "resume_file": self.resume_file.text().strip(),
            "mode": self.mode.currentData(),
        }
