"""信号页面的真实动作协调；界面不直接执行远程命令。"""
from dataclasses import replace
from PySide6.QtCore import QObject
from modules.common_widgets.common_dialogs import ConfirmDialog, MessageDialog
from modules.capture_services.openwrt.wireless_setup_models import WirelessNetwork
from .signal_setup_dialogs import AccessPointSetupDialog, RelaySetupDialog
from .signal_setup_editor import configure_ap, configure_relay, ap_request, relay_request


class SignalSetupActions(QObject):
    """将一次对话框操作绑定到常驻服务，结果不会误投到下一次编辑。"""
    def __init__(self, panels, service):
        super().__init__(panels.page)
        self.panels, self.service = panels, service
        self.pending = None
        self._resume_editor = None
        service.completed.connect(self._completed)
        service.snapshotChanged.connect(self._environment_changed)

    def environment(self, resume=None):
        """编辑缺少条件时给出可操作的说明，不静默丢弃点击。"""
        state = self.service.snapshot
        env = state.environment
        if env and env.ready:
            return env
        if self._resume_editor is not None:
            return None
        if env and env.missing:
            text = ("编辑前需要补齐以下无线管理组件：\n\n" + "、".join(env.missing)
                    + "\n\n保留已有无线软件和驱动；安装计划涉及替换已有软件时停止。安装成功后自动打开编辑窗口。")
            if ConfirmDialog("补齐组件后编辑", text, self.panels.page,
                             yes_text="安装并继续", no_text="取消").exec() == ConfirmDialog.Accepted:
                self._resume_editor = resume
                if not self.service.install(env.missing):
                    self._resume_editor = None
                    MessageDialog("暂时无法编辑", "安装未开始，请等待当前任务结束后重试。", self.panels.page).exec()
        else:
            reason = "；".join(env.issues) if env and env.issues else state.detail
            MessageDialog("暂时无法编辑", reason + "\n请在发射信号面板重新检测无线组件。", self.panels.page).exec()
        return None

    def _environment_changed(self, state):
        """仅在安装任务结束且环境就绪后继续本次编辑。"""
        if self._resume_editor is None or state.busy:
            return
        resume, self._resume_editor = self._resume_editor, None
        if state.environment and state.environment.ready:
            resume()

    def _confirm(self, dialog, title, message):
        confirmation = ConfirmDialog(title, message, dialog, yes_text="继续", no_text="取消")
        try:
            return confirmation.exec() == ConfirmDialog.Accepted
        finally:
            confirmation.deleteLater()

    def _current_dialog(self, dialog):
        identity = getattr(dialog, "_router_connection", None)
        if identity is not None and self.service._connection_identity() is identity:
            return True
        dialog.message("连接已断开或路由器已变化，请关闭此窗口并重新打开设置。")
        return False

    def _start(self, dialog, action, start, callback):
        if not self._current_dialog(dialog):
            return
        if self.pending:
            return
        self.pending = (action, dialog, callback)
        dialog.set_busy(True, {"password": "正在读取已保存的密码……", "install": "正在安装中继组件……", "save": "正在保存并核实无线状态……", "save_only": "正在保存配置，不重载网络……"}[action])
        if not start():
            self.pending = None
            dialog.set_busy(False, "任务没有开始：请等待其他任务结束，并检查连接。")

    def _bind_stored_password(self, dialog, env, row):
        dialog._router_connection = self.service._connection_identity()
        if row is None or not dialog._old.get("has_key"):
            return lambda: None
        def load(visible=False):
            def loaded(value):
                dialog.password.show_stored_password(value.password, visible=visible)
                if not value.password:
                    dialog.message("这条连接没有保存可读取的密码。")
            self._start(dialog, "password", lambda: self.service.read_password(row.section, env.data["revision"]), loaded)
        dialog.password.storedPasswordRequested.connect(lambda: load(True))
        return load

    def access_point(self, radio, band, row=None):
        env = self.environment(lambda: self.access_point(radio, band, row))
        if env is None:
            return
        dialog = AccessPointSetupDialog(radio, band, self.panels.page, row=row)
        configure_ap(dialog, env)
        load_password = self._bind_stored_password(dialog, env, row)
        dialog.cancelRequested.connect(self.service.cancel)
        def save(apply=True):
            try:
                request = replace(ap_request(dialog), revision=env.data["revision"])
            except ValueError as exc:
                dialog.message(str(exc))
                return
            self._save(dialog, request, apply=apply)
        dialog.saveRequested.connect(save)
        dialog.saveOnlyRequested.connect(lambda: save(False))
        self.panels._show(dialog)
        load_password()

    def join(self, network):
        self.relay(network)

    def relay(self, network, *, row=None, password=""):
        env = self.environment(lambda: self.relay(network, row=row, password=password))
        if env is None:
            return
        dialog = RelaySetupDialog(network.ssid, self.panels.page, radio_name=network.radio, password=password, row=row)
        configure_relay(dialog, env)
        load_password = self._bind_stored_password(dialog, env, row)
        if row:
            old = dialog._old
            dialog.lock_bssid.setCurrentIndex(0 if old.get("bssid") else 1)
            dialog.connection_label.setText("当前状态：" + {"active": "运行中", "inactive": "未运行"}.get(row.running, "尚未确认"))
        dialog.cancelRequested.connect(self.service.cancel)
        context = {"environment": env}
        def save(apply=True):
            try:
                request = replace(relay_request(dialog, network), revision=context["environment"].data["revision"])
            except ValueError as exc:
                dialog.message(str(exc))
                return
            if request.options.get("bridge_mode") == "relayd" and not context["environment"].data.get("features", {}).get("relayd"):
                if self._confirm(dialog, "安装 IPv4 中继组件", "需要安装 relayd。仅补齐此组件，不替换现有无线软件。安装完成后请再确认保存。"):
                    def installed(value):
                        context["environment"] = value
                        dialog.message("中继组件已检测，请确认详细设置后保存。")
                    self._start(dialog, "install", lambda: self.service.install(("relayd",)), installed)
                return
            self._save(dialog, request, apply=apply)
        dialog.saveRequested.connect(save)
        dialog.saveOnlyRequested.connect(lambda: save(False))
        self.panels._show(dialog)
        load_password()

    def delete(self, row):
        state = self.service.signals.snapshot
        if not state.connected or state.busy or state.stale:
            return
        current = next((r for r in state.inventory.rows if r.section == row.section), None)
        if current is None or (current.ssid, current.mode, current.radio) != (row.ssid, row.mode, row.radio):
            MessageDialog("无法删除", "信号已变化，请刷新后重试。", self.panels.page).exec()
            return
        if self.environment(lambda: self.delete(row)) is None:
            return
        revision = state.inventory.revision
        identity = self.service._connection_identity()
        name = self.panels.page._shown_name(row)
        text = "确认删除信号“" + name + "”？\n\n删除后将不再发射或连接该信号，重启后也不会恢复。同网卡的无线可能短暂中断，请确保电脑不依赖该信号管理路由器。"
        if row.managed_bridge:
            text += "\n\n将同时清理本软件创建的中继配置，恢复原有地址分配；共用或无法确认归属的网络会保留。"
        text += "\n\n失败时尝试回滚；管理连接中断时，请等待设备自动回滚后重新连接。"
        if ConfirmDialog("删除信号", text, self.panels.page, yes_text="确认删除", no_text="取消").exec() != ConfirmDialog.Accepted:
            return
        if self.service._connection_identity() is not identity:
            MessageDialog("删除未开始", "连接已变化，请刷新后重试。", self.panels.page).exec()
            return
        if not self.service.delete(row.section, revision):
            MessageDialog("删除未开始", "请等待其他任务结束并检查连接。", self.panels.page).exec()

    def edit(self, row):
        if row.mode == "ap":
            self.access_point(row.radio, self.panels.page._band_text(row).split(" · ")[0], row)
            return
        env = self.environment(lambda: self.edit(row))
        if env is None:
            return
        old = next((r for r in env.data["rows"] if r["section"] == row.section), {})
        radio = next((r for r in env.data["radios"] if r["name"] == row.radio), {})
        channel = int(radio["channel"]) if str(radio.get("channel", "")).isdigit() else None
        network = WirelessNetwork(row.radio, old.get("bssid", ""), row.ssid, channel, row.band, None, old.get("encryption", "psk2"))
        self.relay(network, row=row)

    def _save(self, dialog, request, *, apply=True):
        if not self._current_dialog(dialog):
            return
        bridge_note = ""
        if request.options.get("identity_action", "keep") != "keep":
            bridge_note = "本次会更改中继身份（MAC 地址或上报名称），可能影响上级的固定地址绑定。\n\n"
        if request.options.get("bridge_mode") in ("wds", "relayd"):
            bridge_note += "桥接会停用 " + str(request.options.get("network")) + " 的地址分配，由上级网络负责。保留原管理地址；建议通过网线管理。\n\n"
        if apply and not self._confirm(dialog, "保存并应用", bridge_note + "设置将应用到路由器，无线可能短暂中断。确认运行后保存，失败时尝试恢复。"):
            return
        def saved(_value):
            dialog.accept()
            self.panels.empty_hint.setText("设置已保存并确认无线运行。" if apply else
                "信号已保存为禁用，需手动启用。")
        start = (lambda: self.service.save(request)) if apply else (lambda: self.service.save(request, apply=False))
        self._start(dialog, "save" if apply else "save_only", start, saved)

    def _completed(self, result):
        if self.pending is None or result.action != self.pending[0]:
            return
        _action, dialog, callback = self.pending
        self.pending = None
        closing = dialog._close_pending
        dialog.set_busy(False, result.error)
        if not result.error and not closing and self._current_dialog(dialog):
            callback(result.value)

    def dispose(self):
        """释放页面回调；已开始的恢复由常驻服务完成。"""
        if self.pending:
            self.service.cancel()
        self.pending = None
        self._resume_editor = None
        self.service.completed.disconnect(self._completed)
        self.service.snapshotChanged.disconnect(self._environment_changed)
