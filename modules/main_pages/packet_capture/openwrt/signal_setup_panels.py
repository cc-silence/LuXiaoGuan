"""信号设置入口和公共中继表格，真实操作委托常驻服务。"""
from collections import Counter
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView, QHBoxLayout, QHeaderView, QSizePolicy, QPushButton, QVBoxLayout, QWidget
from modules.common_widgets.auto_hide_scroll_bar import AutoHideScrollBar
from modules.common_widgets.spaced_item_delegate import SpacedItemDelegate
from modules.common_widgets.spin_box import CommonSpinBox
from modules.main_pages.page_action_buttons import create_page_text_button
from modules.ui_scale_manager import UiScaleManager
from .signal_setup_forms import hint
from .wireless_network_table import WirelessNetworkTable


def radio_choices(device_radios, rows):
    """合并实际网卡与配置清单；频段冲突保留未知，不按 radio 编号推断。"""
    bands = {}
    aliases = {"2g": "2.4G", "2.4G": "2.4G", "5g": "5G", "5G": "5G",
               "6g": "6G", "6G": "6G"}
    for radio in device_radios:
        bands[radio.name] = {aliases[b] for b in radio.bands if b in aliases}
    row_bands = {}
    for row in rows:
        if row.radio:
            row_bands.setdefault(row.radio, set())
            if row.band in aliases:
                row_bands[row.radio].add(aliases[row.band])
    for name, configured in row_bands.items():
        if name not in bands or not bands[name]:
            bands[name] = configured
        elif configured and not configured.issubset(bands[name]):
            bands[name] = set()
    return tuple((name, " / ".join(b for b in ("2.4G", "5G", "6G") if b in values)
                  or "未知频段") for name, values in bands.items())


class SignalSetupPanels:
    """页面持有的轻量协调器；远程任务经常驻服务执行，不保存密码草稿。"""
    def __init__(self, page):
        self.page = page
        self._dialogs = set()
        self._radio_key = None
        self.radio_buttons = {}
        self._networks = ()
        self.setup = getattr(page.service, "setup", None)
        self.actions = None
        self._owner = getattr(page.service, "owner", None)
        self._owner_signal = getattr(self._owner, "openwrtSnapshotChanged", None)
        self.transmit_page = QWidget(page)
        transmit_layout = QVBoxLayout(self.transmit_page)
        UiScaleManager.for_widget(page).bind_layout(transmit_layout, margins=(10, 8, 10, 14), spacing=12)
        transmit_layout.setAlignment(Qt.AlignTop)
        self.radios_area = QWidget(self.transmit_page)
        self.radios_layout = QHBoxLayout(self.radios_area)
        UiScaleManager.for_widget(page).bind_layout(self.radios_layout, margins=(0, 0, 0, 0), spacing=8)
        transmit_layout.addWidget(self.radios_area)
        self.radio_hint = hint("读取无线配置后，将按实际无线网卡显示新增按钮。", self.transmit_page)
        transmit_layout.addWidget(self.radio_hint)
        self.relay_page = QWidget(page)
        relay_layout = QVBoxLayout(self.relay_page)
        UiScaleManager.for_widget(page).bind_layout(relay_layout, margins=(0, 0, 0, 0), spacing=8)
        self.connection_hints = []
        for content, layout in ((self.transmit_page, transmit_layout), (self.relay_page, relay_layout)):
            notice = hint("路由器已断开，请前往“路由器登录”重新登录。", content)
            layout.insertWidget(0, notice)
            self.connection_hints.append(notice)
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        self.scan_buttons = {}
        self.scan_buttons_layout = QHBoxLayout()
        self.scan_buttons_layout.setContentsMargins(0, 0, 0, 0)
        UiScaleManager.for_widget(page).bind_layout(self.scan_buttons_layout, margins=(0, 0, 0, 0), spacing=8)
        toolbar.addWidget(hint("扫描时间", self.relay_page))
        self.scan_seconds = CommonSpinBox(self.relay_page)
        self.scan_seconds.setRange(5, 120)
        self.scan_seconds.setValue(10)
        self.scan_seconds.setSuffix(" 秒")
        self.scan_seconds.setToolTip("只用点击的无线网卡扫描；不会启用监听模式。")
        toolbar.addWidget(self.scan_seconds)
        toolbar.addLayout(self.scan_buttons_layout, 1)
        relay_layout.addLayout(toolbar)
        self.table = WirelessNetworkTable(self.relay_page)
        self.table.joinRequested.connect(self.join_network)
        self.table.setObjectName("captureAccessPointTable")
        self.table.setHorizontalHeaderLabels(("信号名称", "接入点地址", "频段", "信道", "强度", "加密", "操作"))
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setItemDelegate(SpacedItemDelegate(self.table, show_focus_rect=False))
        self.table.setVerticalScrollBar(AutoHideScrollBar(Qt.Vertical, self.table))
        self.table.setHorizontalScrollBar(AutoHideScrollBar(Qt.Horizontal, self.table))
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setWordWrap(False)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Interactive)
        self.table.setColumnWidth(0, UiScaleManager.for_widget(page).px(210))
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Interactive)
        self.table.setColumnWidth(1, UiScaleManager.for_widget(page).px(170))
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.Fixed)
        self.table.setColumnWidth(6, UiScaleManager.for_widget(page).px(120))
        self.table.horizontalHeader().setMinimumSectionSize(35)
        self.table.setMinimumHeight(UiScaleManager.for_widget(page).px(230))
        self.table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        relay_layout.addWidget(self.table, 1)
        self.empty_hint = hint("扫描后在这里选择信号，点击“加入网络”输入密码。", self.relay_page)
        relay_layout.addWidget(self.empty_hint)
        self.environment_hint = hint("登录后自动检测无线设置环境。", self.transmit_page)
        transmit_layout.addWidget(self.environment_hint)
        self.check_button = create_page_text_button(page, "检测无线组件", "读取实际组件和无线能力，不替换现有无线软件。")
        self.check_button.clicked.connect(self._check_or_install)
        transmit_layout.addWidget(self.check_button)
        if self.setup is not None:
            from .signal_setup_actions import SignalSetupActions
            self.actions = SignalSetupActions(self, self.setup)
            self.setup.snapshotChanged.connect(self._setup_changed)
            self.setup.enable()
            self._setup_changed(self.setup.snapshot)
        if self._owner_signal is not None:
            self._owner_signal.connect(self._device_changed)

    def _setup_changed(self, state):
        """常驻状态回放不读设备，不重建已有扫描结果。"""
        self.environment_hint.setText(state.detail)
        env = state.environment
        self.check_button.setText("安装缺少组件" if env and env.missing else "重新检测无线组件")
        if state.networks != self._networks:
            self._set_networks(state.networks)
        self.empty_hint.setText(state.detail)
        self.empty_hint.setVisible(state.busy or bool(state.detail) or not self._networks)
        if env and env.data.get("radios"):
            from types import SimpleNamespace
            radios = tuple(SimpleNamespace(name=r["name"], bands=(r.get("band", ""),)) for r in env.data["radios"])
            # Normal inventory supplies radios; fall back to detected configuration only.
            self._detected_radios = radios
        self.apply_snapshot(self.page.service.snapshot)

    def _check_or_install(self):
        """安装前展示这次检测的真实缺项，用户确认后才写入设备。"""
        if self.setup is None:
            return
        env = self.setup.snapshot.environment
        if env and env.missing:
            from modules.common_widgets.common_dialogs import ConfirmDialog
            message = "仅安装以下缺少的组件：\n\n" + "、".join(env.missing) + "\n\n保留现有无线认证软件和驱动。安装计划若要求替换或升级已有包将停止。路由器不能联网时可由本机代下载。安装后必要时重新加载管理服务，网页可能需要重新登录，无线配置保持原样。"
            if ConfirmDialog("补充无线组件", message, self.page, yes_text="安装", no_text="取消").exec() == ConfirmDialog.Accepted:
                self.setup.install(env.missing)
        else:
            self.setup.check()

    def _scan(self, radio):
        if self.setup is None:
            return
        state = self.setup.snapshot
        if self.setup.active:
            if state.action == "scan" and state.scan_radio == radio:
                self.setup.cancel()
        elif not self.setup.scan(self.scan_seconds.value(), radio):
            self.empty_hint.setText("扫描未开始，请等待其他任务结束并检查连接。")
            self.empty_hint.show()

    def _sync_scan_buttons(self, choices, snapshot):
        names = {name for name, _band in choices}
        for name in self.scan_buttons.keys() - names:
            button = self.scan_buttons.pop(name)
            self.scan_buttons_layout.removeWidget(button)
            button.hide()
            button.deleteLater()
        state = self.setup.snapshot if self.setup is not None else None
        env = state.environment if state is not None else None
        detected = {r["name"]: r for r in env.data.get("radios", ())} if env else None
        scanning = bool(self.setup and self.setup.active and state.action == "scan")
        idle = bool(self.setup and snapshot.connected and not snapshot.busy and not self.setup.active)
        for index, (name, band) in enumerate(choices):
            button = self.scan_buttons.get(name)
            if button is None:
                button = create_page_text_button(self.page, "", "")
                button.setProperty("signalPrimaryAction", True)
                UiScaleManager.for_widget(self.page).bind_metrics(button, min_height=48)
                button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
                button.clicked.connect(lambda _checked=False, n=name: self._scan(n))
                self.scan_buttons_layout.insertWidget(index, button, 1)
                self.scan_buttons[name] = button
            selected = scanning and state.scan_radio == name
            radio = detected.get(name, {}) if detected is not None else None
            available = radio is None or bool(radio and not radio.get("runtime_pending"))
            text = ("停止扫描" if selected else "扫描 " + band) + "\n" + name
            if button.text() != text:
                button.setText(text)
            button.setEnabled(bool(selected or idle and available))
            button.setToolTip(("停止此网卡的扫描。" if selected else
                "只用 " + name + " 扫描；从结果加入网络时也用这块网卡桥接。") if available else
                "网卡未读取到或正在切换状态，请稍后刷新。")

    def _device_changed(self, _snapshot):
        """设备快照改变时更新入口，不执行额外读取。"""
        self.apply_snapshot(self.page.service.snapshot)

    def apply_snapshot(self, snapshot):
        """依照设备和任务状态复用按钮；断连清空旧设备的编辑上下文。"""
        owner_snapshot = getattr(self._owner, "openwrt_snapshot", None)
        device = getattr(owner_snapshot, "device", None)
        radios = (getattr(device, "radios", ()) or getattr(self, "_detected_radios", ())) if snapshot.connected else ()
        rows = snapshot.inventory.rows if snapshot.connected else ()
        choices = radio_choices(radios, rows)
        if choices != self._radio_key:
            self._radio_key = choices
            for button in self.radio_buttons.values():
                self.radios_layout.removeWidget(button)
                button.hide()
                button.deleteLater()
            self.radio_buttons.clear()
            counts = Counter(band for _name, band in choices)
            for index, (name, band) in enumerate(choices):
                text = "新增" + band + "信号"
                if counts[band] > 1 or band == "未知频段":
                    text += " · " + name
                button = create_page_text_button(self.page, text,
                    "在此无线网卡上新增发射信号；设置按设备能力提供。")
                button.setProperty("signalPrimaryAction", True)
                UiScaleManager.for_widget(self.page).bind_metrics(button, min_height=48)
                button.clicked.connect(lambda _checked=False, n=name, b=band: self.add_access_point(n, b))
                button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
                self.radios_layout.addWidget(button, 1)
                self.radio_buttons[name] = button
        ready = bool(snapshot.connected and not snapshot.busy and not snapshot.stale)
        if self.setup is not None:
            env = self.setup.snapshot.environment
            ready = ready and bool(env and env.ready)
        for button in self.radio_buttons.values():
            button.setEnabled(ready)
        self.radio_hint.setVisible(snapshot.connected and not choices)
        self.environment_hint.setVisible(snapshot.connected)
        for notice in self.connection_hints:
            notice.setVisible(not snapshot.connected)
        state = self.setup.snapshot if self.setup is not None else None
        self.empty_hint.setVisible(bool(snapshot.connected and
            (not self._networks or state and (state.busy or state.detail))))
        self.scan_seconds.setEnabled(snapshot.connected and not snapshot.busy and not (self.setup and self.setup.active))
        self._sync_network_actions(ready)
        actual_names = {radio.name for radio in radios}
        scan_choices = tuple(choice for choice in choices if not actual_names or choice[0] in actual_names)
        self._sync_scan_buttons(scan_choices, snapshot)
        if self.setup is not None:
            self.check_button.setEnabled(snapshot.connected and not snapshot.busy)
        if not snapshot.connected:
            self._detected_radios = ()
            self._set_networks(())
            self.empty_hint.hide()
            if self.actions is not None and self.actions.pending:
                self.setup.cancel()
                self.actions.pending = None
            for dialog in tuple(self._dialogs):
                dialog._busy = False
            self.close_dialogs()

    def _sync_network_actions(self, ready):
        """仅同步表格按钮，不重建扫描行、不丢失选择。"""
        self.table.set_actions_enabled(ready)

    def show_preview_networks(self, networks):
        """独立演示窗口专用数据入口；真实页面禁止把示例当扫描结果。"""
        if not self.page.property("wirelessSetupDemo"):
            raise ValueError("Example networks are only allowed in the preview window")
        self._set_networks(networks)

    def _set_networks(self, networks):
        """用真实结果更新表格，不把示例接入正式页面。"""
        self._networks = tuple(networks)[:1024]
        self.table.set_networks(self._networks)
        self.empty_hint.setVisible(not self._networks)
        self.refresh_privacy()

    def refresh_privacy(self):
        """Project names once, without rebuilding rows or resizing every column."""
        private = bool(self.page._privacy and self.page._privacy.enabled)
        self.table.refresh_privacy(self.page._mask_names or private, private)

    def _show(self, dialog):
        """非阻塞打开有所属的弹窗；关闭后解除引用并销毁。"""
        self._dialogs.add(dialog)
        dialog.finished.connect(lambda _result, d=dialog: self._release(d))
        dialog.setWindowModality(Qt.NonModal)
        dialog.show()

    def _release(self, dialog):
        """回收一次性窗口，密码由弹窗关闭路径清空。"""
        self._dialogs.discard(dialog)
        dialog.deleteLater()

    def add_access_point(self, radio_name, band):
        """打开新增发射设置；检测未就绪时不允许保存。"""
        if self.actions is not None:
            return self.actions.access_point(radio_name, band)
        from .signal_setup_dialogs import AccessPointSetupDialog
        self._show(AccessPointSetupDialog(radio_name, band, self.page))

    def edit(self, row):
        """新增和编辑按信号用途复用同一套弹窗，未知模式拒绝。"""
        if self.actions is not None:
            return self.actions.edit(row)
        from .signal_setup_dialogs import AccessPointSetupDialog, RelaySetupDialog
        if row.mode == "ap":
            self._show(AccessPointSetupDialog(row.radio, self.page._band_text(row).split(" · ")[0],
                                              self.page, row=row))
        elif row.mode == "sta":
            self._show(RelaySetupDialog(row.ssid, self.page, radio_name=row.radio, row=row))

    def join_network(self, network):
        """正式线路先验证再打开详情；隔离示例服务只允许预览。"""
        if self.actions is not None:
            return self.actions.join(network)
        from .signal_setup_dialogs import RelayPasswordDialog, RelaySetupDialog
        dialog = RelayPasswordDialog(network.ssid, self.page)
        dialog.preview_button.show()

        def preview_details():
            details = RelaySetupDialog(network.ssid, self.page, password=dialog.password.edit.text())
            dialog.reject()
            self._show(details)

        dialog.previewRequested.connect(preview_details)
        self._show(dialog)

    def close_dialogs(self):
        """清理本页全部编辑上下文，忙碌窗口等待后台恢复。"""
        for dialog in tuple(self._dialogs):
            dialog.reject()

    def dispose(self):
        """释放订阅和弹窗；正在验证的本页操作请求取消并由服务恢复。"""
        if self.actions is not None:
            self.actions.dispose()
            self.actions.deleteLater()
        if self.setup is not None:
            self.setup.snapshotChanged.disconnect(self._setup_changed)
        for dialog in tuple(self._dialogs):
            dialog._busy = False
        self.close_dialogs()
        if self._owner_signal is not None:
            self._owner_signal.disconnect(self._device_changed)
