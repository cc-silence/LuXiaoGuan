"""Small dialog for the quick WPS time/round settings."""
from PySide6.QtWidgets import QLabel

from modules.common_widgets.common_dialogs import apply_common_dialog_theme
from modules.common_widgets.frameless_dialog import FramelessDialog
from modules.common_widgets.spin_box import CommonSpinBox as QSpinBox
from modules.main_pages.page_action_buttons import create_page_text_button
from modules.main_pages.packet_capture.ui_helpers import create_capture_card, create_form_row
from modules.settings_pages.settings_widgets import create_dialog_ok_cancel_buttons


class QuickWpsDialog(FramelessDialog):
    """Collect per-candidate seconds and online rounds before a quick WPS task."""

    def __init__(self, settings, parent=None, clear_callback=None):
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
        self.resize(560, 320)

        card, layout = create_capture_card(
            self,
            "秒 Pin 参数",
            "先试 pin24，再试一次离线 Pin，最后补其余候选。",
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
        layout.addLayout(create_form_row(
            card, "每个候选时间", self.seconds,
            "每个候选 PIN 最多验证多久；0 表示不限制。离线 Pin 和验证已获取的 PIN 也使用这个时间。"))
        layout.addLayout(create_form_row(
            card, "在线轮数", self.rounds,
            "在线候选（pin24、其他公式）重复几轮；离线 Pin 每个目标只试一次。"))
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
        self.seconds.setValue(int(data.get("quick_seconds", 60) or 0))
        self.rounds.setValue(int(data.get("quick_rounds", 1) or 1))

    def get_settings(self):
        return {
            "quick_seconds": int(self.seconds.value()),
            "quick_rounds": int(self.rounds.value()),
        }
