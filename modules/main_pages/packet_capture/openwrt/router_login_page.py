"""OpenWrt 路由器登录页：SSH 连接、设备信息与信息窗口。"""

from PySide6.QtCore import Qt, QTimer, Signal, QSignalBlocker
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from modules.main_pages.packet_capture.ui_helpers import create_capture_card
from .connection_section import ConnectionSection
from .device_status_panel import DeviceStatusPanel
from .resource_picker import OpenWrtResourcePicker


class RouterLoginPage(QWidget):
    """路由器登录页，只管连接与设备基本信息，不保存抓包策略。

    参数：service 是业务服务；settings 是标准化 OpenWrt 配置；
    info_panel 是下半部分信息窗口。SSH 密码只在内存和 Windows 凭据之间流转。
    """

    settingsChanged = Signal()
    actionRequested = Signal(str)

    def __init__(self, service, settings, info_panel=None, parent=None, dependency_page=None):
        super().__init__(parent)
        self.service = service
        self.settings = settings
        self._dependency_page = dependency_page
        self.resource_picker = OpenWrtResourcePicker(self)
        self.setObjectName("openwrtRouterLoginPage")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        from modules.common_widgets.bounce_scroll_area import BounceScrollArea
        self.scroll_area = BounceScrollArea(self)
        self.scroll_area.setObjectName("captureScrollArea")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(BounceScrollArea.NoFrame)
        content = QWidget(self.scroll_area)
        content.setObjectName("openwrtLoginContent")
        content.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)

        # 左右并排：左侧参数，右侧基本信息，各占一半
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 4, 0)
        content_layout.setSpacing(8)
        self.scroll_area.setWidget(content)

        self.connection = ConnectionSection(
            content,
            settings,
            self._request,
            password_callback=self.service.set_openwrt_password,
            remember_callback=self._sync_saved_password,
            identity_callback=self._reload_saved_password_for_identity,
        )
        self._remember_password_preference = self.connection.remember_password.isChecked()
        self._password_identity = self._credential_identity()
        self._last_connection_state = None
        current_password = self.service.openwrt_password()
        if current_password:
            self.connection.password.setText(current_password)
        else:
            self._load_saved_password()
        self.connection.private_key_button.clicked.connect(
            lambda: self.resource_picker.choose_private_key(self.connection.private_key)
        )
        self.connection.private_key.editingFinished.connect(self.settingsChanged)
        self.connection.auth_method.currentIndexChanged.connect(
            lambda _index: self.settingsChanged.emit()
        )
        self.connection.card.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        content_layout.addWidget(self.connection.card, 1)

        device_card, device_layout = create_capture_card(
            content,
            "基本信息",
            "显示路由器系统版本、硬件标识、无线网卡及存储等硬件状态。",
        )
        self.device_card = device_card
        self.device_status = DeviceStatusPanel(device_card)
        device_layout.addWidget(self.device_status)
        device_layout.addStretch(1)
        device_card.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        content_layout.addWidget(device_card, 1)

        self.info_panel = info_panel
        if info_panel is not None:
            from modules.common_widgets.info_panel_dock import InfoPanelDock
            # 垂直分栏：上面是参数与基本信息各占一半，下面是日志全宽
            self.info_dock = InfoPanelDock(self.scroll_area, info_panel, Qt.Vertical, self, sizes=(360, 200))
            outer.addWidget(self.info_dock, 1)
        else:
            outer.addWidget(self.scroll_area, 1)

        self.dependency_prompt = None
        service.openwrtSnapshotChanged.connect(self._apply_snapshot)
        self._apply_snapshot(service.openwrt_snapshot)

    @property
    def dependencies(self):
        if self._dependency_page is not None:
            return self._dependency_page.dependencies
        if not hasattr(self, "_fallback_dependency_page"):
            from .router_dependency_page import RouterDependencyPage
            self._fallback_dependency_page = RouterDependencyPage(self.service, self.settings)
        return self._fallback_dependency_page.dependencies

    @property
    def common_tools(self):
        if self._dependency_page is not None:
            return self._dependency_page.common_tools
        if not hasattr(self, "_fallback_dependency_page"):
            from .router_dependency_page import RouterDependencyPage
            self._fallback_dependency_page = RouterDependencyPage(self.service, self.settings)
        return self._fallback_dependency_page.common_tools

    def set_dependency_page(self, dependency_page):
        self._dependency_page = dependency_page

    def _request(self, action):
        """把登录分组动作转发给页面主线程协调器。"""

        self.actionRequested.emit(action)

    def collect_settings(self):
        """返回 SSH 连接字段与功能开关，明确不包含密码。"""

        result = self.connection.collect()
        if self.dependencies is not None:
            result["enabled_features"] = self.dependencies.enabled_features()
        return result

    def _credential_settings(self):
        """返回 Windows 凭据键所需的非敏感连接字段。"""

        return {
            "host": self.connection.host.text().strip(),
            "port": self.connection.port.value(),
            "user": self.connection.user.text().strip(),
            "remember_password": self.connection.remember_password.isChecked(),
        }

    def _credential_identity(self):
        settings = self._credential_settings()
        return (settings["host"].casefold(), settings["port"], settings["user"])

    def _load_saved_password(self):
        """按当前地址加载 Windows 凭据；失败时保留空密码并显示原因。"""

        try:
            password = self.service.load_saved_openwrt_password(
                self._credential_settings()
            )
        except OSError as exc:
            if self.info_panel is not None:
                self.info_panel.append_info(f"读取 Windows 保存的路由器密码失败：{exc}", "error")
            return False
        self.connection.password.setText(password)
        return True

    def _restore_password_preference(self):
        with QSignalBlocker(self.connection.remember_password):
            self.connection.remember_password.setChecked(self._remember_password_preference)

    def _sync_saved_password(self):
        """记住密码开关或设备身份变化后更新 Windows 凭据。"""

        settings = self._credential_settings()
        try:
            if settings["remember_password"]:
                if not self.connection.password.text() and not self._load_saved_password():
                    self._restore_password_preference()
                    return
                self.service.save_openwrt_password_preference(settings)
            else:
                self.service.forget_saved_openwrt_password(settings)
                self.show_storage_feedback("\u5df2\u5220\u9664\u672c\u673a\u4fdd\u5b58\u7684\u5f53\u524d\u8bbe\u5907\u5bc6\u7801")
        except OSError as exc:
            self._restore_password_preference()
            if self.info_panel is not None:
                self.info_panel.append_info(f"更新 Windows 路由器密码凭据失败：{exc}", "error")
            return
        self._remember_password_preference = settings["remember_password"]
        self.settingsChanged.emit()

    def show_storage_feedback(self, text):
        """Reuse one parent-owned toast, only after persistent deletion succeeds."""
        from modules.common_widgets.floating_feedback import FloatingFeedback
        from modules.ui_scale_manager import UiScaleManager
        feedback = getattr(self, "_storage_feedback", None)
        if feedback is None:
            feedback = self._storage_feedback = FloatingFeedback(self)
        feedback.show_feedback(text, "success", UiScaleManager.for_widget(self))

    def _reload_saved_password_for_identity(self):
        """地址、端口或用户变化时只加载对应凭据，不能复制旧设备密码。"""

        identity = self._credential_identity()
        if identity == self._password_identity:
            return
        self._password_identity = identity
        self.connection.password.clear()
        self.service.set_openwrt_password("")
        if self.connection.remember_password.isChecked():
            self._load_saved_password()
        self.settingsChanged.emit()

    def apply_snapshot(self, snapshot):
        """刷新连接与设备显示。"""

        scroll_bar = self.scroll_area.verticalScrollBar()
        self._snapshot_scroll_position = scroll_bar.value()
        state = snapshot.connection_state.value
        previous = self._last_connection_state
        self._last_connection_state = state
        self.connection.set_connection_state(state)
        if (
            state in {"disconnected", "failed"} and previous is not None and state != previous
        ):
            if self.connection.remember_password.isChecked():
                self._load_saved_password()
            else:
                self.connection.password.clear()
        self.device_status.apply_snapshot(snapshot)
        # 控件启用状态和设备信息会触发布局重算；下一轮事件循环恢复用户原滚动位置。
        QTimer.singleShot(0, self._restore_snapshot_scroll)

    def _restore_snapshot_scroll(self):
        """恢复最新快照前的位置；绑定页面槽使销毁时 Qt 自动取消回调。"""

        self.scroll_area.verticalScrollBar().setValue(self._snapshot_scroll_position)

    def _apply_snapshot(self, snapshot):
        """服务信号适配槽，保持 Qt 回调名称清晰。"""

        self.apply_snapshot(snapshot)
