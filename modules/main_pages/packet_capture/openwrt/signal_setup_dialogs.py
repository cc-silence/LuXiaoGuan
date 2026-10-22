"""发射与中继设置窗口，默认简化，高级选项按设备能力装配。"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QScrollArea, QVBoxLayout, QWidget
from modules.common_widgets.bounce_scroll_area import BounceScrollArea
from modules.common_widgets.spin_box import CommonSpinBox
from modules.common_widgets.collapsible_section import CollapsibleSection
from modules.common_widgets.frameless_dialog import FramelessDialog
from modules.config_manager import ConfigManager
from modules.dialog_theme import build_base_stylesheet, detect_light_theme
from modules.settings_pages.settings_widgets import create_dialog_ok_cancel_buttons
from modules.ui_scale_manager import UiScaleManager
from .signal_identity_fields import IdentityFields
from .signal_setup_forms import PasswordInput, SettingsTabs, add_field, choice, hint, text_input


class _SetupDialog(FramelessDialog):
    """公共窗口外壳，任务结束前关闭按钮仅发出取消请求。"""
    saveRequested = Signal()
    saveOnlyRequested = Signal()
    cancelRequested = Signal()

    def __init__(self, title, parent=None, *, compact=False):
        super().__init__(title, parent, inherit_parent_stylesheet=False)
        light = detect_light_theme(parent)
        self.setProperty("settingsLightTheme", light)
        self.setStyleSheet(build_base_stylesheet(ConfigManager(), light_theme=light,
                                               scale_manager=UiScaleManager.for_widget(self)))
        self.set_scaled_size((460, 280) if compact else (620, 450),
                             (540, 330) if compact else (780, 570))
        self.scroll_area = BounceScrollArea(self)
        self.scroll_area.setObjectName("settingsScrollArea")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QScrollArea.NoFrame)
        self.body = QWidget(self.scroll_area)
        self.form = QVBoxLayout(self.body)
        UiScaleManager.for_widget(self).bind_layout(self.form, margins=(2, 12, 2, 6), spacing=14)
        self.scroll_area.setWidget(self.body)
        self.content_layout.addWidget(self.scroll_area, 1)
        self.status_label = hint("", self)
        self.status_label.hide()
        self.content_layout.addWidget(self.status_label)
        self.save_button, self.cancel_button = create_dialog_ok_cancel_buttons(self, "保存并应用", "取消")
        self.save_only_button = None
        if not compact:
            from modules.common_widgets.common_dialogs import _create_dialog_button
            self.save_only_button = _create_dialog_button("仅保存", self, "settingsSecondaryButton")
            self.save_only_button.setEnabled(False)
            self.save_only_button.clicked.connect(self.saveOnlyRequested)
            self.button_layout.addWidget(self.save_only_button)
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self.saveRequested)
        self.cancel_button.clicked.connect(self.reject)
        self.button_layout.addWidget(self.save_button)
        self.button_layout.addWidget(self.cancel_button)
        self.password_fields = []
        self._busy = False
        self._close_pending = False

    def message(self, text):
        self.status_label.setText(text)
        self.status_label.setVisible(bool(text))

    def set_busy(self, busy, text=""):
        """保持原窗口和密码直到后台恢复完毕，避免重建或提前释放。"""
        self._busy = busy
        self.body.setEnabled(not busy)
        self.save_button.setEnabled(not busy)
        if self.save_only_button is not None:
            self.save_only_button.setEnabled(not busy)
        self.cancel_button.setText("取消任务" if busy else "取消")
        self.message(text)
        if not busy and self._close_pending:
            self._close_pending = False
            self.reject()

    def _basic(self, title):
        group = QFrame(self.body)
        group.setObjectName("settingsInlinePage")
        layout = QVBoxLayout(group)
        UiScaleManager.for_widget(self).bind_layout(layout, margins=(16, 8, 16, 16), spacing=12)
        if title:
            heading = hint(title, group)
            heading.setObjectName("settingsSectionTitle")
            layout.addWidget(heading)
        self.form.addWidget(group)
        return group, layout

    def _section(self, title, tabs):
        description = {"常用设置": "信道、带宽、安全与接入网络", "不常用设置": "功率、设备访问与兼容性", "其他设置": "连接、地址与防火墙"}.get(title, "")
        section = CollapsibleSection(title, description=description, expanded=False, parent=self.body)
        navigation = SettingsTabs(tabs, section)
        section.addWidget(navigation)
        self.form.addWidget(section)
        return section, navigation

    def reject(self):
        if self._busy:
            self._close_pending = True
            self.message("正在取消并恢复原有无线，请等待恢复完成。")
            self.cancelRequested.emit()
            return
        super().reject()

    def closeEvent(self, event):
        if self._busy:
            event.ignore()
            self.reject()
        else:
            super().closeEvent(event)

    def done(self, result):
        for field in self.password_fields:
            field.clear()
        super().done(result)


class AccessPointSetupDialog(_SetupDialog):
    """名称、密码集中展示；无线、安全、接入和地区设置分层排列。"""
    def __init__(self, radio_name, band_label, parent=None, *, row=None):
        editing = row is not None
        super().__init__("编辑发射信号" if editing else "新增" + band_label + "信号", parent)
        self.radio_name, self.editing, self.row = radio_name, editing, row
        self.summary = hint(band_label + " \u00b7 " + radio_name, self.body) if editing else None
        if self.summary is not None:
            self.form.addWidget(self.summary)
        group, layout = self._basic("Wi-Fi \u540d\u79f0\u4e0e\u5bc6\u7801" if editing else "")
        self.name_edit = text_input(group, row.ssid if editing else "", "输入手机和电脑搜索到的 Wi-Fi 名称")
        add_field(layout, group, "信号名称", self.name_edit, "最多 32 字节，约 10 个汉字或 32 个英文字母。")
        self.password = PasswordInput(group, "留空保留原密码" if editing else "至少 8 位，建议使用字母和数字")
        self.password_fields.append(self.password)
        add_field(layout, group, "连接密码", self.password, "编辑时自动读取已保存的密码，默认隐藏，点眼睛可查看。")
        self.common_section, self.common_tabs = self._section("常用设置", ("无线参数", "安全设置", "接入网络"))
        page, layout = self.common_tabs.pages["无线参数"]
        self.channel = add_field(layout, page, "信道", choice(page, ()), "同一块网卡的所有信号共用信道；中继时通常跟随上级。")
        self.standard_combo = add_field(layout, page, "工作频率", choice(page, ()), "例如 Wi-Fi 4/5/6/7；只列出此网卡和频段已确认支持的标准。同网卡的所有信号共用此设置。")
        self.bandwidth_combo = add_field(layout, page, "带宽", choice(page, ()), "只列出驱动实际提供的模式。")
        page, layout = self.common_tabs.pages["安全设置"]
        self.encryption = add_field(layout, page, "加密方式", choice(page, (
            ("WPA2 个人版", "psk2"), ("WPA2 / WPA3 混合", "sae-mixed"),
            ("WPA3 个人版", "sae"), ("OWE 加密（无需密码）", "owe"), ("WPA / WPA2 兼容", "psk-mixed"), ("开放网络（无需密码）", "none"))),
            "WPA2 兼容性较好；WPA3 仍需系统认证组件支持，应用后将核实启动状态。")
        self.hidden = add_field(layout, page, "名称广播", choice(page, (("正常广播", "0"), ("隐藏名称", "1"))),
                                "隐藏名称需要设备手动填写名称，不会提高密码强度。")
        self.isolation = add_field(layout, page, "客户端隔离", choice(page, (("开启（隔离无线设备）", "1"), ("关闭（允许互相访问）", "0"))),
                                   "阻止连接同一个 Wi-Fi 的无线设备互访，可能影响投屏和打印。不等于隔离整个局域网；新增默认开启，编辑保留原值。")
        page, layout = self.common_tabs.pages["接入网络"]
        self.network = add_field(layout, page, "接入网络", choice(page, ()), "通常选择 lan，使连接设备使用路由器现有的局域网。")
        self.advanced_section, self.advanced_tabs = self._section("不常用设置", ("地区与功率", "设备访问", "兼容与省电"))
        page, layout = self.advanced_tabs.pages["地区与功率"]
        self.country = add_field(layout, page, "使用地区", choice(page, ()), "按实际所在地设置；修改会影响同网卡全部信号，信道、功率须重新检测。")
        self.power = add_field(layout, page, "发射功率", choice(page, ()), "只列出当前地区和驱动允许的功率，不建议一味调到最高。")
        page, layout = self.advanced_tabs.pages["设备访问"]
        self.client_limit = add_field(layout, page, "最多连接", choice(page, (("沿用系统设置", None),) +
            tuple((str(n) + " 台", str(n)) for n in (8, 16, 32, 64, 128, 256))), "未明确修改时保留系统原有连接数限制。")
        page, layout = self.advanced_tabs.pages["兼容与省电"]
        self.wmm = add_field(layout, page, "多媒体优化", choice(page, (("沿用系统设置", None), ("启用 WMM（建议）", "1"), ("关闭", "0"))),
                             "高速无线通常需要 WMM；关闭可能降低速度。")
        self.management = add_field(layout, page, "管理帧保护", choice(page, (("沿用系统设置", None), ("关闭", "0"), ("可选", "1"), ("强制", "2"))),
                                    "WPA3 要求强制保护；旧设备可能不兼容。")
        self.dtim = CommonSpinBox(page)
        self.dtim.setRange(0, 255)
        self.dtim.setSpecialValueText("沿用系统设置")
        add_field(layout, page, "唤醒周期", self.dtim, "DTIM 周期，通常为 1 或 2；影响省电设备接收广播的间隔。")
        self.beacon = CommonSpinBox(page)
        self.beacon.setRange(0, 65535)
        self.beacon.setSpecialValueText("沿用系统设置")
        add_field(layout, page, "信标间隔", self.beacon, "通常为 100；仅在明确了解影响时修改，单位为 1.024 毫秒。")
        self.expert_section, self.expert_tabs = self._section("高级参数", ("兼容与漫游",))
        from .signal_advanced_fields import AdvancedFields
        self.expert_fields = AdvancedFields(*self.expert_tabs.pages["兼容与漫游"])
        self.form.addWidget(hint("一般只需填写名称和密码，其余选项可沿用当前路由器设置。", self.body))
        self.form.addStretch(1)


class RelaySetupDialog(_SetupDialog):
    """验证成功后的详情；默认只展示备注、连接目标和验证状态。"""
    def __init__(self, ssid, parent=None, *, radio_name="", password="", row=None):
        super().__init__("编辑中继信号" if row else "中继详细设置", parent)
        self.row = row
        group, layout = self._basic("中继连接")
        self._basic_layout = layout
        self.name_edit = text_input(group, ssid, "例如：客厅上级网络")
        self.target_label = hint(ssid or "隐藏网络", group)
        add_field(layout, group, "信号名称", self.target_label, "实际连接的网络。")
        self.connection_label = hint("当前连接：" + (row.running if row else "待保存"), group)
        layout.addWidget(self.connection_label)
        self.other_section, self.other_tabs = self._section("其他设置", ("连接与密码", "网络设置", "高级选项", "设备身份"))
        page, layout = self.other_tabs.pages["连接与密码"]
        add_field(layout, page, "备注名称", self.name_edit, "仅用于辨认此连接，不修改上级 Wi-Fi 名称。")
        self.password = PasswordInput(group, "留空沿用原密码，点眼睛可查看" if row else "输入上级 Wi-Fi 密码")
        self.password.edit.setText(password)
        self.password_fields.append(self.password)
        add_field(self._basic_layout, group, "连接密码", self.password, "编辑时自动读取已保存的密码，默认隐藏，点眼睛可查看；不写入日志或本机配置。")
        add_field(layout, page, "无线网卡", hint(radio_name or "尚未读取", page), "上级信道会影响这块网卡同时发射的信号。")
        self.lock_bssid = add_field(layout, page, "固定接入点", choice(page, (
            ("只连接所选接入点", True), ("允许连接同名信号", False))),
            "上级有多个同名信号时，是否固定连接扫描时选中的那个。")
        page, layout = self.other_tabs.pages["网络设置"]
        self.bridge_mode = add_field(layout, page, "连接方式", choice(page, (
            ("无线上网（独立子网）", "routed"),
            ("WDS 透明桥接", "wds"),
            ("同网段中继（IPv4）", "relayd"))),
            "WDS 需要上级同时开启 WDS；IPv4 中继使用 relayd。桥接会停用所选局域网的地址分配，由上级负责。")
        self.bridge_network = add_field(layout, page, "桥接局域网", choice(page, ()), "选择需要接入上级网络的现有局域网，通常为 lan。")
        self.relay_ip = add_field(layout, page, "中继管理地址", text_input(page), "可选：relayd 在上级网段内的本地 IPv4 地址；需要避免与其他设备冲突。")
        self.bridge_hint = hint("", page)
        layout.addWidget(self.bridge_hint)
        self.protocol = add_field(layout, page, "获取地址", choice(page, (("自动获取（建议）", "dhcp"), ("手动填写", "static"))),
                                  "连接验证只要求无线认证成功，不要求获得地址。")
        self.address = add_field(layout, page, "手动地址", text_input(page, placeholder="例如 192.168.1.10"), "此路由器在上级网络中使用的地址。")
        self.netmask = add_field(layout, page, "子网掩码", text_input(page, placeholder="例如 255.255.255.0"), "仅在手动地址模式填写。")
        self.gateway = add_field(layout, page, "网关", text_input(page, placeholder="例如 192.168.1.1"), "上级网络出口地址。")
        self.dns = add_field(layout, page, "域名解析", text_input(page, placeholder="自动获取；手动填写时用空格分隔"), "留空使用上级提供的域名解析服务。")
        self.protocol.currentIndexChanged.connect(self._network_mode_changed)
        self.bridge_mode.currentIndexChanged.connect(self._network_mode_changed)
        self._network_mode_changed()
        page, layout = self.other_tabs.pages["高级选项"]
        self.zone = add_field(layout, page, "防火墙区域", choice(page, ()), "通常使用现有 wan 区域，沿用其转发和地址转换规则。")
        self.metric_combo = add_field(layout, page, "出口优先级", choice(page, (("沿用系统设置", None),) +
            tuple((str(n) + ("（优先）" if n == 0 else ""), str(n)) for n in (0, 10, 20, 50, 100, 200, 500))),
            "多条上网线路同时存在时数值越小越优先。")
        page, layout = self.other_tabs.pages["设备身份"]
        self.identity_fields = IdentityFields(page, layout, self.bridge_mode)
        self.form.addWidget(hint("这是连接上级网络的设置；需要分享 Wi-Fi 时，在“发射信号”中新增。", self.body))
        self.form.addStretch(1)

    def _network_mode_changed(self):
        mode = self.bridge_mode.currentData()
        self.bridge_network.setEnabled(mode != "routed")
        self.relay_ip.setEnabled(mode == "relayd")
        self.protocol.setEnabled(mode != "wds")
        manual = mode != "wds" and self.protocol.currentData() == "static"
        self.bridge_hint.setText({
            "routed": "保留独立局域网，通过现有外网区域上网。",
            "wds": "上级必须支持并开启 WDS；保留本机管理地址，由上级分配客户端地址。",
            "relayd": "仅中继 IPv4，不等同于透明桥接。本机原管理网段必须与上级不同；使用局域网防火墙区域。"
        }.get(mode, ""))
        for field in (self.address, self.netmask, self.gateway):
            field.setEnabled(manual)


class RelayPasswordDialog(_SetupDialog):
    """第一步输入密码；真实验证交给常驻服务，成功后再进入详情。"""
    previewRequested = Signal()

    def __init__(self, ssid, parent=None):
        super().__init__("加入网络", parent, compact=True)
        self.form.addWidget(hint("连接到：" + (ssid or "隐藏网络"), self.body))
        self.password = PasswordInput(self.body)
        self.password_fields.append(self.password)
        add_field(self.form, self.body, "连接密码", self.password, "只检查是否能连接 Wi-Fi，不检查能否上网。")
        self.form.addWidget(hint("连接验证可能短暂中断所选网卡的现有信号，开始前会提示。", self.body))
        self.save_button.setText("验证连接")
        self.preview_button, unused = create_dialog_ok_cancel_buttons(self, "预览详细设置")
        unused.deleteLater()
        self.preview_button.setProperty("dialogAcceptButton", False)
        self.preview_button.clicked.connect(self.previewRequested)
        self.button_layout.insertWidget(0, self.preview_button)
        self.preview_button.hide()
        self.form.addStretch(1)
