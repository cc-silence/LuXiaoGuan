"""Confirmed handling of pending global wireless/network changes."""
from PySide6.QtCore import QObject
from modules.common_widgets.common_dialogs import ConfirmDialog, MessageDialog
from modules.main_pages.page_action_buttons import create_page_text_button
from modules.ui_scale_manager import UiScaleManager


class PendingConfigActions(QObject):
    def __init__(self, page):
        super().__init__(page)
        self.page = page
        self.service = getattr(page.service, "setup", None)
        self._identity = None
        self._waiting = False
        self.button = create_page_text_button(page.inventory_toolbar, "查看待提交改动",
            "先预览无线和网络的暂存改动，确认后提交并刷新；不重启网络。")
        self.button.setProperty("signalPrimaryAction", True)
        UiScaleManager.for_widget(page).bind_metrics(self.button, min_height=38)
        layout = page.inventory_toolbar.layout()
        layout.insertWidget(max(0, layout.count()-1), self.button)
        self.button.clicked.connect(self.review)
        page.service.snapshotChanged.connect(self._sync)
        if self.service is not None:
            self.service.snapshotChanged.connect(self._sync)
            self.service.completed.connect(self._completed)
        self._sync()

    def _sync(self, *_):
        state = self.service.snapshot if self.service is not None else None
        if self._waiting and (not self.page.service.snapshot.connected or self.service._connection_identity() is not self._identity):
            self._waiting = False
        pending = getattr(self.page.service.snapshot, "pending_changes", False) or getattr(state, "pending_changes", False)
        self.button.setVisible(bool(pending and self.service is not None))
        self.button.setEnabled(bool(self.service is not None and self.page.service.snapshot.connected
                                   and not self.page.service.snapshot.busy and not self.service.active and not self._waiting))
        self.button.set_breathing(bool(pending and self.button.isEnabled()))

    def review(self):
        if self.service is None or self._waiting:
            return
        self._identity = self.service._connection_identity()
        self._waiting = True
        if not self.service.review_pending():
            self._waiting = False
            self.page.status_label.setText("预览未开始，请等待其他任务结束后重试。")
        self._sync()

    @staticmethod
    def summary(review):
        labels = {"wireless": "无线", "network": "网络"}
        lines = []
        for row in review["rows"]:
            value = "已隐藏敏感或未知内容" if row["value"] == "[hidden]" else row["value"] or "（空值或删除）"
            lines.append(labels[row["config"]] + " / " + row["section"] + " / " + row["option"] + "：" + value)
        return ("以下是当前全局暂存的改动，可能来自网页或脚本：\n\n" + "\n".join(lines) +
                "\n\n仅提交上面列出的无线、网络配置，提交前自动备份；失败时尝试恢复。本次只保存并刷新，不重新加载网络，新配置不一定已运行。不提交防火墙或 DHCP 配置。网页私有会话的改动仍需在原网页中处理。\n\n请暂停其他配置工具的编辑。提交开始后请等待核对结果。")

    def _completed(self, result):
        if result.action != "pending_review" or not self._waiting:
            return
        self._waiting = False
        if result.error:
            self._sync()
            return
        if not result.value["rows"]:
            MessageDialog("待提交改动", "没有可提交的全局无线或网络改动。如网页仍提示未提交，请在原网页中处理。", self.page).exec()
            self.page.service.refresh()
        else:
            dialog = ConfirmDialog("确认提交并刷新", self.summary(result.value), self.page,
                                   yes_text="提交并刷新", no_text="取消")
            if dialog.exec() == ConfirmDialog.Accepted:
                if self.service._connection_identity() is not self._identity or not self.page.service.snapshot.connected:
                    self.page.status_label.setText("连接已变化，请重新预览待提交改动。")
                elif not self.service.commit_pending(result.value["token"]):
                    self.page.status_label.setText("提交未开始，请等待任务结束后重新预览。")
        self._sync()

    def dispose(self):
        self.button.set_breathing(False)
        self._waiting = False
        self.page.service.snapshotChanged.disconnect(self._sync)
        if self.service is not None:
            self.service.snapshotChanged.disconnect(self._sync)
            self.service.completed.disconnect(self._completed)
