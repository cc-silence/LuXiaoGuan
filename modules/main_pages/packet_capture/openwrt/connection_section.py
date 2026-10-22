"""OpenWrt 抓包设置页的 SSH 连接分组。"""

from modules.common_widgets.spin_box import CommonSpinBox as QSpinBox
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QLineEdit, QPushButton
from modules.common_widgets.combo_box import CommonComboBox as QComboBox

from modules.dialog_theme import detect_light_theme
from modules.icon_utils import get_theme_icon
from modules.main_pages.packet_capture.ui_helpers import (
    create_capture_card,
    create_form_row,
    create_path_row,
    set_row_visible,
)
from modules.main_pages.page_action_buttons import create_page_icon_button


class ConnectionSection:
    """构建 SSH 设置控件，并通过 signal_callback 把用户动作交给页面。

    本对象只保存控件引用和布局，不导入 Paramiko、不连接网络、不保存密码。
    """

    def __init__(
        self,
        parent,
        settings,
        action_callback,
        password_callback=None,
        remember_callback=None,
        identity_callback=None,
    ):
        self._initializing = True
        self._action_callback_set_password = password_callback or (lambda _value: None)
        self._remember_callback = remember_callback or (lambda: None)
        self._identity_callback = identity_callback or (lambda: None)
        self.card, self.layout = create_capture_card(
            parent,
            "SSH 设置",
            "连接自有或已授权的 OpenWrt 路由器。建议用网线连接，避免抓包时无线中断影响操作。",
        )
        self.host = QLineEdit(self.card)
        self.host.setObjectName("captureLineEdit")
        self.port = QSpinBox(self.card)
        self.port.setObjectName("captureSpinBox")
        self.port.setRange(1, 65535)
        self.user = QLineEdit(self.card)
        self.user.setObjectName("captureLineEdit")
        self.password = QLineEdit(self.card)
        self.password.setObjectName("captureLineEdit")
        self.password.setEchoMode(QLineEdit.Password)
        self.password_toggle = create_page_icon_button(
            self.card,
            ":/icons/images/icons/cil-low-vision.png",
            "显示密码",
        )
        self.password_toggle.setCheckable(True)
        self.remember_password = QCheckBox("记住密码", self.card)
        self.remember_password.setObjectName("captureCheckBox")
        self.remember_password.setToolTip(
            "自己的电脑可勾选，下次连接不用重新输入密码；公用电脑不要勾选。密码由 Windows 为当前用户保存，不写入普通配置文件；取消勾选会删除已记住的密码。"
        )
        self.auth_method = QComboBox(self.card)
        self.auth_method.setObjectName("captureComboBox")
        self.auth_method.addItem("密码", "password")
        self.auth_method.addItem("私钥", "private_key")
        self.private_key = QLineEdit(self.card)
        self.private_key.setObjectName("captureLineEdit")
        self.private_key.setPlaceholderText("选择 SSH 私钥文件")
        self.private_key_button = create_page_icon_button(
            self.card,
            ":/icons/images/icons/cil-file.png",
            "选择 Windows 本地 SSH 私钥文件。",
        )
        self.login_button = QPushButton("登录", self.card)
        self.login_button.setObjectName("captureSecondaryButton")
        self.login_button.setToolTip(
            "用上面的地址账号连接 OpenWrt，顺带查出版本、无线网卡和已装软件。\n"
            "连上后按钮变“登出”；抓包、装软件都必须先登录。"
        )
        self.fingerprint_check = QCheckBox("保存此设备的主机指纹", self.card)
        self.fingerprint_check.setObjectName("captureCheckBox")
        self.fingerprint_check.setChecked(False)
        self.fingerprint_check.setToolTip(
            "指纹用于识别是不是同一台路由器。常用设备可勾选，首次确认后记住身份；以后身份变化会提醒你核对，确认前不连接。重装路由器系统后指纹可能改变。"
        )
        self.layout.addLayout(create_form_row(self.card, "地址", self.host, "填写要连接的 OpenWrt 路由器地址，通常与打开路由器管理页面时使用的地址相同，不要填写 Wi-Fi 名称。"))
        self.layout.addLayout(create_form_row(self.card, "SSH 端口", self.port, "通常保留 22。只有在路由器上修改过 SSH 连接端口时，才填写修改后的数字；不是网页管理端口。"))
        self.layout.addLayout(create_form_row(self.card, "用户", self.user, "通常填写 root。若使用其他账号，需要让它具备管理无线网络和运行抓包软件的权限。"))
        self.layout.addLayout(create_form_row(self.card, "认证方式", self.auth_method, "普通用户推荐密码认证；已在路由器配置公钥时可选择私钥认证。"))
        self.password_row = create_path_row(
            self.card,
            "密码",
            self.password,
            self.password_toggle,
            "填写路由器管理账号的 SSH 登录密码，不是要测试的 Wi-Fi 密码。不会写入普通配置或日志；需要下次自动填写时勾选“记住密码”。",
        )
        self.private_key_row = create_path_row(
            self.card,
            "私钥文件",
            self.private_key,
            self.private_key_button,
            "只有已配置密钥登录时才选择对应的私钥文件，目前不支持带密码保护的私钥。不清楚这个文件是什么，请改用“密码”认证。",
        )
        self.layout.addLayout(self.password_row)
        self.layout.addWidget(self.remember_password)
        self.layout.addLayout(self.private_key_row)
        self.layout.addWidget(self.fingerprint_check)
        self.layout.addWidget(self.login_button)
        self.login_button.clicked.connect(lambda: action_callback("connect"))
        # 密码输入完成后按回车直接走与登录按钮相同的主线程校验和连接流程，
        # 不在控件回调中直接建立 SSH 连接。
        self.password.returnPressed.connect(lambda: action_callback("connect"))
        self.password_toggle.toggled.connect(self._toggle_password_visibility)
        self.auth_method.currentIndexChanged.connect(self._sync_authentication_fields)
        self.remember_password.toggled.connect(lambda _checked: self._remember_callback())
        self.fingerprint_check.toggled.connect(lambda _checked: action_callback("fingerprint_preference_changed"))
        self.host.editingFinished.connect(self._identity_callback)
        self.port.lineEdit().editingFinished.connect(self._identity_callback)
        self.user.editingFinished.connect(self._identity_callback)
        # 密码仅同步到 CaptureService 当前进程内存，不进入 collect() 或配置文件。
        self.password.textChanged.connect(self._action_callback_set_password)
        self.load(settings)
        self._initializing = False

    def load(self, settings):
        """从配置回显非敏感 SSH 字段，不回显密码。"""

        self.host.setText(str(settings.get("host", "")))
        self.port.setValue(int(settings.get("port", 22)))
        self.user.setText(str(settings.get("user", "root")))
        self.auth_method.setCurrentIndex(max(0, self.auth_method.findData(settings.get("auth_method", "password"))))
        # 构造期间 CaptureSettingsPage 尚未接收 self.connection，必须阻止回调提前访问页面。
        self.remember_password.blockSignals(True)
        self.remember_password.setChecked(bool(settings.get("remember_password", False)))
        self.remember_password.blockSignals(False)
        self.fingerprint_check.blockSignals(True)
        self.fingerprint_check.setChecked(bool(settings.get("remember_host_fingerprint", False)))
        self.fingerprint_check.blockSignals(False)
        self.private_key.setText(self._display_path(settings.get("private_key_path", "")))
        self._sync_authentication_fields()

    def collect(self):
        """返回非敏感 SSH 设置，密码由服务层单独管理。"""

        return {
            "host": self.host.text().strip(),
            "port": self.port.value(),
            "user": self.user.text().strip(),
            "auth_method": self.auth_method.currentData(),
            "remember_password": self.remember_password.isChecked(),
            "private_key_path": self.private_key.text().strip(),
            "remember_host_fingerprint": self.fingerprint_check.isChecked(),
        }

    def password_text(self):
        """返回当前密码控件内容，调用方不得保存到磁盘或日志。"""

        return "" if self.auth_method.currentData() == "private_key" else self.password.text()

    def set_connection_state(self, state):
        """按 SSH 状态更新登录按钮和编辑控件，防止连接中重复提交。"""

        connected = state == "connected"
        busy = state in {"connecting", "disconnecting"}
        self.login_button.setText(
            "正在连接..." if state == "connecting"
            else "正在断开..." if state == "disconnecting"
            else "登出" if connected
            else "登录"
        )
        action_state = (
            "busy" if busy
            else "logout" if connected
            else "login"
        )
        if self.login_button.property("connectionActionState") != action_state:
            self.login_button.setProperty("connectionActionState", action_state)
            self.login_button.style().unpolish(self.login_button)
            self.login_button.style().polish(self.login_button)
        self.login_button.setEnabled(not busy)
        # 登录按钮失去可用状态前先把焦点放回分组，避免 Qt 自动跳到下方保存目录，
        # 从而带动整个设置滚动区滚动。
        if busy and self.login_button.hasFocus():
            self.card.setFocusPolicy(Qt.StrongFocus)
            self.card.setFocus(Qt.OtherFocusReason)
        for control in (
            self.host,
            self.port,
            self.user,
            self.password,
            self.auth_method,
            self.private_key,
            self.private_key_button,
        ):
            control.setEnabled(not connected and not busy)
        password_mode = self.auth_method.currentData() == "password"
        # 已登录后仍允许临时查看当前密码、开启或关闭安全凭据保存；连接中和断开中禁用。
        self.password_toggle.setEnabled(password_mode and not busy)
        self.remember_password.setEnabled(password_mode and not busy)

    def _toggle_password_visibility(self, visible):
        """切换密码明文显示；只影响当前控件，不读取或保存密码。"""

        self.password.setEchoMode(QLineEdit.Normal if visible else QLineEdit.Password)
        self.password_toggle.setToolTip("隐藏密码" if visible else "显示密码")
        icon_path = (
            ":/icons/images/icons/cil-lock-locked.png"
            if visible
            else ":/icons/images/icons/cil-low-vision.png"
        )
        self.password_toggle.setProperty("iconPath", icon_path)
        self.password_toggle.setIcon(get_theme_icon(icon_path, detect_light_theme(self.card)))

    def _sync_authentication_fields(self):
        """按认证方式互斥显示密码或私钥字段。

        切换到私钥认证时清除密码和服务内存中的旧凭据，避免隐藏字段继续参与登录。
        当前私钥模式支持未加密的 OpenSSH/PEM 私钥。
        """

        use_private_key = self.auth_method.currentData() == "private_key"
        set_row_visible(self.password_row, not use_private_key)
        self.remember_password.setVisible(not use_private_key)
        set_row_visible(self.private_key_row, use_private_key)
        if use_private_key:
            self.remember_password.blockSignals(True)
            self.remember_password.setChecked(False)
            self.remember_password.blockSignals(False)
            self.password.clear()
            self._action_callback_set_password("")
            if not self._initializing:
                self._remember_callback()

    @staticmethod
    def _display_path(value):
        """将配置中的便携路径解析为界面显示的绝对路径。"""

        if not str(value or "").strip():
            return ""
        from modules.config_services.capture_settings_manager import CaptureSettingsManager
        return str(CaptureSettingsManager.resolve_path(value))
