"""Relocation preview and explicit confirmation, sharing the standard dialog and task service."""
from PySide6.QtCore import QObject
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


class RelocationActions(QObject):
    def __init__(self, page, container):
        super().__init__(page)
        self.page = page
        self.service = getattr(page.service, "setup", None)
        self._waiting = False
        self._identity = None
        self.button = create_page_text_button(container, "搬家清理",
            "清理旧 Wi-Fi 和桥接，更换主机名与网络 MAC。先预览再确认；保留管理地址、登录密码和已装组件。")
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

    def review(self):
        if not self.button.isEnabled():
            return
        self._identity = self.service._connection_identity()
        self._waiting = True
        if not self.service.relocation_preview():
            self._waiting = False
            self.page.status_label.setText("请等待当前任务结束后重试。")
        self._sync()

    @staticmethod
    def summary(review):
        lines = ["将删除 %d 个无线配置（包括名称和密码）：" % len(review["rows"])]
        lines.extend("  " + (row["ssid"] or row["section"]) for row in review["rows"])
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
        lines.append("\n请先用网线连接路由器，并停止在网页或脚本中改配置。旧 Wi-Fi 会全部断开，上网连接可能重新取地址。若无法确认成功，设备会在 90 秒内自动回滚，请等待后重连。")
        lines.append("\n本功能只清理当前网络配置，用户自建文件、备份和其他软件的数据需另行处理。")
        lines.append("\n" + DISCONNECT_WARNING)
        return "\n".join(lines)

    def _completed(self, result):
        if result.action != "move_preview" or not self._waiting:
            return
        self._waiting = False
        if result.error:
            self.page.status_label.setText(result.error)
            dialog=MessageDialog("无法打开搬家清理",result.error,self.page)
            try:dialog.exec()
            finally:
                dialog.deleteLater()
                self._sync()
            return
        review = result.value
        if not review["targets"]:
            MessageDialog("搬家清理", "未找到可安全更换 MAC 的网络接口，未执行清理。", self.page).exec()
        else:
            dialog = ConfirmDialog("确认搬家清理", self.summary(review), self.page,
                                   yes_text="清理并更换身份", no_text="取消")
            dialog.yes_button.setProperty("riskAction", True)
            if dialog.exec() == ConfirmDialog.Accepted:
                if self.service._connection_identity() is not self._identity or not self.page.service.snapshot.connected:
                    self.page.status_label.setText("连接已变化，请重新预览。")
                elif not self.service.relocate(review):
                    self.page.status_label.setText("清理未开始，请等待任务结束后重新预览。")
        self._sync()

    def dispose(self):
        self._waiting = False
        self.page.service.snapshotChanged.disconnect(self._sync)
        if self.service is not None:
            self.service.snapshotChanged.disconnect(self._sync)
            self.service.completed.disconnect(self._completed)
