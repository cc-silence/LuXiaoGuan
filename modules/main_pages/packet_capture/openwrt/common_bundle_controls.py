"""Shared download confirmation and progress for both dependency groups."""
from PySide6.QtCore import QObject, QUrl
from PySide6.QtGui import QDesktopServices
from pathlib import Path
from modules.common_widgets.common_dialogs import MessageDialog
from PySide6.QtWidgets import QHBoxLayout, QLabel
from modules.main_pages.page_action_buttons import create_page_text_button
from modules.common_widgets.common_dialogs import ConfirmDialog
from modules.common_widgets.task_progress_dialog import TaskProgressDialog
from modules.main_pages.packet_capture.ui_helpers import create_capture_card
from modules.ui_scale_manager import UiScaleManager

OFFLINE_HINT = "\u79bb\u7ebf\u5305\u5305\u542b\u5df2\u652f\u6301\u7684\u5e38\u7528\u8def\u7531\u5668\u4f9d\u8d56\uff0c\u6587\u4ef6\u8f83\u5927\uff1b\u82e5\u8bbe\u5907\u80fd\u8054\u7f51\uff0c\u5efa\u8bae\u5728\u7ebf\u5b89\u88c5\u3002"

def size_text(size):
    """\u6309\u5b9e\u9645\u5b57\u8282\u5c55\u793a\u5341\u8fdb\u5236 MB\uff0c\u4e0e\u5df2\u53d1\u5e03\u79bb\u7ebf\u5305\u5927\u5c0f\u4e00\u81f4\u3002"""
    return f"{size / 1_000_000:.2f} MB"

class CommonBundleControls(QObject):
    """\u9875\u9762\u53ea\u7ba1\u63d0\u793a\u4e0e\u786e\u8ba4\uff0c\u6e05\u5355\u8bfb\u53d6\u548c\u4e0b\u8f7d\u5747\u7531\u5e38\u9a7b\u670d\u52a1\u540e\u53f0\u6267\u884c\u3002"""
    def __init__(self, card, layout, owner, kind="common"):
        super().__init__(card)
        from modules.capture_services.openwrt.bundle_specs import bundle_spec
        self.spec = bundle_spec(kind)
        self.idle_text = "\u4e0b\u8f7d" + self.spec.title + "\u5305"
        self.card, content_layout = create_capture_card(
            card, self.spec.title + "\u79bb\u7ebf\u5305")
        layout.addWidget(self.card)
        layout.addSpacing(UiScaleManager.for_widget(card).px(6))
        card, layout = self.card, content_layout
        self.owner = owner
        self.service = None
        self.dialog = None
        self.confirmation = None
        self._disposed = False
        self._download_speed = 0.0
        row = QHBoxLayout()
        self.button = create_page_text_button(card, self.idle_text, "\u79bb\u7ebf\u5b89\u88c5\u8d44\u6e90")
        self.button.setObjectName("captureSecondaryButton")
        detail = "\u542b wpa-cli\u3001rpcd \u53ca\u4fe1\u53f7\u7ba1\u7406\u4f9d\u8d56\uff0c\u4e0d\u4f1a\u5220\u9664\u8f6f\u4ef6\u539f\u6709\u4f9d\u8d56\u3002" if kind == "common" else "\u542b aircrack-ng\u3001hcxdumptool\u3001reaver \u53ca\u5176\u4f9d\u8d56\u3002"
        self.button.setToolTip("\u5148\u663e\u793a\u79bb\u7ebf\u5305\u5927\u5c0f\uff0c\u786e\u8ba4\u540e\u4e0b\u8f7d\u3002\u65e0\u9700\u767b\u5f55\u8def\u7531\u5668\uff0c\u9700\u8981\u7535\u8111\u8054\u7f51\u3002" + detail)
        self.label = QLabel(OFFLINE_HINT, card)
        self.label.setObjectName("captureDescription")
        self.label.setWordWrap(True)
        row.addWidget(self.button)
        self.open_button = create_page_text_button(card, "\u6253\u5f00\u4f9d\u8d56\u5305\u6240\u5728\u4f4d\u7f6e", "\u6253\u5f00\u672c\u5730\u79bb\u7ebf\u5305\u76ee\u5f55")
        self.open_button.setObjectName("captureSecondaryButton")
        self.open_button.hide()
        self.open_button.clicked.connect(self._open_location)
        row.addWidget(self.open_button)
        row.addWidget(self.label, 1)
        layout.addLayout(row)
        self.status_label = QLabel(card)
        self.status_label.setObjectName("captureStatusValue")
        self.status_label.setWordWrap(True)
        self.status_label.hide()
        layout.addWidget(self.status_label)
        self.button.clicked.connect(self._clicked)
        owner.activityChanged.connect(self._sync)
        existing = getattr(owner, self.spec.service_attr, None)
        if existing is None:
            from modules.capture_services.common_bundle_service import CommonBundleDownloadService
            existing = CommonBundleDownloadService(owner, self.spec.kind)
            setattr(owner, self.spec.service_attr, existing)
            self._attach(existing)
            existing.refresh_local()
        else:
            self._attach(existing)
        self._sync()

    def _open_location(self):
        status = self.service.local_status if self.service is not None else {}
        path = Path(status.get("path", ""))
        if not status.get("installed") or not status.get("path") or not path.is_dir():
            self.open_button.hide()
            if self.service is not None:
                self.service.refresh_local()
            MessageDialog("\u76ee\u5f55\u4e0d\u53ef\u7528", "\u672c\u5730\u79bb\u7ebf\u5305\u5df2\u79fb\u52a8\u6216\u5220\u9664\uff0c\u8bf7\u91cd\u65b0\u68c0\u67e5\u3002", self.card).exec()
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve()))):
            MessageDialog("\u6253\u5f00\u5931\u8d25", "\u65e0\u6cd5\u6253\u5f00\u4f9d\u8d56\u5305\u76ee\u5f55\u3002", self.card).exec()

    def _attach(self, service):
        self.service = service
        service.changed.connect(self._sync)
        service.progress.connect(self._progress)
        service.finished.connect(self._finished)
        service.offerReady.connect(self._offer_ready)

    def _clicked(self):
        if self.service is None:
            from modules.capture_services.common_bundle_service import CommonBundleDownloadService
            service = getattr(self.owner, self.spec.service_attr, None)
            if service is None:
                service = CommonBundleDownloadService(self.owner, self.spec.kind)
                setattr(self.owner, self.spec.service_attr, service)
            self._attach(service)
        if self.service.phase == "downloading":
            self._show_dialog()
        elif self.service.phase == "idle":
            self.service.inspect()
            self._sync()

    def _offer_ready(self, payload):
        """\u4e3b\u7ebf\u7a0b\u7528\u975e\u963b\u585e\u5f39\u7a97\u786e\u8ba4\uff0c\u4e0d\u5360\u4f4f\u5df2\u5b8c\u6210\u7ebf\u7a0b\u7684\u56de\u6536\u3002"""
        if self._disposed or self.confirmation is not None:
            return
        from modules.capture_services.openwrt.bundle_status import revision_label
        series = sorted({".".join(str(v).split(".")[:2]) for v in payload.get("releases", [])})
        message = (self.spec.title + "\u79bb\u7ebf\u5305"
                   + "\n\u4e0b\u8f7d\u5927\u5c0f\uff1a" + size_text(payload["size"])
                   + "\n\u89e3\u538b\u540e\u7ea6\uff1a" + size_text(payload["unpacked_size"]))
        message += "\n线上资源版本：" + revision_label(payload)
        if self.service.local_status.get("installed"):
            message += "\n当前本地版本：" + self.service.local_status.get("label", "")
        if series:
            message += "\n\u8986\u76d6\u7cfb\u7edf\uff1aOpenWrt " + " / ".join(series)
        message += "\n\n" + OFFLINE_HINT + "\n\u4e0b\u8f7d\u540e\u4fdd\u5b58\u5728\u8f6f\u4ef6\u76ee\u5f55\uff0c\u53ef\u968f\u8f6f\u4ef6\u4e00\u8d77\u62f7\u8d1d\u79bb\u7ebf\u4f7f\u7528\u3002\n\n\u786e\u5b9a\u4e0b\u8f7d\u5417\uff1f"
        self.confirmation = ConfirmDialog(self.idle_text, message, self.card)
        self.confirmation.finished.connect(self._confirmation_finished)
        self.confirmation.open()

    def _confirmation_finished(self, result):
        dialog, self.confirmation = self.confirmation, None
        if dialog is not None:
            dialog.deleteLater()
        blocked = False
        blocked_reason = ""
        if result == ConfirmDialog.Accepted and not self._disposed:
            if not self.service.start():
                blocked_reason = self.service.message
                self.service.decline()
                blocked = True
        else:
            self.service.decline()
        self._sync()
        if blocked:
            self.status_label.setText(blocked_reason or "\u5f53\u524d\u6709\u5176\u4ed6\u4efb\u52a1\uff0c\u8bf7\u7a7a\u95f2\u540e\u91cd\u8bd5\u4e0b\u8f7d\u3002")
            self.status_label.show()

    def _show_dialog(self):
        if (self.dialog is None and self.card.isVisible() and self.service is not None
                and self.service.phase == "downloading"):
            self.dialog = TaskProgressDialog(
                self.idle_text, self.spec.title + "\u5305\uff1a" + size_text(self.service.download_size),
                parent=self.card, progress_formatter=self._progress_text)
            self.dialog.cancelRequested.connect(self.service.cancel)

    def _progress_text(self, done, total):
        """\u540c\u65f6\u663e\u793a\u5bb9\u91cf\u3001\u767e\u5206\u6bd4\u4e0e\u540e\u53f0\u8fd4\u56de\u7684\u5b9e\u9645\u4e0b\u8f7d\u901f\u5ea6\u3002"""
        speed = self._download_speed
        rate = f"{speed / 1_000_000:.2f} MB/s" if speed >= 1_000_000 else f"{speed / 1000:.1f} KB/s"
        return (f"{size_text(done)} / {size_text(total)} ({min(100,round(done*100/total)) if total else 0}%)"
                + "\n\u4e0b\u8f7d\u901f\u5ea6\uff1a" + rate)

    def _sync(self):
        if self._disposed:
            return
        phase = self.service.phase if self.service is not None else "idle"
        downloading = phase == "downloading"
        self.button.setEnabled(downloading or (not self.owner.is_busy and phase == "idle"))
        installed = self.service is not None and self.service.local_status.get("installed")
        self.open_button.setVisible(bool(installed and self.service.local_status.get("path")))
        idle_text = "检测" + self.spec.title + "包更新" if installed else self.idle_text
        self.button.setText("检测更新中…" if phase == "checking" else
                            "检查本地版本…" if phase == "local_check" else
                            "\u67e5\u770b\u4e0b\u8f7d\u8fdb\u5ea6" if downloading else idle_text)
        message = self.service.message if self.service is not None else ""
        self.status_label.setText(message)
        self.status_label.setVisible(bool(message))
        if downloading:
            self._show_dialog()
            if self.dialog is not None:
                self.dialog.update_stage(message)

    def _progress(self, value):
        if self._disposed:
            return
        self._download_speed = max(0.0, float(value.get("speed", 0)))
        self._show_dialog()
        if self.dialog is not None:
            self.dialog.update_progress(value)

    def _finished(self, success, message):
        if self.dialog is not None:
            dialog, self.dialog = self.dialog, None
            dialog.finish(success, message)

    def set_light_theme(self, enabled):
        self.button.set_light_theme(bool(enabled))
        self.open_button.set_light_theme(bool(enabled))

    def dispose(self):
        """\u91ca\u653e\u65f6\u53d6\u6d88\u5c1a\u672a\u786e\u8ba4\u7684\u8bf7\u6c42\uff0c\u5df2\u786e\u8ba4\u4e0b\u8f7d\u4ecd\u7531\u5e38\u9a7b\u670d\u52a1\u7ba1\u7406\u3002"""
        if self._disposed:
            return
        self._disposed = True
        self.owner.activityChanged.disconnect(self._sync)
        if self.service is not None:
            self.service.changed.disconnect(self._sync)
            self.service.progress.disconnect(self._progress)
            self.service.finished.disconnect(self._finished)
            self.service.offerReady.disconnect(self._offer_ready)
            if self.service.phase == "checking":
                self.service.cancel()
            self.service.decline()
        if self.confirmation is not None:
            dialog, self.confirmation = self.confirmation, None
            dialog.finished.disconnect(self._confirmation_finished)
            dialog.reject()
            dialog.deleteLater()
        if self.dialog is not None:
            self.dialog.cancelRequested.disconnect()
            self.dialog.finish(True)
            self.dialog = None
