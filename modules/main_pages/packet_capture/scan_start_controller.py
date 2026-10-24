"""Main-thread scan choices after router-side, cancellable link verification."""
from PySide6.QtCore import QObject
from modules.common_widgets.common_dialogs import ConfirmDialog, MessageDialog
from modules.config_services.capture_settings_manager import CaptureSettingsManager


class ScanStartController(QObject):
    def __init__(self, page):
        super().__init__(page)
        self.page = page
        self.service = page.capture_service.scan_management
        self._pending = None
        self.service.finished.connect(self._ready)

    def start(self, settings):
        probe_id = self.service.start(settings)
        if not probe_id:
            self.page.info_panel.append_info("\u5f53\u524d\u672a\u8fde\u63a5\u8def\u7531\u5668\u6216\u5df2\u6709\u4efb\u52a1\u8fd0\u884c\uff0c\u4e0d\u80fd\u5f00\u59cb\u626b\u63cf\u3002", "warning")
            return
        self._pending = (probe_id, dict(settings))
        self.page.info_panel.append_info("\u6b63\u5728\u8def\u7531\u5668\u4e0a\u6838\u5b9e\u5f53\u524d\u767b\u5f55\u8fde\u63a5\u7684\u5b9e\u9645\u63a5\u53e3\uff0c\u8bf7\u7a0d\u5019\u3002", "info")

    def _cancelled(self):
        self.page.info_panel.append_info("\u5df2\u53d6\u6d88\u672c\u6b21 Wi-Fi \u626b\u63cf\u3002", "info")

    def _ready(self, probe_id, plan, error, cancelled):
        if self._pending is None or self._pending[0] != probe_id:
            return
        _probe_id, settings = self._pending
        self._pending = None
        if cancelled:
            self._cancelled()
            return
        page = self.page
        if error or plan is None or plan.link.status == "unknown":
            message = error or "\u65e0\u6cd5\u6838\u5b9e\u8def\u7531\u5668\u5f53\u524d\u767b\u5f55\u8fde\u63a5\u7684\u5b9e\u9645\u63a5\u53e3\uff0c\u672c\u6b21\u626b\u63cf\u672a\u5f00\u59cb\u3002\u8bf7\u5bfc\u51fa\u65e5\u5fd7\u6838\u67e5\u3002"
            page.info_panel.append_info(message, "warning")
            MessageDialog("\u626b\u63cf\u672a\u5f00\u59cb", message, page).exec()
            return
        if plan.fingerprint != page.capture_service.openwrt_backend.ssh_client.host_fingerprint:
            page.info_panel.append_info("\u767b\u5f55\u8fde\u63a5\u5df2\u53d8\u5316\uff0c\u8bf7\u91cd\u65b0\u5f00\u59cb\u626b\u63cf\u3002", "warning")
            return
        if plan.at_risk:
            band = "5G" if settings.get("band") == "5G" else "2.4G"
            other = "2.4G" if band == "5G" else "5G"
            message = ("当前登录连接经过 " + plan.link.interface + "（" + plan.link.phy +
                "），扫描会使用同一张无线网卡，可能导致登录断开。\n\n"
                "为保护管理连接，本次不使用这张网卡扫描。可切换频段重新核实，或改用网线登录。")
            dialog = ConfirmDialog(
                "扫描连接保护", message,
                page, yes_text="切换到" + other + "并重新核实",
                no_text="取消扫描")
            result = dialog.exec()
            if result == ConfirmDialog.Accepted:
                settings["band"] = other
                page.openwrt_tab.capture_page.tools.band_combo.setCurrentText(other)
                page._save_openwrt_settings()
                self.start(settings)
                return
            self._cancelled()
            return
        settings["management_scan_phy"] = plan.phy
        settings["management_scan_fingerprint"] = plan.fingerprint
        self._launch(settings)

    def _launch(self, settings):
        page = self.page
        try:
            settings["resolved_output_dir"] = str(CaptureSettingsManager.normalized_output_dir(settings.get("output_dir")))
        except Exception as exc:
            page.info_panel.append_info("\u6293\u5305\u7ed3\u679c\u76ee\u5f55\u65e0\u6548\uff1a" + str(exc), "error")
            return
        if not page.capture_service.scan_openwrt_wifi_async(settings):
            page.info_panel.append_info("\u5f53\u524d\u672a\u8fde\u63a5\u8def\u7531\u5668\u6216\u5df2\u6709\u4efb\u52a1\u8fd0\u884c\uff0c\u4e0d\u80fd\u5f00\u59cb\u626b\u63cf\u3002", "warning")
