"""OpenWrt 高级 Pin 参数弹窗骨架。"""

from modules.common_widgets.spin_box import CommonSpinBox as QSpinBox
from modules.common_widgets.spin_box import CommonDoubleSpinBox as QDoubleSpinBox
from PySide6.QtWidgets import QCheckBox, QLineEdit, QHBoxLayout, QWidget
from modules.common_widgets.common_dialogs import apply_common_dialog_theme, MessageDialog
from modules.settings_pages.settings_widgets import create_dialog_ok_cancel_buttons, create_tool_button
from modules.common_widgets.combo_box import CommonComboBox as QComboBox

from modules.common_widgets.frameless_dialog import FramelessDialog
from modules.main_pages.packet_capture.ui_helpers import create_capture_card, create_form_row, create_path_row
from .resource_picker import OpenWrtResourcePicker


class AdvancedWpsDialog(FramelessDialog):
    """收集 MintToolBox 高级 Pin 参数，不在弹窗内启动 Reaver。

    参数：settings 是标准化 wps_defaults 字典；parent 是常规抓包页。
    返回值：get_settings 返回本次弹窗的普通参数字典。
    副作用：只修改内存中的控件；用户确认前不会写配置，也不会访问路由器。
    """

    def __init__(self, settings, parent=None):
        super().__init__("高级 Pin 工具", parent, inherit_parent_stylesheet=False)
        from modules.dialog_theme import detect_light_theme
        apply_common_dialog_theme(self, parent, theme_name="light.qss" if detect_light_theme(parent) else "dark.qss")
        from modules.ui_scale_manager import UiScaleManager
        self.scale_manager = UiScaleManager.for_widget(parent)
        self.light_theme = detect_light_theme(self)
        from modules.main_pages.packet_capture.capture_page_styles import CapturePageStyleManager
        self.setStyleSheet(self.styleSheet() + "\n" + CapturePageStyleManager(self)._capture_stylesheet(detect_light_theme(self)))
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
        self.attack_seconds.setSpecialValueText("\u4e0d\u9650\u5236\uff080 \u79d2\uff09")
        self.attack_seconds.setSuffix(" 秒")
        self.attack_rounds = QSpinBox(card)
        self.attack_rounds.setObjectName("captureSpinBox")
        self.attack_rounds.setRange(1, 20)
        self.attack_rounds.setSuffix(" 轮")
        self.additional_args = QLineEdit(card)
        self.additional_args.setObjectName("captureLineEdit")
        self.spoof_mac = QLineEdit(card)
        self.spoof_mac.setObjectName("captureLineEdit")
        self.spoof_mac.setPlaceholderText("启用后自动生成，也可手动填写")
        self.spoof_mac_enabled = QCheckBox("启用伪装 MAC", card)
        self.spoof_mac_enabled.setObjectName("captureRiskCheckBox")
        self.spoof_mac_enabled.setToolTip("\u5f00\u542f\u540e\u7a7a\u5730\u5740\u81ea\u52a8\u751f\u6210\uff0c\u5df2\u586b\u5199\u7684\u5730\u5740\u4fdd\u7559\u3002\u672a\u52fe\u9009\u9010\u7801\u968f\u673a\u65f6\uff0c\u540c\u4e00\u6279\u4efb\u52a1\u4f7f\u7528\u540c\u4e00\u5730\u5740\uff1b\u4e0d\u4fee\u6539\u76ee\u6807\u8def\u7531\u5668\u7684 MAC\u3002")
        self.rotate_mac_each_pin = QCheckBox("\u9010\u7801\u968f\u673a", card)
        self.rotate_mac_each_pin.setObjectName("captureCheckBox")
        self.rotate_mac_each_pin.setToolTip("\u6bcf\u4e2a\u5019\u9009 PIN \u5f00\u59cb\u524d\u751f\u6210\u65b0 MAC\uff0c\u9996\u4e2a\u5019\u9009\u548c\u505c\u6b62\u540e\u91cd\u542f\u4e5f\u91cd\u65b0\u751f\u6210\u3002\u540c\u4e00\u5019\u9009\u91cd\u8bd5\u4fdd\u6301\u4e00\u81f4\uff1b\u6362\u5740\u4f1a\u589e\u52a0\u51c6\u5907\u8017\u65f6\u3002\u52fe\u9009\u540e\u4e0d\u4f7f\u7528\u5730\u5740\u6846\u5185\u7684\u56fa\u5b9a\u503c\uff1b\u79bb\u7ebf\u8ba1\u7b97\u4e0d\u9002\u7528\u3002")
        self.spoof_mac_row = QWidget(card)
        mac_layout = QHBoxLayout(self.spoof_mac_row)
        mac_layout.setContentsMargins(0, 0, 0, 0)
        mac_layout.addWidget(self.spoof_mac, 1)
        mac_layout.addWidget(self.rotate_mac_each_pin)
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
        self.resume_file.setPlaceholderText("自动保存并恢复；手动导入可留空")
        self.resume_file_button = create_tool_button(":/icons/images/icons/cil-file.png", self)
        self.resume_file_button.setToolTip("自动保存并恢复同一目标的进度；仅在需要手动导入时选择 .wpc 文件。")
        self.mode = QComboBox(card)
        self.mode.setObjectName("captureComboBox")
        self.mode.addItem("Pin", "normal")
        self.mode.addItem("秒 Pin", "quick")
        self.mode.addItem("离线 Pin", "offline")
        for label, control, tip in (
            ("Pin 速率", self.pin_speed, "两次尝试之间的等待时间，不是每秒尝试次数。建议保留 1 秒；不要盲目调小，尝试过快可能被目标限制。"),
            ("超时时间", self.pin_timeout, "等待一次回应的时间，建议保留 5 秒。信号弱、经常超时时可适当调大，但每次等待会更久。"),
            ("攻击时间", self.attack_seconds, "0 表示不限制；常规/离线为每目标每轮上限，秒 Pin 为每个候选的上限。"),
            ("攻击轮数", self.attack_rounds, "秒 Pin 只重复在线候选，离线每个目标只试一次；增加轮数会延长总时间。"),
            ("附加参数", self.additional_args, "高级用户使用。普通用户保留原值，不要粘贴整条命令；不支持的参数会被拒绝。"),
            ("排序 Pin 码", self.pin_order, "不清楚就保留 0123456789。只调整尝试顺序，不是填写已知的 Pin 码；已知部分号码请填在下面。"),
            ("部分 Pin 码", self.partial_pin, "已知 Pin 前 4 位或完整 8 位时填写；不知道就留空。这里不是 Wi-Fi 密码。"),
            ("模式", self.mode, "“秒 Pin”先试 pin24、再试离线 Pin、最后补厂商公式；“Pin”逐个尝试；“离线 Pin”使用 Pixie Dust。只用于自有或已授权网络。"),
        ):
            layout.addLayout(create_form_row(card, label, control, tip))
        layout.addLayout(create_path_row(card, "恢复进度", self.resume_file, self.resume_file_button,
            "需要接着上次任务继续时，选择同一目标保存的 .wpc 文件；第一次使用就留空，不要选其他目标的进度。"))
        layout.addWidget(self.random_order)
        layout.addWidget(self.spoof_mac_enabled)
        self.spoof_mac_form_row = create_form_row(card, "伪装 MAC", self.spoof_mac_row,
            "填写固定地址，或勾选逐码随机。")
        layout.addLayout(self.spoof_mac_form_row)
        self.content_layout.addWidget(card, 1)
        self.ok_button, self.cancel_button = create_dialog_ok_cancel_buttons(self, "确认", "取消")
        self.ok_button.clicked.connect(self.accept)
        self.cancel_button.clicked.connect(self.reject)
        self.button_layout.addWidget(self.ok_button)
        self.button_layout.addWidget(self.cancel_button)
        self._load(settings)
        self.spoof_mac_enabled.toggled.connect(self._on_spoof_mac_toggled)
        self.rotate_mac_each_pin.toggled.connect(self._sync_rotation_controls)
        self.resume_file_button.clicked.connect(
            lambda: self.resource_picker.choose_wpc_file(self.resume_file)
        )
        self._on_spoof_mac_toggled(self.spoof_mac_enabled.isChecked())
        self.attack_seconds.valueChanged.connect(self._sync_mode_controls)
        self.mode.currentIndexChanged.connect(self._on_mode_changed)
        self._sync_mode_controls()

    def _load(self, settings):
        """回显 wps_defaults；参数已由配置管理器标准化。"""

        self.pin_speed.setValue(float(settings.get("pin_speed", 1.0)))
        self.pin_timeout.setValue(float(settings.get("pin_timeout", 5.0)))
        self._normal_seconds = int(settings.get("attack_seconds", 0) or 0)
        self._normal_rounds = int(settings.get("attack_rounds", 1) or 1)
        self._quick_seconds = int(settings.get("quick_seconds", 60) or 0)
        self._quick_rounds = int(settings.get("quick_rounds", 1) or 1)
        self.additional_args.setText(str(settings.get("additional_args", "-S -N -l 30")))
        self.spoof_mac.setText(str(settings.get("spoof_mac", "")))
        self.spoof_mac_enabled.setChecked(bool(settings.get("spoof_mac_enabled", False)))
        self.rotate_mac_each_pin.setChecked(bool(settings.get("rotate_mac_each_pin", False)))
        self.pin_order.setText(str(settings.get("pin_order", "0123456789")))
        self.random_order.setChecked(bool(settings.get("random_order", False)))
        self.partial_pin.setText(str(settings.get("partial_pin", "")))
        resume_value = str(settings.get("resume_file", "")).strip()
        self.resume_file.setText(
            str(self.resource_picker.settings_manager.resolve_path(resume_value)) if resume_value else ""
        )
        self.mode.setCurrentIndex(max(0, self.mode.findData(settings.get("mode", "normal"))))
        self._current_mode = self.mode.currentData()
        self._load_mode_values()

    def _save_mode_values(self):
        if self._current_mode == "quick":
            self._quick_seconds = int(self.attack_seconds.value())
            self._quick_rounds = int(self.attack_rounds.value())
        else:
            self._normal_seconds = int(self.attack_seconds.value())
            self._normal_rounds = int(self.attack_rounds.value())

    def _load_mode_values(self):
        if self._current_mode == "quick":
            self.attack_seconds.setValue(self._quick_seconds)
            self.attack_rounds.setValue(self._quick_rounds)
        else:
            self.attack_seconds.setValue(self._normal_seconds)
            self.attack_rounds.setValue(self._normal_rounds)

    def _on_mode_changed(self, _index=None):
        mode = self.mode.currentData()
        if mode == self._current_mode:
            return
        self._save_mode_values()
        self._current_mode = mode
        self._load_mode_values()
        self._sync_mode_controls()

    def _sync_mode_controls(self, _index=None):
        quick = self.mode.currentData() == "quick"
        self.attack_seconds.setSuffix(" 秒")
        self.attack_rounds.setSuffix(" 轮")
        self.partial_pin.setPlaceholderText("已知完整 8 位可填；留空自动匹配" if quick else "取值：[前4位，全部8位]")
        for control in (self.pin_order, self.random_order,
                        self.resume_file, self.resume_file_button):
            control.setEnabled(not quick)
        self.attack_seconds.setEnabled(True)
        self.attack_rounds.setEnabled(quick or self.attack_seconds.value() > 0)
        self._sync_rotation_controls()

    def _sync_rotation_controls(self, _checked=None):
        enabled = self.spoof_mac_enabled.isChecked()
        self.rotate_mac_each_pin.setEnabled(enabled and self.mode.currentData() != "offline")
        rotating = self.rotate_mac_each_pin.isEnabled() and self.rotate_mac_each_pin.isChecked()
        self.spoof_mac.setEnabled(enabled and not rotating)

    def _on_spoof_mac_toggled(self, enabled):
        from modules.main_pages.packet_capture.ui_helpers import set_row_visible
        set_row_visible(self.spoof_mac_form_row, enabled)
        self.spoof_mac.setEnabled(enabled)
        if enabled and not self.spoof_mac.text().strip():
            from modules.capture_services.openwrt.wps_options import random_source_mac
            self.spoof_mac.setText(random_source_mac())
        self._sync_rotation_controls()

    def accept(self):
        from modules.capture_services.openwrt.wps_options import prepare_wps_options
        try:
            prepared = prepare_wps_options(self.get_settings(), mode={"quick": "second"}.get(self.mode.currentData()))
            if not self.spoof_mac.text().strip():
                self.spoof_mac.setText(prepared.get("spoof_mac", ""))
        except ValueError as exc:
            MessageDialog("Pin 参数不完整", str(exc), self).exec()
            self.spoof_mac.setFocus()
            return
        super().accept()

    def get_settings(self):
        """返回未执行的高级 Pin 参数；秒 Pin 与常规 Pin 各用一组时间/轮数。"""

        if self.mode.currentData() == "quick":
            attack_seconds, attack_rounds = self._normal_seconds, self._normal_rounds
            quick_seconds, quick_rounds = int(self.attack_seconds.value()), int(self.attack_rounds.value())
        else:
            attack_seconds, attack_rounds = int(self.attack_seconds.value()), int(self.attack_rounds.value())
            quick_seconds, quick_rounds = self._quick_seconds, self._quick_rounds
        return {
            "pin_speed": float(self.pin_speed.value()),
            "pin_timeout": float(self.pin_timeout.value()),
            "attack_seconds": attack_seconds,
            "attack_rounds": attack_rounds,
            "quick_seconds": quick_seconds,
            "quick_rounds": quick_rounds,
            "additional_args": self.additional_args.text().strip(),
            "spoof_mac_enabled": self.spoof_mac_enabled.isChecked(),
            "rotate_mac_each_pin": self.rotate_mac_each_pin.isEnabled() and self.rotate_mac_each_pin.isChecked(),
            "spoof_mac": self.spoof_mac.text().strip(),
            "pin_order": self.pin_order.text().strip(),
            "random_order": self.random_order.isChecked(),
            "partial_pin": self.partial_pin.text().strip(),
            "resume_file": self.resume_file.text().strip(),
            "mode": self.mode.currentData(),
        }
