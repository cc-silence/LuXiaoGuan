"""路由器信号管理页面；只订阅白名单快照、发送已确认请求。

本页面不连接设备、不解析原始配置：所有清单和启停核实都来自常驻信号服务
的脱敏快照。按钮文字只在进行并核实后由快照回放翻转，失败不假装切换。
"""

from shiboken6 import delete as delete_layout_item
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from modules.capture_services.privacy_mask import mask_text
from modules.display_privacy import controller_for, masker_for, PrivacyLabel
from modules.common_widgets.bounce_scroll_area import BounceScrollArea
from modules.common_widgets.navigation_button import NavigationButton
from .signal_setup_panels import SignalSetupPanels
from modules.common_widgets.common_dialogs import ConfirmDialog, MessageDialog
from modules.main_pages.page_action_buttons import create_page_text_button


class RouterSignalPage(QWidget):
    """独立页面边界，service 是信号管理服务，info_panel 复用主页面组件。

    信号只来自 service.snapshot.inventory.rows；不读取或保存路由器敏感配置。
    """

    loginRequested = Signal()
    """未登录时用户确认去登录，容器切到设置页。"""

    def __init__(self, service, info_panel, parent=None):
        """创建无网络副作用的页面；信息通道只订阅本模块服务。

        参数：service 是常驻 RouterSignalService；info_panel 是共享信息面板。
        返回值：无。副作用：创建控件并订阅服务快照，不执行远程命令。
        """
        super().__init__(parent)
        self.service = service
        self.info_panel = info_panel
        self.setObjectName("openwrtSignalPage")
        # 名称隐藏是纯本机显示偏好，每次打开默认关闭，不保存、不恢复。
        self._mask_names = False
        self._privacy = controller_for(self)
        self._name_labels = []
        self._row_widgets = {}
        self._group_labels = {}
        self._layout_keys = ()
        self._ssid_values = set()
        if self._privacy is not None:
            self._privacy.changed.connect(self._refresh_display_privacy)
        root = QVBoxLayout(self)
        from modules.ui_scale_manager import UiScaleManager
        scale = UiScaleManager.for_widget(self)
        scale.bind_layout(root, margins=(0, 0, 0, 0), spacing=8)
        navigation = QHBoxLayout()
        scale.bind_layout(navigation, margins=(12, 8, 12, 0), spacing=6)
        self.navigation_group = QButtonGroup(self)
        self.navigation_buttons = []
        for index, title in enumerate(("已配置信号", "发射信号", "无线桥接")):
            button = NavigationButton(title, self)
            button.setObjectName("captureSignalNavigation")
            button.setCheckable(True)
            button.setAutoDefault(False)
            button.setCursor(Qt.PointingHandCursor)
            button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            scale.bind_metrics(button, fixed_height=34)
            self.navigation_group.addButton(button, index)
            self.navigation_buttons.append(button)
            navigation.addWidget(button, 1)
        root.addLayout(navigation)
        body = QHBoxLayout()
        scale.bind_layout(body, margins=(0, 0, 0, 0), spacing=8)
        root.addLayout(body, 1)

        left = QWidget(self)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)

        # 顶部操作行：刷新读取、停止当前核实。按钮不参与互斥，仅按连接状态启用。
        self.inventory_toolbar = QWidget(left)
        top_row = QHBoxLayout(self.inventory_toolbar)
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(8)
        self.refresh_button = create_page_text_button(
            left, "刷新信号列表", "重新读取路由器已配置的全部信号（含禁用项）。"
        )
        from modules.ui_scale_manager import UiScaleManager
        self.refresh_button.setProperty("signalPrimaryAction", True)
        UiScaleManager.for_widget(self).bind_metrics(self.refresh_button, min_height=38, min_width=132)
        self.install_button = create_page_text_button(
            left, "安装信号依赖",
            "缺少管理信号所需的软件时，点这里自动安装到路由器。安装前会列出清单；路由器不能上网时可由这台电脑下载。安装后自动刷新列表，不修改 Wi-Fi 名称或密码。",
        )
        UiScaleManager.for_widget(self).bind_metrics(self.install_button, fixed_height=26)
        self.install_button.hide()
        self.status_label = PrivacyLabel("尚未读取路由器无线配置。", left)
        self.status_label.setObjectName("captureDescription")
        self.status_label.setWordWrap(True)
        self.mask_names_check = QCheckBox("隐藏信号名称", left)
        self.mask_names_check.setObjectName("captureCheckBox")
        self.mask_names_check.setToolTip(
            "勾选后，列表里的 WiFi 名称只显示圆点，不分自己发的还是连上级的；启停操作不受影响，确认框里仍能看到频段和网卡来分辨是哪一个。"
        )
        self.details_check = QCheckBox("显示信号详情", left)
        self.details_check.setObjectName("captureCheckBox")
        self.details_check.setToolTip("勾选后读取接口、信道、带宽配置、加密和已连接设备；只读不改路由器。未勾选时不读取详情。")
        self.details_check.setChecked(bool(getattr(service, 'details_enabled', False)))
        top_row.addWidget(self.mask_names_check)
        top_row.addWidget(self.details_check)
        top_row.addWidget(self.status_label, 1)
        top_row.addWidget(self.install_button)
        top_row.addWidget(self.refresh_button)
        left_layout.addWidget(self.inventory_toolbar)

        self.stack = QStackedWidget(left)
        self.content = QWidget(self.stack)
        self.content.setObjectName("openwrtCaptureContent")
        self.rows_layout = QVBoxLayout(self.content)
        scale.bind_layout(self.rows_layout, margins=(10, 8, 10, 14), spacing=4)
        self.setup_panels = SignalSetupPanels(self)
        from .signal_pending_actions import PendingConfigActions
        self.pending_actions = PendingConfigActions(self)
        self.inventory_footer = QWidget(left)
        self.inventory_footer.setObjectName("captureSignalMaintenanceActions")
        footer_layout = QHBoxLayout(self.inventory_footer)
        scale.bind_layout(footer_layout, margins=(10, 0, 10, 8), spacing=8)
        from .signal_relocation_actions import RelocationActions
        self.relocation_actions = RelocationActions(self)
        from .signal_orphan_actions import OrphanInterfaceActions
        self.orphan_actions = OrphanInterfaceActions(self)
        for index, button in enumerate((self.relocation_actions.button, self.orphan_actions.button)):
            button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            scale.bind_metrics(button, fixed_height=40)
            footer_layout.setStretch(index, 1)

        self.page_scrolls = []
        for content in (self.content, self.setup_panels.transmit_page, self.setup_panels.relay_page):
            scroll = BounceScrollArea(self.stack)
            scroll.setObjectName("captureScrollArea")
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QScrollArea.NoFrame)
            if content is self.setup_panels.relay_page:
                from modules.common_widgets.auto_hide_scroll_bar import AutoHideScrollBar
                scroll.setVerticalScrollBar(AutoHideScrollBar(Qt.Vertical, scroll, reserve_space=False))
                scroll.setHorizontalScrollBar(AutoHideScrollBar(Qt.Horizontal, scroll, reserve_space=False))
            scroll.setWidget(content)
            self.stack.addWidget(scroll)
            self.page_scrolls.append(scroll)
        self.scroll = self.page_scrolls[0]
        self.navigation_group.idClicked.connect(self.stack.setCurrentIndex)
        self.stack.currentChanged.connect(self._navigation_changed)
        self.navigation_buttons[0].setChecked(True)
        left_layout.addWidget(self.stack, 1)
        left_layout.addWidget(self.inventory_footer)
        if info_panel is not None:
            from modules.common_widgets.info_panel_dock import InfoPanelDock
            self.info_dock = InfoPanelDock(left, info_panel, Qt.Horizontal, self)
            body.addWidget(self.info_dock, 1)
        else:
            body.addWidget(left, 1)

        self.refresh_button.clicked.connect(self._on_refresh)
        self.install_button.clicked.connect(self._on_install)
        self.mask_names_check.toggled.connect(lambda _checked: self._on_mask_toggled())
        self.details_check.toggled.connect(self._on_details_toggled)
        service.snapshotChanged.connect(self.apply_snapshot)
        service.reconnectRequired.connect(self._on_reconnect_required)
        if info_panel is not None:
            service.messageGenerated.connect(info_panel.append_info)
            for text, level in service.messages:
                info_panel.append_info(text, level)
        self.apply_snapshot(service.snapshot)

    def _navigation_changed(self, index):
        self.navigation_buttons[index].setChecked(True)
        self.inventory_toolbar.setVisible(index == 0)
        self.inventory_footer.setVisible(index == 0)

    def _on_refresh(self):
        """主线程申请只读刷新；未登录先弹窗指引去登录，不断按钮。

        自动安装依赖期间此按钮文字已是“取消安装”，点击改为请求取消安装。
        """

        if self.service.snapshot.busy and self.service.active:
            self._on_stop()
            return
        if bool(getattr(self.service.snapshot, "installing", False)):
            self._request_cancel("依赖安装")
            return
        if not self.service.snapshot.connected:
            if MessageDialog(
                "请先登录路由器",
                "刷新信号列表前，请先在“路由器登录”中登录 OpenWrt 路由器。",
                self,
            ).exec() == MessageDialog.Accepted:
                self.loginRequested.emit()
            return
        if not self.service.refresh():
            self.status_label.setText("无法刷新：未连接、已有任务运行或服务正在退出。")
            if self.info_panel is not None:
                self.info_panel.append_info(
                    "无法刷新信号列表：请先连接路由器，并等待其他任务结束。",
                    "warning",
                )

    def _request_cancel(self, what):
        """请求取消后台任务；按钮状态由服务快照回放，不在这里断言成功。"""

        if not self.service.cancel():
            if self.info_panel is not None:
                self.info_panel.append_info(f"当前没有正在进行的{what}。", "info")

    def _on_stop(self):
        """请求停止当前核实；结果由服务快照回放，不在这里断言成功。"""

        self._request_cancel("信号读取或核实")

    def _on_mask_toggled(self):
        """开关变化只改当次显示，不保存；下次打开页面恢复默认关闭。"""

        self._mask_names = bool(self.mask_names_check.isChecked())
        self._refresh_display_privacy()

    def _refresh_display_privacy(self):
        from PySide6.QtCore import QSignalBlocker
        forced = bool(self._privacy and self._privacy.enabled)
        with QSignalBlocker(self.mask_names_check):
            self.mask_names_check.setChecked(forced or self._mask_names)
        self.mask_names_check.setEnabled(not forced)
        for label, row in self._name_labels:
            label.setText(self._shown_name(row))
        self.setup_panels.refresh_privacy()
        if self.info_panel is not None:
            self.info_panel.set_local_masker(masker_for(self._ssid_values if self._mask_names else (), mask_mac=False))

    def _request_edit(self, row):
        """只在当前清单有效时打开对应编辑预览；不向设备提交配置。"""
        snapshot = self.service.snapshot
        if snapshot.connected and not snapshot.busy and not snapshot.stale and not row.reason:
            self.setup_panels.edit(row)

    def _shown_name(self, row):
        """显示用名称：开关打开时全部行只显示圆点；操作仍用配置段名。"""

        return mask_text(self._display_name(row), self._mask_names or bool(self._privacy and self._privacy.enabled))

    def _on_install(self):
        """主线程确认后安装信号依赖；装完服务自动重读，不在这里断言成功。"""

        dialog = ConfirmDialog(
            "安装信号依赖",
            "路由器缺少信号管理运行环境，\u8def\u5c0f\u7ba1 可以自动安装：\n\n"
            "lua、libuci-lua、libubus-lua、luci-lib-jsonc\n\n"
            "官方软件源小包（共约几百 KB），只加解释器和库文件，不改任何配置、"
            "不断网。路由器能上网时直接安装；路由器上不了网时自动使用 "
            "\u8def\u5c0f\u7ba1 下载安装（需要这台电脑能联网，\u8def\u5c0f\u7ba1 下载后传到路由器安装），"
            "完成后自动重新读取列表。",
            self,
            yes_text="确认安装",
            no_text="取消",
        )
        if dialog.exec() != ConfirmDialog.Accepted:
            return
        if not self.service.install_dependencies():
            self.status_label.setText("安装未开始：连接已断开或有其他任务运行。")
            if self.info_panel is not None:
                self.info_panel.append_info(
                    "依赖安装未开始：请确认已连接，并等待其他任务结束。",
                    "warning",
                )

    def _confirm_inventory_action(self, dialog, inventory):
        try:
            accepted = dialog.exec() == ConfirmDialog.Accepted
        finally:
            dialog.deleteLater()
        if not accepted:
            return False
        state = self.service.snapshot
        if state.inventory is inventory and state.connected and not state.busy and not state.stale:
            return True
        self.status_label.setText("列表或连接已变化，请刷新后重新操作。")
        return False

    def _request_change(self, row):
        """主线程确认风险后发起单行启停；按钮翻转只在核实后由快照完成。

        参数：row 是当前 RouterSignal。返回值：无。副作用：可能向路由器提交
        无线配置并重载该 radio，可能短暂影响该频段 Wi-Fi 或断开 SSH。
        """
        inventory = self.service.snapshot.inventory
        if not any(current is row for current in inventory.rows):
            return
        if row.reason:
            if self.info_panel is not None:
                self.info_panel.append_info(f"{self._shown_name(row)}：{row.reason}", "warning")
            return
        target_enabled = row.disabled or row.radio_disabled
        parent_note = (f"父网卡 {row.radio} 已关闭，本次将同时开启父网卡和当前信号。\n"
                       "该网卡上其他已启用的信号也可能恢复运行；仍处于禁用的其他信号保持禁用。\n\n" if row.radio_disabled else "")
        verb = "启用" if target_enabled else "禁用"
        name = self._shown_name(row)
        bridge_note = ("此连接使用桥接：停用时恢复本机原有地址分配，启用时再交由上级网络分配；失败时尝试回滚。\n\n" if row.managed_bridge else "")
        # 诚实说明：启用/禁用会提交 UCI 并重载对应 radio，可能短暂影响同频段
        # 其他信号，甚至让通过该 Wi-Fi 连接的 SSH 暂时断开。
        dialog = ConfirmDialog(
            f"{verb}信号：{name}",
            f"确认要{verb}信号“{name}”吗？\n\n"
            f"频段：{self._band_text(row)}，所属无线：" f"{row.radio}\n"
            "\u8def\u5c0f\u7ba1 会保存到路由器（重启后仍生效）并立即重新加载该无线网卡。\n\n"
            "注意：重载期间该频段 Wi-Fi 可能短暂中断；如果电脑正通过该信号连接"
            "路由器，SSH 也可能断开，断开后请重新连接再核对状态。\n\n"
            "\u8def\u5c0f\u7ba1 不会修改信号名称、密码或加密方式。" + "\n\n" + parent_note + bridge_note,
            self,
            yes_text=f"确认{verb}",
            no_text="取消",
        )
        if not self._confirm_inventory_action(dialog, inventory):
            return
        if not self.service.change(row.section, target_enabled, confirmed=True):
            self.status_label.setText("操作未开始：快照已过期、有其他任务或连接已断开。")
            if self.info_panel is not None:
                self.info_panel.append_info(
                    "操作未开始：请等待其他任务结束，并刷新列表后重试。",
                    "warning",
                )

    def apply_snapshot(self, snapshot):
        """回放白名单快照：重建两组列表并同步按钮可用性，不修改配置。"""

        values = [self._display_name(row) for row in snapshot.inventory.rows]
        if len(self._ssid_values) < 50000:
            self._ssid_values.update(values[:50000 - len(self._ssid_values)])
        if self._privacy is not None:
            self._privacy.register_values(value for row in snapshot.inventory.rows if row.details is not None
                                          for client in row.details.clients for value in (client.name, client.ip))
            self._privacy.register_snapshot(snapshot)
            self._privacy.register_values(values)
        self.status_label.setText(snapshot.detail)
        # 刷新按钮只看忙闲：未登录不断按钮，点后弹窗指引去登录。
        # stale 只锁单行启停：刷新是解除 stale 的唯一入口，连接空闲时必须可用。
        # 依赖自动安装期间把“刷新信号列表”变成“取消安装”，点一次只取消本安装。
        committing = getattr(getattr(getattr(self.service, "setup", None), "snapshot", None), "action", "") == "pending_commit"
        installing = bool(getattr(snapshot, "installing", False))
        idle = bool(snapshot.connected and not snapshot.busy)
        if committing:
            refresh_text = "正在提交配置"
            refresh_tip = "正在保存并核对结果，请等待完成。"
        elif installing:
            refresh_text = "取消安装"
            refresh_tip = "请求停止本次信号依赖自动安装；当前步骤完成后停止，回到可刷新状态。"
        elif snapshot.busy and self.service.active:
            refresh_text = "停止核实"
            refresh_tip = "请求停止本次信号读取或无线任务；必要的恢复完成后回到刷新状态。"
        elif snapshot.busy:
            refresh_text = f"{snapshot.busy_label or '忙碌中'}，无法使用"
            refresh_tip = "有抓包或验证任务正在运行，结束后自动恢复；无需点击，点了也没用。"
        else:
            refresh_text = "刷新信号列表"
            refresh_tip = "重新读取路由器已配置的全部信号（含禁用项）。"
        if self.refresh_button.text() != refresh_text:
            self.refresh_button.setText(refresh_text)
            self.refresh_button.setToolTip(refresh_tip)
        self.refresh_button.setEnabled(not committing and bool(installing or self.service.active or not snapshot.busy))
        # 安装入口只在缺依赖且能立即开工时出现，其他时间隐藏不占注意力。
        # 出现即置绿（QSS 用 needsDependencyInstall 区分），给用户明确指引。
        needs_install = bool(getattr(self.service, "dependencies_missing", False) and idle)
        self.install_button.setVisible(needs_install)
        if bool(self.install_button.property("needsDependencyInstall")) != needs_install:
            self.install_button.setProperty("needsDependencyInstall", needs_install)
            self.install_button.style().unpolish(self.install_button)
            self.install_button.style().polish(self.install_button)
        self._rebuild_rows(snapshot)
        self.setup_panels.apply_snapshot(snapshot)
        self._refresh_display_privacy()

    def _on_details_toggled(self, checked):
        """勾选后才读取和显示详情，取消后立即收起。"""
        self.service.set_details_enabled(checked)
        self._rebuild_rows(self.service.snapshot)

    def _request_delete(self, row):
        actions = self.setup_panels.actions
        if actions is not None:
            actions.delete(row)

    def _rebuild_rows(self, snapshot):
        """按配置段复用行，仅增删真正变化的控件。"""
        from .signal_row import SignalRowWidget
        rows = snapshot.inventory.rows
        keys = {row.section for row in rows}
        for key in set(self._row_widgets) - keys:
            widget = self._row_widgets.pop(key)
            self.rows_layout.removeWidget(widget)
            widget.hide()
            widget.deleteLater()
        ap = [r for r in rows if r.mode == 'ap' and not r.reason]
        sta = [r for r in rows if r.mode == 'sta' and not r.reason]
        groups = [('自身发射的信号（AP）',ap),
                  ('连接上级网络的信号（STA · 2.4G）',[r for r in sta if (r.runtime_band or r.band) == '2g']),
                  ('连接上级网络的信号（STA · 5G）',[r for r in sta if (r.runtime_band or r.band) == '5g'])]
        other = [r for r in sta if (r.runtime_band or r.band) not in ('2g','5g')]
        unsupported = [r for r in rows if r.reason]
        if other: groups.append(('连接上级网络的信号（STA · 未知频段）',other))
        if unsupported: groups.append(('暂不支持操作的信号',unsupported))
        desired, used_labels = [], set()
        self._name_labels.clear()
        def label(key, text, object_name):
            used_labels.add(key)
            if key not in self._group_labels:
                w = QLabel(text, self.content)
                w.setObjectName(object_name)
                self._group_labels[key] = w
            return self._group_labels[key]
        for title, members in groups:
            desired.append(label(title,title,'captureSignalSectionTitle'))
            if not members:
                desired.append(label(title+'empty','（无）','captureSignalEmpty'))
            for row in sorted(members,key=self._activity_key):
                widget = self._row_widgets.get(row.section)
                if widget is None:
                    widget = self._row_widgets[row.section] = SignalRowWidget(self,row)
                widget.update_row(row,snapshot,snapshot.connected and not row.reason,self.details_check.isChecked())
                self._name_labels.append((widget.name_label,row))
                desired.append(widget)
        if not rows:
            desired.append(label('placeholder','尚未读取到无线配置；连接后点击“刷新信号列表”。','captureDescription'))
        for key in set(self._group_labels) - used_labels:
            w = self._group_labels.pop(key)
            self.rows_layout.removeWidget(w)
            w.hide()
            w.deleteLater()
        order = tuple(id(w) for w in desired)
        if order != self._layout_keys:
            # takeAt transfers ownership: delete the item, preserving its widget.
            # A leaked QWidgetItem keeps stale height-for-width caches on reused rows.
            while self.rows_layout.count():
                delete_layout_item(self.rows_layout.takeAt(0))
            for widget in desired:
                self.rows_layout.addWidget(widget)
                widget.show()
            self.rows_layout.addStretch(1)
            self._layout_keys = order

    @staticmethod
    def _display_name(row):
        """返回用户可读名称：SSID 为空时用配置段名替代。"""
        return row.runtime_ssid.strip() or row.ssid.strip() or row.section or "（无名称）"

    @staticmethod
    def _band_text(row):
        value = row.runtime_band or row.band
        band = {"2g": "2.4G", "5g": "5G", "6g": "6G"}.get(value, value or "未知频段")
        return f"{band} · {row.radio}"

    @staticmethod
    def _band_label(row, parent):
        label = QLabel(RouterSignalPage._band_text(row), parent)
        label.setObjectName("captureDescription")
        return label

    @staticmethod
    def _state_text(row):
        """组合“配置状态 + 实际运行状态”，避免把未运行误显示成已禁用。

        不支持的行直接把原因缀在状态后，按钮为什么点不了用户一眼可见。
        """
        config = "已禁用" if row.disabled else "已启用"
        if row.radio_disabled:
            run = "父 radio 已关闭"
        elif row.running == "active":
            run = "运行中"
        elif row.running == "inactive":
            run = "未运行"
        else:
            run = "状态未知"
        text = f"{config} · {run}"
        if row.runtime_note:
            text += f"（{row.runtime_note}）"
        elif row.running == "inactive" and not row.disabled and not row.radio_disabled:
            text += "（原因尚未确定，请查看路由器无线日志）"
        if row.reason:
            text += f"（{row.reason}）"
        return text

    @staticmethod
    def _state_kind(row):
        if row.disabled or row.radio_disabled:
            return "blocked"
        if row.running == "active":
            return "ready"
        return "unknown"

    @staticmethod
    def _activity_key(row):
        """组内置顶键：已启用且运行中排前面，其余保持原有相对顺序。"""
        return 0 if (not row.disabled and row.running == "active") else 1

    @staticmethod
    def _needs_repair(row):
        """运行态修复 eligibility：配置已启用、明确未运行、其余可用。

        状态未知的不进修复（可能是读取能力问题，乱重载帮不上忙）；已禁用、
        父 radio 关闭、有原因的行各回各家。
        """
        return (not row.disabled and not row.radio_disabled
                and not row.reason and row.running == "inactive")

    @staticmethod
    def _button_text(row):
        """互斥核心：已禁用显示“启用”，已启用未运行显示“重新启用”，其余显示“禁用”。"""
        if row.radio_disabled:
            return "启用"
        if RouterSignalPage._needs_repair(row):
            return "重新启用"
        return "启用" if row.disabled else "禁用"

    def _request_repair(self, row):
        """主线程确认后发起运行态修复；不改任何配置，只重载 radio 并核实回来。

        参数：row 是当前 RouterSignal。副作用：重载对应 radio，该频段 Wi-Fi
        可能短暂中断；按钮翻转只在核实后由快照完成。
        """
        inventory = self.service.snapshot.inventory
        if not any(current is row for current in inventory.rows):
            return
        name = self._shown_name(row)
        dialog = ConfirmDialog(
            f"重新启用信号：{name}",
            f"信号“{name}”配置是启用的，但实际没有运行起来（原因尚未确定）。\n\n"
            f"频段：{self._band_text(row)}，所属无线：{row.radio}\n"
            "\u8def\u5c0f\u7ba1 只重新加载该无线网卡并核实接口回来，"
            "不修改信号名称、密码、加密方式，不增删任何配置。\n\n"
            "注意：重载期间该频段 Wi-Fi 可能短暂中断；如果电脑正通过该信号连接"
            "路由器，SSH 也可能断开，断开后请重新连接再核对状态。",
            self,
            yes_text="确认修复",
            no_text="取消",
        )
        if not self._confirm_inventory_action(dialog, inventory):
            return
        if not self.service.repair(row.section, confirmed=True):
            self.status_label.setText("操作未开始：快照已过期、有其他任务或连接已断开。")
            if self.info_panel is not None:
                self.info_panel.append_info(
                    "操作未开始：请等待其他任务结束，并刷新列表后重试。",
                    "warning",
                )

    def _on_reconnect_required(self, summary):
        """远程连接断开时弹窗指引重登；确定跳登录页，取消只留信息。"""

        if ConfirmDialog(
            "远程连接已断开",
            f"{summary}\n\n是否前往“路由器登录”页重新登录？",
            self,
            yes_text="去登录",
            no_text="取消",
        ).exec() == ConfirmDialog.Accepted:
            self.loginRequested.emit()

    def dispose(self):
        """仅断开页面订阅，不停止常驻任务。"""
        self.relocation_actions.dispose()
        self.orphan_actions.dispose()
        self.pending_actions.dispose()
        self.setup_panels.dispose()
        try:
            self.service.snapshotChanged.disconnect(self.apply_snapshot)
            self.service.reconnectRequired.disconnect(self._on_reconnect_required)
            if self.info_panel is not None:
                self.service.messageGenerated.disconnect(self.info_panel.append_info)
        except (RuntimeError, TypeError):
            pass
