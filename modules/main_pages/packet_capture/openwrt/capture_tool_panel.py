"""OpenWrt 常规抓包页右侧操作栏。"""

from modules.common_widgets.spin_box import CommonSpinBox as QSpinBox
from PySide6.QtCore import Qt, Signal
from modules.ui_scale_manager import UiScaleManager
from PySide6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from modules.main_pages.page_action_buttons import create_page_text_button
from modules.common_widgets.combo_box import CommonComboBox as QComboBox




class CaptureToolPanel(QWidget):
    """集中展示扫描、握手、WPS 和猜密码入口。

    当前架构阶段只发出动作信号。OpenWrtPage 根据连接、依赖和任务快照控制按钮状态，
    不在本组件执行命令或创建 worker。
    """

    settingsChanged = Signal()
    actionRequested = Signal(str)
    advancedWpsRequested = Signal()

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setObjectName("captureToolPanel")
        # 操作按钮在任务状态切换时可能被禁用。容器本身可接收焦点但不可编辑，
        # 用它承接焦点可避免 Qt 自动跳到下一个数字输入框。
        self.setFocusPolicy(Qt.StrongFocus)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 8, 0)
        # 四个操作阶段之间使用明显大于组内的留白，让用户能按从上到下的流程阅读。
        root.setSpacing(10)

        scan_group, scan_layout = self._group("信号扫描")
        self.band_combo = QComboBox(scan_group)
        self.band_combo.setObjectName("captureComboBox")
        self.band_combo.addItems(["2.4G", "5G"])
        self.band_combo.setToolTip("选择目标 Wi-Fi 所在频段，路由器网卡也必须支持。找不到信号时可换另一个频段重新扫描；每次只扫描所选频段。")
        self.scan_seconds = self._spin(scan_group, 5, 120, " 秒")
        self.scan_seconds.setToolTip("建议先用 24 秒。信号少或设备不活跃时可适当延长，更容易看到新信号和连接设备；时间越长等待越久。")
        scan_grid = QGridLayout()
        scan_grid.setHorizontalSpacing(6)
        scan_grid.setVerticalSpacing(5)
        scan_grid.addWidget(self._field_label("频段", scan_group), 0, 0)
        scan_grid.addWidget(self._field_label("扫描时间", scan_group), 0, 1)
        scan_grid.addWidget(self.band_combo, 1, 0)
        scan_grid.addWidget(self.scan_seconds, 1, 1)
        scan_grid.setColumnStretch(0, 1)
        scan_grid.setColumnStretch(1, 1)
        scan_layout.addLayout(scan_grid)
        self.scan_button = self._button(scan_group, "开始扫描", "scan")
        scan_layout.addWidget(self.scan_button)
        self.export_signals_button = self._button(scan_group, "导出信号列表", "export_signals")
        self.export_signals_button.setToolTip("导出全部扫描结果为 Excel 或 TXT，按信号由强到弱排列。文件包含真实信号名称和 MAC地址，不受界面隐藏开关影响。")
        self.export_signals_button.setEnabled(False)
        scan_layout.addWidget(self.export_signals_button)
        root.addWidget(scan_group)

        handshake_group, handshake_layout = self._group("握手抓取")
        self.capture_seconds = self._spin(handshake_group, 0, 3600, " 秒")
        self.capture_seconds.setToolTip("每个信号最多等待多久，建议保留 180 秒。连接活动少时可调高，但整个任务会更久；填 0 使用程序默认时长，不代表立即停止。")
        handshake_layout.addWidget(self._field_label("单目标超时", handshake_group))
        handshake_layout.addWidget(self.capture_seconds)
        self.handshake_button = self._button(handshake_group, "开始常规抓包", "handshake")
        handshake_layout.addWidget(self.handshake_button)
        root.addWidget(handshake_group)

        other_group, other_layout = self._group("其他方式")
        other_layout.addWidget(self._subsection_label("WPS / Pin", other_group))
        # 秒 Pin 和高级 Pin 放一排，省出空间给下面的猜密码按钮。
        wps_row = QHBoxLayout()
        wps_row.setContentsMargins(0, 0, 0, 0)
        wps_row.setSpacing(6)
        self.wps_button = self._button(other_group, "开始秒 Pin", "wps")
        self.advanced_wps_button = create_page_text_button(other_group, "高级 Pin", "打开普通 Pin、秒 Pin和离线 Pin 参数窗口。")
        self.advanced_wps_button.clicked.connect(self.advancedWpsRequested)
        wps_row.addWidget(self.wps_button, 1)
        wps_row.addWidget(self.advanced_wps_button, 1)
        other_layout.addLayout(wps_row)
        risk_label = self._subsection_label("在线密码验证", other_group)
        risk_label.setProperty("risk", True)
        other_layout.addWidget(risk_label)
        self.password_pause_button = self._button(other_group, "暂停猜密码", "pause_password")
        self.password_pause_button.hide()
        other_layout.addWidget(self.password_pause_button)
        self.password_button = self._button(other_group, "开始猜密码", "password")
        self.password_button.setProperty("riskAction", True)
        other_layout.addWidget(self.password_button)
        root.addWidget(other_group)
        # “打开包存储目录”独立放在其他方式组下方：它是取结果入口，不属于抓包手段；
        # 跑出可用包时用户第一时间来这里取结果。
        self.open_dir_button = self._button(self, "打开包存储目录", "open_output_dir")
        # 该按钮比普通动作按钮高三分之一，在窄栏里更显眼，方便跑出结果后一眼找到。
        UiScaleManager.for_widget(self).bind_metrics(self.open_dir_button, min_height=35)
        root.addWidget(self.open_dir_button)
        root.addStretch(1)

        self.load_settings(settings)
        self.band_combo.currentTextChanged.connect(lambda _text: self.settingsChanged.emit())
        self.scan_seconds.lineEdit().editingFinished.connect(self.settingsChanged)
        self.capture_seconds.lineEdit().editingFinished.connect(self.settingsChanged)

    @staticmethod
    def _spin(parent, minimum, maximum, suffix):
        spin = QSpinBox(parent)
        spin.setObjectName("captureSpinBox")
        spin.setRange(minimum, maximum)
        spin.setSuffix(suffix)
        return spin

    def _button(self, parent, text, action):
        tips = {
            "scan": "查找所选频段的 Wi-Fi 和连接设备，运行中再点一次可停止。先登录路由器。",
            "handshake": "先勾选自有或已授权的信号，再开始抓取连接数据。是否让目标设备断网重连，由“允许主动断连”设置决定；运行中可点此按钮停止。",
            "wps": "仅适用于自有或已授权且开启 WPS 的网络。尝试常见 Pin 码，不保证成功；频繁尝试可能让目标暂时锁定 WPS。",
            "open_output_dir": "打开已保存抓包结果的文件夹，按钮变绿表示已有可用结果。",
        }
        button = create_page_text_button(self, text, tips.get(action, text))
        button.setProperty("captureAction", action)
        button.clicked.connect(
            lambda _checked=False, control=button: self.actionRequested.emit(
                str(control.property("captureAction"))
            )
        )
        return button

    def _group(self, title):
        """创建标题居中嵌入边框的紧凑操作分组。"""

        group = QGroupBox(title, self)
        group.setObjectName("captureToolGroup")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(6, 12, 6, 6)
        layout.setSpacing(5)
        return group, layout

    @staticmethod
    def _field_label(text, parent):
        """创建弱化的字段标签，让分组标题和操作按钮保持更高视觉权重。"""

        label = QLabel(text, parent)
        label.setObjectName("captureToolFieldLabel")
        return label

    @staticmethod
    def _subsection_label(text, parent):
        """创建同一阶段内的轻量子标题，不增加新的边框。"""

        label = QLabel(text, parent)
        label.setObjectName("captureToolSubsectionLabel")
        return label

    def load_settings(self, settings):
        """从 OpenWrt 配置回显工具栏公共参数。"""

        self.band_combo.setCurrentText(str(settings.get("band", "2.4G")))
        self.scan_seconds.setValue(int(settings.get("scan_seconds", 24)))
        self.capture_seconds.setValue(int(settings.get("idle_timeout_seconds", 180)))

    def collect_settings(self):
        """返回工具栏控制的配置字段。"""

        return {
            "band": self.band_combo.currentText(),
            "scan_seconds": self.scan_seconds.value(),
            "idle_timeout_seconds": self.capture_seconds.value(),
        }


    def set_action_availability(self, connected, busy, enabled_features, task_type="", task_state="", pause_state=""):
        """按连接、任务和功能开关统一控制动作，不自行判断依赖细节。"""

        enabled = bool(connected and not busy)
        mapping = {
            self.scan_button: "scan",
            self.handshake_button: "handshake",
            self.wps_button: "wps",
            self.advanced_wps_button: "wps",
            self.password_button: "password",
        }
        # 扫描进行中保留同一入口作为停止按钮，避免用户只能等待超时或关闭程序。
        # 其他高风险抓包入口仍保持禁用，确保无线环境始终只有一个任务所有者。
        scan_running = busy and task_type == "wifi_scan"
        handshake_running = busy and task_type == "handshake"
        password_running = busy and task_type == "password_verify"
        self.password_pause_button.setVisible(password_running)
        self.password_pause_button.setText({
            "pausing": "等待暂停…", "paused": "继续猜密码", "cleaning": "正在恢复配置…",
        }.get(pause_state, "暂停猜密码"))
        self.password_pause_button.setProperty(
            "captureAction", "resume_password" if pause_state == "paused" else "pause_password",
        )
        self.password_pause_button.setToolTip(
            "已暂停；点击后从下一个密码继续。暂停不会恢复路由器配置，需要恢复请点“停止猜密码”。"
            if pause_state == "paused" else
            "试完当前密码后暂停，不再尝试下一个。正在恢复路由器配置时不能暂停。"
        )
        self._set_button_enabled(
            self.password_pause_button,
            password_running and task_state != "stopping" and pause_state in {"running", "paused"},
        )
        for button, feature in mapping.items():
            if button in {self.scan_button, self.handshake_button, self.password_button}:
                continue
            self._set_button_enabled(button, enabled and bool(enabled_features.get(feature, False)))
        if scan_running:
            self.scan_button.setText("停止扫描")
            self.scan_button.setProperty("captureAction", "stop")
            self._set_button_enabled(self.scan_button, bool(connected))
        else:
            self.scan_button.setText("开始扫描")
            self.scan_button.setProperty("captureAction", "scan")
            # 未连接时仍允许点击并显示明确的“先登录”提示；连接后检查 scan 依赖。
            self._set_button_enabled(
                self.scan_button,
                not connected or (not busy and bool(enabled_features.get("scan", False))),
            )
        if handshake_running:
            self.handshake_button.setText("停止定向任务")
            self.handshake_button.setProperty("captureAction", "stop")
            self._set_button_enabled(self.handshake_button, bool(connected))
        else:
            self.handshake_button.setText("开始常规抓包")
            self.handshake_button.setProperty("captureAction", "handshake")
            self._set_button_enabled(
                self.handshake_button,
                not connected or (not busy and bool(enabled_features.get("handshake", False))),
            )
        self.password_button.setText(
            ("正在停止…" if task_state == "stopping" else "停止猜密码")
            if password_running else "开始猜密码"
        )
        self.password_button.setProperty("captureAction", "stop" if password_running else "password")
        self.password_button.setToolTip(
            "正在停止猜密码并恢复路由器配置，请等待清理完成。"
            if password_running and task_state == "stopping" else
            "停止猜密码并恢复路由器配置；不保留可继续的验证进度。"
            if password_running else "按设置中的字典逐个尝试连接，只能用于自有或已授权的网络。会临时修改路由器配置，可能断网，开始前需确认并备份；大字典可能耗时很久。"
        )
        self._set_button_enabled(
            self.password_button,
            task_state != "stopping" if password_running
            else enabled and bool(enabled_features.get("password", False)),
        )
        # 运行中的按钮使用 hover 样式 + 循环扫光（蓝色边框），与未开始明确区分。
        # 复用 SweepButton 引导模式的视觉；状态未变化时不重复调用，避免每次快照
        # 刷新都重启扫光动画导致扫光冻结在起始位置。
        self._sync_running_visual(self.scan_button, scan_running)
        self._sync_running_visual(self.handshake_button, handshake_running)
        self._sync_running_visual(self.password_button, password_running)

    def _set_button_enabled(self, button, enabled):
        """安全更新动作按钮状态，防止 Qt 自动将焦点跳到表单输入框。"""

        enabled = bool(enabled)
        if button.isEnabled() and not enabled and button.hasFocus():
            self.setFocus(Qt.OtherFocusReason)
        button.setEnabled(enabled)

    def set_result_available(self, has_result):
        """有可用抓包产物时把“打开包存储目录”置绿，提示用户去取结果。

        参数 has_result 为真表示当前会话已跑出可跑包产物；断开连接后快照清空，
        调用方会重新传入假值恢复原色。QSS 用 hasCaptureResult 属性区分颜色。
        """

        has_result = bool(has_result)
        if bool(self.open_dir_button.property("hasCaptureResult")) == has_result:
            return
        self.open_dir_button.setProperty("hasCaptureResult", has_result)
        self.open_dir_button.style().unpolish(self.open_dir_button)
        self.open_dir_button.style().polish(self.open_dir_button)
        self.open_dir_button.update()

    @staticmethod
    def _sync_running_visual(button, running):
        """同步运行中按钮的视觉状态，未变化时跳过。"""

        running = bool(running)
        if bool(button.property("guided")) != running:
            button.set_guided_mode(running)
