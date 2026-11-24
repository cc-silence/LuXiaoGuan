"""Small dialog for the quick WPS time/round settings."""
from PySide6.QtWidgets import QCheckBox, QLabel, QVBoxLayout, QWidget

from modules.common_widgets.common_dialogs import apply_common_dialog_theme
from modules.common_widgets.combo_box import CommonComboBox as QComboBox
from modules.common_widgets.frameless_dialog import FramelessDialog
from modules.common_widgets.spin_box import CommonSpinBox as QSpinBox
from modules.main_pages.page_action_buttons import create_page_text_button
from modules.main_pages.packet_capture.ui_helpers import create_capture_card, create_form_row
from modules.settings_pages.settings_widgets import create_dialog_ok_cancel_buttons


class QuickWpsDialog(FramelessDialog):
    """Collect per-candidate seconds and online rounds before a quick WPS task."""

    UpgradeRequested = 2

    def __init__(self, settings, parent=None, clear_callback=None, *, upgrade_available=False):
        super().__init__("秒 Pin 设置", parent, inherit_parent_stylesheet=False)
        from modules.dialog_theme import detect_light_theme
        apply_common_dialog_theme(
            self, parent, theme_name="light.qss" if detect_light_theme(parent) else "dark.qss")
        from modules.ui_scale_manager import UiScaleManager
        self.scale_manager = UiScaleManager.for_widget(parent)
        from modules.main_pages.packet_capture.capture_page_styles import CapturePageStyleManager
        self.setStyleSheet(
            self.styleSheet() + "\n"
            + CapturePageStyleManager(self)._capture_stylesheet(detect_light_theme(self)))
        self.setObjectName("quickWpsDialog")
        self.resize(560, 420)

        card, layout = create_capture_card(
            self,
            "秒 Pin 参数",
            "先试 pin24，再按发送方式补试其余候选；默认不限制单个候选时长，并固定启用小 DH（-S）和不回 NACK（-N）提速；全部失败后不进入穷举。",
        )
        self.seconds = QSpinBox(card)
        self.seconds.setObjectName("captureSpinBox")
        self.seconds.setRange(0, 3600)
        self.seconds.setSpecialValueText("不限制（0 秒）")
        self.seconds.setSuffix(" 秒")
        self.rounds = QSpinBox(card)
        self.rounds.setObjectName("captureSpinBox")
        self.rounds.setRange(1, 20)
        self.rounds.setSuffix(" 轮")
        self.sender = QComboBox(card)
        self.sender.setObjectName("captureComboBox")
        self.sender.addItem("reaver（原来的方式）", "reaver")
        self.sender.addItem("系统 WPS（和手机同样的方式）", "system")
        layout.addLayout(create_form_row(
            card, "每个候选时间", self.seconds,
            "每个候选 PIN 最多验证多久；默认 0 表示不限制，像秒 Pin 脚本一样等目标给出明确结果。"
            "离线 Pin 和验证已获取的 PIN 也使用这个时间。"))
        layout.addLayout(create_form_row(
            card, "在线轮数", self.rounds,
            "在线候选（pin24、其他公式）重复几轮；离线 Pin 每个目标只试一次。"))
        layout.addLayout(create_form_row(
            card, "发送方式", self.sender,
            "reaver：在路由器上直接发无线帧，兼容性差一些但不需要固件组件。"
            "系统 WPS：让路由器连上去按 WPS 协议谈，和手机做法一致，"
            "但要求固件里的无线组件带 WPS；不支持时会提示改回 reaver。只影响秒 Pin。"))
        self.offline_box = QWidget(card)
        offline_layout = QVBoxLayout(self.offline_box)
        offline_layout.setContentsMargins(0, 0, 0, 0)
        offline_layout.setSpacing(4)
        self.offline_pixie = QCheckBox(
            "同时试一次离线 Pixie（会用 reaver 发帧）", self.offline_box)
        self.offline_pixie.setToolTip(
            "离线 Pixie 仍由 reaver 在路由器上发无线帧完成，部分网卡可能短暂掉线。"
            "系统 WPS 默认不跑；勾选后算出的 PIN 仍用系统 WPS 验证。")
        self.offline_hint = QLabel(
            "默认关闭。开启只会多跑一次离线 Pixie；不会改用 reaver 验证 PIN。",
            self.offline_box,
        )
        self.offline_hint.setObjectName("captureDescription")
        self.offline_hint.setWordWrap(True)
        offline_layout.addWidget(self.offline_pixie)
        offline_layout.addWidget(self.offline_hint)
        layout.addWidget(self.offline_box)
        self.wpad_upgrade_button = create_page_text_button(
            card, "\u5347\u7ea7\u5b8c\u6574\u7248 wpad\uff08\u7cfb\u7edf WPS\uff09",
            "\u628a\u7cbe\u7b80\u65e0\u7ebf\u7ec4\u4ef6\u6362\u6210\u5b8c\u6574\u7248\uff0c\u4f7f\u7528\u7cfb\u7edf WPS\u3002\u70b9\u51fb\u540e\u8fdb\u5165\u5347\u7ea7\u786e\u8ba4\uff0c\u4e0d\u542f\u52a8\u79d2 Pin\u3002")
        self.wpad_upgrade_button.setVisible(bool(upgrade_available))
        self.wpad_upgrade_button.clicked.connect(lambda: self.done(self.UpgradeRequested))
        layout.addWidget(self.wpad_upgrade_button)
        self.sender.currentIndexChanged.connect(self._sync_sender_rows)
        note = QLabel(
            "认得出厂商最多 8 个候选；认不出时最多试 17 个 MAC 公式；"
            "全部失败后不进入穷举。",
            card,
        )
        note.setObjectName("captureDescription")
        note.setWordWrap(True)
        layout.addWidget(note)
        if clear_callback is not None:
            self.clear_button = create_page_text_button(
                card, "清除记住的 PIN", "删除本机凭据库中为所选目标记住的 WPS PIN。")
            self.clear_button.clicked.connect(clear_callback)
            layout.addWidget(self.clear_button)
        else:
            self.clear_button = None
        self.content_layout.addWidget(card, 1)

        self.ok_button, self.cancel_button = create_dialog_ok_cancel_buttons(
            self, "开始", "取消")
        self.ok_button.clicked.connect(self.accept)
        self.cancel_button.clicked.connect(self.reject)
        self.button_layout.addWidget(self.ok_button)
        self.button_layout.addWidget(self.cancel_button)
        self._load(settings)

    def _load(self, settings):
        data = settings if isinstance(settings, dict) else {}
        self.seconds.setValue(int(data.get("quick_seconds", 0) or 0))
        self.rounds.setValue(int(data.get("quick_rounds", 1) or 1))
        sender = str(data.get("wps_sender", "reaver") or "reaver").strip().lower()
        self.sender.setCurrentIndex(max(0, self.sender.findData(sender)))
        self.offline_pixie.setChecked(bool(data.get("system_offline_pixie", False)))
        self._sync_sender_rows()

    def _sync_sender_rows(self, *_args):
        """离线 Pixie 只在系统 WPS 下可选，避免 reaver 模式重复展示开关。"""

        self.offline_box.setVisible(self.sender.currentData() == "system")

    def get_settings(self):
        return {
            "quick_seconds": int(self.seconds.value()),
            "quick_rounds": int(self.rounds.value()),
            "wps_sender": self.sender.currentData(),
            "system_offline_pixie": bool(
                self.sender.currentData() == "system"
                and self.offline_pixie.isChecked()),
        }
