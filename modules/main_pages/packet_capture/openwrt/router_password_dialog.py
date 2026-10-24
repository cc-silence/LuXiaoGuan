"""Small secret-entry dialog for the current router login account."""
from PySide6.QtWidgets import QWidget, QVBoxLayout
from modules.common_widgets.frameless_dialog import FramelessDialog
from modules.common_widgets.common_dialogs import apply_common_dialog_theme
from modules.settings_pages.settings_widgets import create_dialog_ok_cancel_buttons
from .signal_setup_forms import PasswordInput, add_field, hint
from modules.capture_services.openwrt.router_password import PasswordChange


class RouterPasswordDialog(FramelessDialog):
    def __init__(self, account, parent=None):
        super().__init__("修改路由器登录密码", parent, inherit_parent_stylesheet=False)
        apply_common_dialog_theme(self, parent)
        self.set_scaled_size((540, 300))
        self.content_layout.addWidget(hint("当前账号：" + account + "。修改的是路由器登录密码，不是 Wi-Fi 密码。", self))
        body = QWidget(self)
        layout = QVBoxLayout(body)
        self.new_password = PasswordInput(body, "输入新登录密码")
        self.confirm_password = PasswordInput(body, "再次输入新登录密码")
        add_field(layout, body, "新密码", self.new_password, "8～128 字节，不允许换行或控制字符。")
        add_field(layout, body, "确认密码", self.confirm_password, "两次输入必须一致。")
        self.error_label = hint("", body)
        layout.addWidget(self.error_label)
        self.content_layout.addWidget(body)
        self.save_button, cancel = create_dialog_ok_cancel_buttons(self, "修改密码", "取消")
        self.button_layout.addWidget(self.save_button)
        self.button_layout.addWidget(cancel)
        self.save_button.clicked.connect(self._validate)
        cancel.clicked.connect(self.reject)

    def _validate(self):
        password = self.new_password.submitted_text()
        if password != self.confirm_password.submitted_text():
            self.error_label.setText("两次输入的新密码不一致。")
            return
        try:
            request = PasswordChange.create(password, {})
            request.clear()
        except ValueError as exc:
            self.error_label.setText(str(exc))
            return
        self.accept()

    def clear(self):
        self.new_password.clear()
        self.confirm_password.clear()

    def reject(self):
        self.clear()
        super().reject()
