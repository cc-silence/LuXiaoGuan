"""Show current capture targets in the existing intelligent-analysis dialog."""
from dataclasses import replace
from PySide6.QtCore import QObject, Qt
from modules.display_privacy import controller_for, MASKED_TEXT
from modules.capture_services.openwrt.auto_capture_log_analyzer import (
    AutoCaptureDiagnosticReport, CapturedTargetSummary,
)


class LiveCaptureDetailsPresenter(QObject):
    def __init__(self, card, output_dir=""):
        super().__init__(card)
        self.card = card
        self.output_dir = output_dir
        self.dialog = None
        self.stats = {}
        self._details = None
        self._targets = ()
        self._privacy = controller_for(card)
        if self._privacy is not None:
            self._privacy.changed.connect(self._refresh_privacy)

    def update(self, stats):
        self.stats = dict(stats)
        if self.dialog is not None:
            self.dialog.update_live_report(self._report())

    def _refresh_privacy(self):
        if self.dialog is not None:
            self.dialog.update_live_report(self._report())

    def update_state(self, state):
        if state == "starting" and self.stats.get("state") != "starting":
            self.update(dict(state=state, output_dir=self.stats.get("output_dir", self.output_dir),
                             capture_details={}, pmkid_count=0, eapol_count=0))
        else:
            self.update(dict(self.stats, state=state))

    def _report(self):
        details = self.stats.get("capture_details", {})
        if details is not self._details:
            self._details = details
            self._targets = tuple(CapturedTargetSummary(**row) for row in details.get("targets", ()))
        state = self.stats.get("state", "idle")
        names = {"starting": "正在启动", "capturing": "正在抓包",
                 "stopping": "正在停止", "error": "抓包异常", "idle": "待机或已停止"}
        title = self.card.radio_name + " · " + names.get(state, state)
        note = ("明细按热点地址合并，同名 Wi-Fi 会分别显示。\n"
                "未识别的名称先显示热点地址，后续自动补全。\n"
                "此处为实时日志记录；握手可用性以抓包文件校验为准。")
        if not self._targets:
            note += "\n\n尚未收到可归属热点的捕获记录，抓包过程中会自动更新。"
        if details.get("truncated"):
            note += "\n\n明细已达到容量上限，部分热点未列出。"
        targets = self._targets
        if self._privacy is not None and self._privacy.enabled:
            targets = tuple(replace(row, bssid=MASKED_TEXT, ssid=MASKED_TEXT) for row in targets)
        return AutoCaptureDiagnosticReport(health_title=title, health_level="info", targets=targets,
            pmkid_count=self.stats.get("pmkid_count", 0), eapol_count=self.stats.get("eapol_count", 0),
            raw_summary=note)

    def show(self):
        if self.dialog is not None:
            self.dialog.raise_()
            self.dialog.activateWindow()
            return
        from .auto_capture_report_dialog import AutoCaptureReportDialog
        dialog = AutoCaptureReportDialog(self._report(), self.stats.get("output_dir") or self.output_dir,
                                         self.card, live=True)
        dialog.setAttribute(Qt.WA_DeleteOnClose, True)
        dialog.finished.connect(self._closed)
        self.dialog = dialog
        dialog.show()

    def _closed(self, *_):
        self.dialog = None
