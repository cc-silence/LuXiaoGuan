"""Final human confirmation for configuration restore and factory reset."""
from PySide6.QtWidgets import QVBoxLayout, QWidget, QCheckBox, QLineEdit, QTextEdit
from modules.common_widgets.frameless_dialog import FramelessDialog
from modules.common_widgets.common_dialogs import apply_common_dialog_theme
from modules.settings_pages.settings_widgets import create_dialog_ok_cancel_buttons
from .signal_setup_forms import hint, add_field


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
        self.content_layout.addWidget(hint(message, self))
        self.content_layout.addWidget(hint("开始前会先生成当前配置备份并保存到电脑：" + backup_path
            + "。保存或校验失败就不会继续。配置备份不能找回被重置删除的软件或全部自建文件。", self))
        if reset:
            self.input = QLineEdit(self)
            self.input.setPlaceholderText("输入：恢复出厂")
            body = QWidget(self)
            layout = QVBoxLayout(body)
            add_field(layout, body, "再次确认", self.input, "输入“恢复出厂”后才能执行。")
            self.content_layout.addWidget(body)
        else:
            self.content_layout.addWidget(hint("归档包含 %d 项，大小 %.1f KiB。以下仅显示前 80 项：" %
                (review["entries"], review["size"] / 1024), self))
            listing = QTextEdit(self)
            listing.setObjectName("settingsMessageText")
            listing.setReadOnly(True)
            listing.setPlainText("\n".join(review["files"]))
            self.content_layout.addWidget(listing, 1)
            self.trusted = QCheckBox("我确认备份来自本路由器、适用于当前固件，并理解重启后需重新登录。", self)
            self.content_layout.addWidget(self.trusted)
        self.go, cancel = create_dialog_ok_cancel_buttons(self, "备份后执行重置" if reset else "备份后恢复并重启", "取消")
        self.go.setEnabled(False)
        self.go.setProperty("dangerAction", True)
        self.button_layout.addWidget(self.go)
        self.button_layout.addWidget(cancel)
        if reset:
            self.input.textChanged.connect(lambda text: self.go.setEnabled(text.strip() == "恢复出厂"))
        else:
            self.trusted.toggled.connect(self.go.setEnabled)
        self.go.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
