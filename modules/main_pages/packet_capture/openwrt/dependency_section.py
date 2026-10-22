"""OpenWrt 抓包依赖展示分组。"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QLabel, QWidget

from modules.common_widgets.sweep_button import SweepButton
from modules.main_pages.packet_capture.ui_helpers import create_capture_card


from .ui_features import PMKID_UI_VISIBLE


class DependencySection:
    """展示功能开关和依赖状态，安装按钮只发出页面动作。"""

    def __init__(self, parent, settings, action_callback, dependency_service=None, owner=None):
        self.card, self.layout = create_capture_card(
            parent,
            "路由器抓包依赖",
            "这些是路由器运行相关功能所需的软件，不安装到 Windows。先登录，勾选需要的功能，再检查安装；安装前会让你确认清单。",
            collapsible=True,
        )
        self.offline_controls = None
        if owner is not None:
            from .common_bundle_controls import CommonBundleControls
            self.offline_controls = CommonBundleControls(self.card, self.layout, owner, "capture")
        self.dependency_service=dependency_service
        self._action_callback = action_callback
        self.checks = {}
        self.statuses = {}
        self._busy = False
        # 各功能总开关：关掉后常规页对应入口停用（下次检查依赖后生效）。
        # 悬停提示只讲人话：这个开关管哪个按钮、关掉有啥影响。
        features = (
            ("scan", "信号扫描", "需要查找周围 Wi-Fi 时保持勾选。关闭后不能使用“开始扫描”；需要借用正在使用的网卡时会先请求确认。"),
            ("pmkid", "PMKID", "需要使用“开始 PMKID 抓包”时勾选。通常只接收数据；是否主动发射由设置中的单独开关决定。关闭后停用这个入口。"),
            ("handshake", "常规抓包", "管“开始常规抓包”按钮。关掉后不能定向抓握手；只抓你勾选的目标。"),
            ("wps", "秒 Pin", "管秒 Pin 和高级 Pin 入口。跑在路由器上，耗时长，部分路由器会锁 WPS，导致后续连不上。"),
            ("password", "猜密码", "管“开始猜密码”入口。高风险：会改路由器无线和网络配置（动前先备份），只能测自己的网络。"),
        )
        enabled = settings.get("enabled_features", {})
        for key, title, tip in features:
            checkbox = QCheckBox(title, self.card)
            checkbox.setObjectName("captureCheckBox")
            checkbox.setToolTip(tip)
            checkbox.setChecked(bool(enabled.get(key, True)))
            status = QLabel("未检测", self.card)
            status.setObjectName("captureStatusValue")
            self.checks[key] = checkbox
            self.statuses[key] = status
            # 使用卡片布局的横向行，保持标签和状态在同一基线。
            from PySide6.QtWidgets import QHBoxLayout
            line = QHBoxLayout()
            line.setContentsMargins(0, 0, 0, 0)
            line.addWidget(checkbox)
            line.addStretch(1)
            line.addWidget(status)
            if key=="pmkid":
                self.pmkid_row=QWidget(self.card)
                self.pmkid_row.setLayout(line)
                self.layout.addWidget(self.pmkid_row)
                self.pmkid_row.setVisible(PMKID_UI_VISIBLE)
            else:
                self.layout.addLayout(line)
            checkbox.toggled.connect(lambda _checked, feature=key: action_callback(f"feature:{feature}"))
        self.install_button = self._make_button(
            "检查并安装抓包依赖", "install_dependencies",
            "检查路由器上缺哪些抓包软件，安装前会列出清单让你确认。\n"
            "装的是路由器端软件，不动 Windows；连不上路由器时点不了。",
        )
        self.layout.addWidget(self.install_button)
        self.uninstall_button = self._make_button(
            "卸载抓包依赖", "uninstall_dependencies",
            "不再使用这些功能时才卸载。会先让你选择要删除的软件；卸载后对应的抓包功能将不可用，需要重新安装才能使用。",
        )
        self.uninstall_button.setProperty("riskAction", True)
        self.layout.addWidget(self.uninstall_button)
        self.install_button.setEnabled(False)
        self.uninstall_button.setEnabled(False)

    def dispose(self):
        """\u91ca\u653e\u9875\u9762\u8ba2\u9605\uff0c\u5e38\u9a7b\u4e0b\u8f7d\u4efb\u52a1\u7ee7\u7eed\u3002"""
        if self.offline_controls is not None:
            self.offline_controls.dispose()

    def _make_button(self, text, action, tooltip):
        """创建扫光操作按钮；点击动作走 captureAction 属性，运行态可切换。"""
        button = SweepButton(text, self.card)
        button.setObjectName("captureSecondaryButton")
        button.setAttribute(Qt.WA_StyledBackground, True)
        button.setCursor(Qt.PointingHandCursor)
        button.setToolTip(tooltip)
        button.set_light_theme(getattr(self.card, "light_theme", False))
        button.setProperty("captureAction", action)
        button.clicked.connect(
            lambda _checked=False, control=button: self._action_callback(
                str(control.property("captureAction"))))
        return button

    def enabled_features(self):
        """返回功能开关快照。"""

        return {key: control.isChecked() for key, control in self.checks.items()}

    def set_connected(self, connected):
        """只有 SSH 连接建立后允许修改依赖开关或安装/卸载。"""

        for control in self.checks.values():
            control.setEnabled(connected)
        self.install_button.setEnabled(connected and not self._busy)
        self.uninstall_button.setEnabled(connected and not self._busy)

    def set_busy(self, busy):
        """任务执行中禁用安装/卸载按钮；结束后恢复。"""

        self._busy = bool(busy)
        enabled_controls = all(control.isEnabled() for control in self.checks.values())
        self.install_button.setEnabled(enabled_controls and not self._busy)
        self.uninstall_button.setEnabled(enabled_controls and not self._busy)

    def apply_snapshot(self, snapshot):
        """刷新依赖状态标签，不改变用户功能开关。

        依赖任务执行中对应按钮变“停止…”并扫光，点即停止；结束自动恢复。
        """

        for item in snapshot.dependencies:
            status = self.statuses.get(item.key)
            if status is None:
                continue
            text = {
                "ready": "已安装",
                "missing": "缺少依赖",
                "unsupported": "不支持",
                "checking": "检测中",
            }.get(item.state.value, "未检测")
            status.setText(text)
            # 版本与缺失详情进悬停提示，状态列只放四个短词。
            status.setToolTip(str(item.detail or ""))
        connected = snapshot.connection_state.value == "connected"
        busy = snapshot.task_state not in {"idle", "failed"}
        task_type = getattr(snapshot.task_type, "value", snapshot.task_type)
        task=getattr(getattr(self,"dependency_service",None),"task",None)
        belongs=task is None or task.group in {"capture","all"}
        self._sync_task_button(
            self.install_button, "install_dependencies",
            "检查并安装抓包依赖", "停止安装",
            connected and busy and belongs and task_type == "dependency_install")
        self._sync_task_button(
            self.uninstall_button, "uninstall_dependencies",
            "卸载抓包依赖", "停止卸载",
            connected and busy and belongs and task_type == "dependency_uninstall")

    @staticmethod
    def _sync_task_button(button, idle_action, idle_text, running_text, running):
        """同步运行态按钮；无变化时不碰，避免重启扫光动画。"""
        running = bool(running)
        if bool(button.property("guided")) != running:
            button.set_guided_mode(running)
        button.setProperty("captureAction", "stop" if running else idle_action)
        text = running_text if running else idle_text
        if button.text() != text:
            button.setText(text)
        if running and not button.isEnabled():
            button.setEnabled(True)
