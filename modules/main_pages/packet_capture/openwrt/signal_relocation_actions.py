"""Relocation preview and explicit confirmation, sharing the standard dialog and task service."""
from copy import deepcopy
from PySide6.QtCore import QObject
from .ap_batch_dialog import AccessPointBatchDialog
from modules.capture_services.openwrt.relocation_access_points import access_point_snapshot
from .page_feedback import publish_page_message
from .settings_impacts import DISCONNECT_WARNING
from modules.common_widgets.common_dialogs import ConfirmDialog, MessageDialog
from modules.main_pages.page_action_buttons import create_page_text_button, set_page_action_enabled


MAC_SKIP_REASONS = {
    "protocol": "网络类型不适合更换 MAC",
    "device_missing": "当前没有可确认的实际网卡",
    "device_type": "使用了 VLAN 或隧道等特殊设备",
    "mac_missing": "无法读取有效的当前 MAC",
    "not_ethernet": "实际网卡不适合按有线网卡更改地址",
}


def relocation_identity_editor(review, parent):
    """Build the first relocation dialog from the shared hotspot editor."""
    return AccessPointBatchDialog(access_point_snapshot(review), parent,
        mac_editable=False, title="搬家清理：选择保留或清理自建热点",
        description="可保留下面的自建热点并改名，也可选择“不保留，直接清理”。保留时，新名称可手动修改或再次随机；MAC 地址自动生成，密码和开关状态不变。两种选择都会先预览再确认。",
        continue_text="保留并改名", discard_text="不保留，直接清理")


class RelocationActions(QObject):
    def __init__(self, page, container):
        super().__init__(page)
        self.page = page
        self.service = getattr(page.service, "setup", None)
        self._waiting = False
        self._identity = None
        self._preview_preserve = True
        self.button = create_page_text_button(container, "搬家清理",
            "清理旧上级 Wi-Fi 和桥接；自建热点可选择保留并改名，或不保留直接清理。先预览再确认，保留管理地址和登录密码。")
        self.button.setProperty("dangerAction", True)
        container.layout().addWidget(self.button)
        self.button.clicked.connect(self.review)
        page.service.snapshotChanged.connect(self._sync)
        if self.service is not None:
            self.service.snapshotChanged.connect(self._sync)
            self.service.completed.connect(self._completed)
        self._sync()

    def _sync(self, *_):
        snapshot = self.page.service.snapshot
        supported = self.service is not None and hasattr(self.service, "relocation_preview")
        if self._waiting and (not snapshot.connected or self.service._connection_identity() is not self._identity):
            self._waiting = False
        set_page_action_enabled(self.button, bool(supported and snapshot.connected and not snapshot.busy
                                   and not self.service.active and not self._waiting))
        if getattr(self.page,'prompt_for_router_login',False) and not snapshot.connected:
            set_page_action_enabled(self.button,not snapshot.busy and not self._waiting)

    def review(self):
        from .router_login_prompt import require_router_login
        if not require_router_login(self.page,self.page.service.snapshot.connected):return
        if not self.button.isEnabled():
            return
        self._identity = self.service._connection_identity()
        self._waiting = True
        self._preview_preserve = True
        if not self.service.relocation_preview():
            self._waiting = False
            publish_page_message(self.page,"请等待当前任务结束后重试。")
        self._sync()

    def _discard_preview(self):
        if self.service._connection_identity() is not self._identity or not self.page.service.snapshot.connected:
            publish_page_message(self.page,"连接已变化，请重新预览。")
            return
        self._preview_preserve = False
        self._waiting = True
        if not self.service.relocation_preview(preserve_access_points=False):
            self._waiting = False
            publish_page_message(self.page,"清理预览未开始，请等待当前任务结束后重试。")
        self._sync()

    @staticmethod
    def summary(review):
        lines = ["将删除 %d 个连接上级的 Wi-Fi 配置（包括旧名称和密码）：" % len(review["rows"])]
        if not review["preserve_access_points"]:
            lines = ["将删除 %d 个无线配置，包含自建热点（名称和密码均删除）：" % len(review["rows"])]
        lines.extend("  " + (row["ssid"] or row["section"]) +
                     ("（自建热点）" if row["mode"]=="ap" else "") for row in review["rows"])
        if review["plan"].get("access_points"):
            lines.append("\n保留以下自建热点，密码和开关状态不变：")
            lines.extend("  "+row["old"]+" → "+row["name"] for row in review["plan"]["access_points"])
            lines.append("每个热点的 MAC（无线地址）已自动随机生成。")
        lines.append("\n新主机名：" + review["plan"]["hostname"])
        for target, mac in zip(review["targets"], review["plan"]["macs"]):
            lines.append(target["device"] + "：" + target["old_mac"] + " → " + mac)
        if review["removed"]:
            lines.append("\n删除旧上级和中继接口（包括残留）：" + "、".join(review["removed"]))
        if review["skipped"]:
            lines.append("\n以下保留接口不更换 MAC：")
            for section in review["skipped"]:
                reason=MAC_SKIP_REASONS.get(review.get("skip_reasons",{}).get(section), "设备状态暂无法确认")
                lines.append("  "+section+"："+reason)
        if review.get("related"):
            lines.append("\n同步清除与旧接口配套的地址分配和路由记录："+"、".join(item["section"] for item in review["related"]))
        lines.append("\n更新外网自动取地址时上报的主机名，清除原有自定义客户端标识。保留管理 IP、登录密码、系统和已安装组件。")
        if review["preserve_access_points"]:
            lines.append("\n请停止在网页或脚本中改配置。旧上级 Wi-Fi 会断开；自建热点改名时也会短暂断开。如通过 Wi-Fi 管理，请记住上面的新名称，并使用原密码及时连接新热点。若无法确认成功，90 秒后将自动回滚，届时请重连旧热点。")
        else:
            lines.append("\n自建热点将被删除，不会更名或重新创建。如果正通过这些 Wi-Fi 管理路由器，连接将断开；请先用网线连接再确认清理。无法确认成功时，90 秒后将自动回滚。")
        lines.append("\n本功能只清理当前网络配置，用户自建文件、备份和其他软件的数据需另行处理。")
        lines.append("\n" + DISCONNECT_WARNING)
        return "\n".join(lines)

    def _completed(self, result):
        if result.action != "move_preview" or not self._waiting:
            return
        self._waiting = False
        if result.error:
            publish_page_message(self.page,result.error)
            dialog=MessageDialog("无法打开搬家清理",result.error,self.page)
            try:dialog.exec()
            finally:
                dialog.deleteLater()
                self._sync()
            return
        review = result.value
        if review.get("preserve_access_points") is not self._preview_preserve:
            publish_page_message(self.page,"清理选择已变化，请重新预览。")
            self._sync()
            return
        if not review["targets"]:
            dialog=MessageDialog("搬家清理", "未找到可安全更换 MAC 的网络接口，未执行清理。", self.page)
            try:dialog.exec()
            finally:dialog.deleteLater()
        else:
            review = deepcopy(review)
            if review["access_points"]:
                editor = relocation_identity_editor(review, self.page)
                try:
                    choice = editor.exec()
                    if choice == editor.Discarded:
                        self._discard_preview()
                        return
                    if choice != editor.Accepted:
                        return
                    review["plan"]["access_points"] = editor.values()["identities"]
                    # Keep the original names for the final review, never user input.
                    originals = {row["section"]:row["ssid"] for row in review["access_points"]}
                    for row in review["plan"]["access_points"]:
                        row["old"] = originals[row["section"]]
                finally:
                    editor.deleteLater()
                    self._sync()
            if self.service._connection_identity() is not self._identity or not self.page.service.snapshot.connected:
                publish_page_message(self.page,"连接已变化，请重新预览。")
                self._sync()
                return
            dialog = ConfirmDialog("确认搬家清理", self.summary(review), self.page,
                                   yes_text="清理并更换身份", no_text="取消")
            try:
                dialog.yes_button.setProperty("riskAction", True)
                if dialog.exec() == ConfirmDialog.Accepted:
                    if self.service._connection_identity() is not self._identity or not self.page.service.snapshot.connected:
                        publish_page_message(self.page,"连接已变化，请重新预览。")
                    elif not self.service.relocate(review):
                        publish_page_message(self.page,"清理未开始，请等待任务结束后重新预览。")
            finally:dialog.deleteLater()
        self._sync()

    def dispose(self):
        self._waiting = False
        self.page.service.snapshotChanged.disconnect(self._sync)
        if self.service is not None:
            self.service.snapshotChanged.disconnect(self._sync)
            self.service.completed.disconnect(self._completed)
