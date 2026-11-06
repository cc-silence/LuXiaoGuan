"""Final human confirmation for configuration restore and factory reset."""
from PySide6.QtWidgets import QVBoxLayout, QWidget, QCheckBox, QTextEdit
from modules.common_widgets.frameless_dialog import FramelessDialog
from modules.common_widgets.common_dialogs import apply_common_dialog_theme, _create_dialog_button
from modules.settings_pages.settings_widgets import create_dialog_ok_cancel_buttons
from .signal_setup_forms import hint, add_field
from .settings_impacts import DISCONNECT_WARNING


class MaintenanceReviewDialog(FramelessDialog):
    def __init__(self, review, backup_path, parent=None):
        reset = review["operation"] == "reset"
        super().__init__("确认恢复出厂设置" if reset else "确认恢复配置", parent, inherit_parent_stylesheet=False)
        apply_common_dialog_theme(self, parent)
        self.set_scaled_size((620, 510))
        self.content_layout.addWidget(hint("当前设备：" + review["model"] + " · " + review["version"], self))
        message = ("将删除当前设置和后来安装的软件，回到本次固件的初始状态。不是恢复成路由器品牌的原厂系统。"
                   "Wi-Fi、上网配置和登录密码会改变，操作后会重启。"
                   if reset else "将用备份覆盖配置，随后重启。管理地址、登录密码和 Wi-Fi 都可能改变；"
                   "不会先清空现有文件，也不会恢复已安装的软件。请仅使用本路由器相同固件的可信备份。")
        self.warning_text = QTextEdit(self)
        self.warning_text.setObjectName("settingsMessageText")
        self.warning_text.setProperty("connectionWarning", True)
        self.warning_text.setReadOnly(True)
        backup_note = "选择“备份后执行”会先将当前配置保存到电脑，保存或校验失败就不会继续。选择“直接执行”将不生成当前配置备份。"
        warning = DISCONNECT_WARNING + "\n\n" + message + "\n\n" + backup_note
        warning += "配置备份不能找回被重置删除的软件或全部自建文件。"
        self.warning_text.setPlainText(warning)
        self.content_layout.addWidget(self.warning_text, 2)
        if reset:
            self.confirm_reset = QCheckBox("我已了解上述风险，确认恢复出厂设置", self)
            body = QWidget(self)
            layout = QVBoxLayout(body)
            add_field(layout, body, "再次确认", self.confirm_reset, "勾选后才能执行恢复出厂设置。")
            self.content_layout.addWidget(body)
        else:
            self.content_layout.addWidget(hint("归档包含 %d 项，大小 %.1f KiB。以下仅显示前 80 项：" %
                (review["entries"], review["size"] / 1024), self))
            listing = QTextEdit(self)
            listing.setObjectName("settingsMessageText")
            listing.setReadOnly(True)
            listing.setPlainText("\n".join(review["files"]))
            self.content_layout.addWidget(listing, 1)
        self.backup_requested = False
        self.go, cancel = create_dialog_ok_cancel_buttons(self, "备份后执行重置" if reset else "备份后恢复并重启", "取消")
        self.direct_button = _create_dialog_button("直接执行重置" if reset else "直接恢复并重启", self, "settingsSecondaryButton")
        for button in (self.direct_button, self.go):
            button.setEnabled(not reset)
            button.setProperty("dangerAction", True)
            self.button_layout.addWidget(button)
        self.button_layout.addWidget(cancel)
        if reset:
            self.confirm_reset.toggled.connect(self.go.setEnabled)
            self.confirm_reset.toggled.connect(self.direct_button.setEnabled)
        self.go.clicked.connect(lambda: self._choose(True))
        self.direct_button.clicked.connect(lambda: self._choose(False))
        cancel.clicked.connect(self.reject)

    def _choose(self, backup):
        self.backup_requested = backup
        self.accept()

    def accept(self):
        confirmation = getattr(self, "confirm_reset", None)
        if confirmation is not None and not confirmation.isChecked():
            return
        super().accept()
