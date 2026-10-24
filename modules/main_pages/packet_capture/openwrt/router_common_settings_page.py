"""Common router maintenance cards; constructors perform no remote work."""
from PySide6.QtWidgets import QWidget, QVBoxLayout, QGridLayout, QLabel, QFrame
from modules.common_widgets.bounce_scroll_area import BounceScrollArea
from modules.main_pages.packet_capture.ui_helpers import create_capture_card
from modules.main_pages.page_action_buttons import create_page_text_button, set_page_action_enabled
from modules.ui_scale_manager import UiScaleManager
from modules.config_manager import BASE_DIR
from modules.capture_services.openwrt.router_password import PasswordChange


class RouterCommonSettingsPage(QWidget):
    def __init__(self, capture_service, connection_settings, parent=None):
        super().__init__(parent)
        self.capture_service = capture_service
        self.service = capture_service.router_signals
        self.manager = capture_service.router_management
        self.connection_settings = connection_settings
        self._password_key = None
        from .common_config_actions import CommonConfigActions
        self.config_actions = CommonConfigActions(self)
        from .maintenance_actions import MaintenanceActions
        self.maintenance_actions = MaintenanceActions(self)
        self.setObjectName("routerCommonSettingsPage")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.status_label = QLabel(self)
        self.status_label.setObjectName("captureDescription")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)
        scroll = BounceScrollArea(self)
        scroll.setObjectName("captureScrollArea")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        self.scroll = scroll
        body = QWidget(scroll)
        body.setObjectName("openwrtCaptureContent")
        cards = QGridLayout(body)
        icon_root = BASE_DIR / "images/user_icons/icons_quick_settings"
        scale = UiScaleManager.for_widget(self)
        scale.bind_layout(cards, margins=(10, 8, 10, 14), spacing=12)
        self.password_card, layout = create_capture_card(body, "修改路由器登录密码",
            "修改路由器的管理登录密码，不是 Wi-Fi 密码。", icon_path=icon_root / "System Lock.png")
        self.account_label = QLabel(self.password_card)
        self.account_label.setObjectName("captureDescription")
        self.account_label.setWordWrap(True)
        layout.addWidget(self.account_label)
        self.password_button = create_page_text_button(self.password_card, "打开设置",
            "密码经加密连接输入；修改后验证新密码，不中断当前登录。")
        self.password_button.clicked.connect(self._change_password)
        layout.addWidget(self.password_button)
        self.lan_card, layout = create_capture_card(body, "LAN 网络设置",
            "修改管理地址、网关和 IPv6；保存后重启网络生效。", icon_path=icon_root / "System Router.png")
        self.lan_button = create_page_text_button(self.lan_card, "打开设置", "读取路由器当前局域网设置后编辑。")
        layout.addWidget(self.lan_button)
        self.system_card, layout = create_capture_card(body, "主机名与时区",
            "修改路由器自己的名称和当地时区，不改变 Wi-Fi 名称。", icon_path=icon_root / "System Clock.png")
        self.system_button = create_page_text_button(self.system_card, "打开设置", "读取当前主机名和时区后编辑。")
        layout.addWidget(self.system_button)
        self.relocation_card, _layout = create_capture_card(body, "搬家清理",
            "清理旧 Wi-Fi 与桥接、更换设备身份；执行前可预览。", icon_path=icon_root / "System Broom.png")
        self.relocation_container = QWidget(self.relocation_card)
        self.relocation_container.setObjectName("captureActionContainer")
        QVBoxLayout(self.relocation_container).setContentsMargins(0, 0, 0, 0)
        _layout.addWidget(self.relocation_container)
        self.orphan_card, _layout = create_capture_card(body, "遗留接口检查",
            "检查旧中继留下的接口，选择后再清理。", icon_path=icon_root / "System Search.png")
        self.orphan_container = QWidget(self.orphan_card)
        self.orphan_container.setObjectName("captureActionContainer")
        QVBoxLayout(self.orphan_container).setContentsMargins(0, 0, 0, 0)
        _layout.addWidget(self.orphan_container)
        self.backup_card, layout = create_capture_card(body, "生成配置备份",
            "导出当前配置到电脑；备份含密码，请妥善保管。",
            icon_path=icon_root / "System Backup.png")
        self.backup_button = create_page_text_button(self.backup_card, "生成备份", "选择保存位置后生成并校验配置归档。")
        layout.addWidget(self.backup_button)
        self.restore_card, layout = create_capture_card(body, "恢复配置",
            "从备份恢复配置并重启，管理地址和密码可能改变。",
            icon_path=icon_root / "System Upload.png")
        self.restore_button = create_page_text_button(self.restore_card, "上传备份", "先预览，再保存当前配置备份，最后恢复并重启。")
        layout.addWidget(self.restore_button)
        self.reset_card, layout = create_capture_card(body, "恢复出厂设置",
            "清空设置和后来安装的软件，回到当前固件初始状态。",
            icon_path=icon_root / "System Refresh.png")
        self.reset_button = create_page_text_button(self.reset_card, "执行重置", "先检查设备支持，再备份当前配置，输入确认文字后才会执行。")
        self.reset_button.setProperty("dangerAction", True)
        layout.addWidget(self.reset_button)
        self.cards_layout = cards
        self.cards = (self.password_card, self.lan_card, self.system_card, self.relocation_card,
                      self.orphan_card, self.backup_card, self.restore_card, self.reset_card)
        for index, card in enumerate(self.cards):
            cards.addWidget(card, index // 2, index % 2)
        cards.setColumnStretch(0, 1)
        cards.setColumnStretch(1, 1)
        cards.setRowStretch(4, 1)
        from .signal_relocation_actions import RelocationActions
        from .signal_orphan_actions import OrphanInterfaceActions
        self.relocation_actions = RelocationActions(self, self.relocation_container)
        self.orphan_actions = OrphanInterfaceActions(self, self.orphan_container)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)
        self.backup_button.clicked.connect(lambda: self.maintenance_actions.start("backup"))
        self.restore_button.clicked.connect(lambda: self.maintenance_actions.start("restore"))
        self.reset_button.clicked.connect(lambda: self.maintenance_actions.start("reset"))
        self.lan_button.clicked.connect(lambda: self.config_actions.read("network"))
        self.system_button.clicked.connect(lambda: self.config_actions.read("system"))
        self.manager.completed.connect(self._completed)
        self.manager.failed.connect(self._failed)
        capture_service.activityChanged.connect(self._sync)
        capture_service.openwrtSnapshotChanged.connect(self._sync)
        self.service.snapshotChanged.connect(self._sync)
        self.service.messageGenerated.connect(self._message)
        self._sync()

    def _message(self, text, level):
        self.status_label.setText(text)

    def _sync(self, *_):
        ssh = self.manager.manager.ssh
        connected = ssh.connected
        self.account_label.setText("当前登录账号：" + (ssh._user if connected else "尚未登录"))
        available = connected and not self.capture_service.is_busy and not self.config_actions.active and not self.maintenance_actions.active and self._password_key is None
        set_page_action_enabled(self.password_button, available)
        set_page_action_enabled(self.lan_button, available)
        set_page_action_enabled(self.system_button, available)
        for button in (self.backup_button, self.restore_button, self.reset_button):
            set_page_action_enabled(button, available)
        self.relocation_actions._sync()
        self.orphan_actions._sync()
        if self.capture_service.is_busy or self.config_actions.active or self.maintenance_actions.active or self._password_key:
            set_page_action_enabled(self.relocation_actions.button, False)
            set_page_action_enabled(self.orphan_actions.button, False)
        notice = "请先到“路由器登录”登录路由器。"
        if not connected and not self.status_label.text():
            self.status_label.setText(notice)
        elif connected and self.status_label.text() == notice:
            self.status_label.clear()

    def _change_password(self):
        if not self.password_button.isEnabled():
            return
        ssh = self.manager.manager.ssh
        from .router_password_dialog import RouterPasswordDialog
        dialog = RouterPasswordDialog(ssh._user, self)
        connection = ssh._client
        request = None
        try:
            if dialog.exec() != dialog.Accepted:
                return
            if ssh._client is not connection or not ssh.connected:
                self.status_label.setText("\u8fde\u63a5\u5df2\u53d8\u5316\uff0c\u8bf7\u91cd\u65b0\u6253\u5f00\u5bc6\u7801\u8bbe\u7f6e\u3002")
                return
            settings = dict(self.connection_settings())
            settings.update(host=ssh._host, port=ssh._port, user=ssh._user)
            request = PasswordChange.create(dialog.new_password.submitted_text(), settings)
            self._password_key = self.manager.submit("password_change", request=request)
        except Exception as exc:
            if request is not None:
                request.clear()
            self.status_label.setText(str(exc))
            return
        finally:
            dialog.clear()
            dialog.deleteLater()
        self.status_label.setText("正在修改登录密码，请等待结果。")
        self._sync()

    def _completed(self, key, result):
        if self.maintenance_actions.completed(key, result):
            return
        if self.config_actions.completed(key, result):
            return
        if key != self._password_key:
            return
        self._password_key = None
        self.status_label.setText(result["note"])
        self._sync()

    def _failed(self, key, message):
        if self.maintenance_actions.failed(key, message):
            return
        if self.config_actions.failed(key, message):
            return
        if key != self._password_key:
            return
        self._password_key = None
        self.status_label.setText(message)
        self._sync()

    def dispose(self):
        self.maintenance_actions.dispose()
        self.config_actions.dispose()
        if self._password_key:
            self.manager.cancel(self._password_key)
        self.relocation_actions.dispose()
        self.orphan_actions.dispose()
        for signal, slot in ((self.manager.completed, self._completed), (self.manager.failed, self._failed),
            (self.capture_service.activityChanged, self._sync), (self.capture_service.openwrtSnapshotChanged, self._sync),
            (self.service.snapshotChanged, self._sync), (self.service.messageGenerated, self._message)):
            signal.disconnect(slot)
