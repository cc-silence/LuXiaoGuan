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
            notes = []
            if plan.management_at_risk:
                notes.append("路由器已核实：当前 SSH 登录连接经过 " + plan.link.interface + "（" + plan.link.phy +
                    "），扫描使用同一张网卡，可能影响登录连接。")
            if plan.business_modes:
                labels = {"managed":"连接上级网络（中继）", "ap":"发射 Wi-Fi", "mesh":"网状网络"}
                modes = "、".join(labels.get(mode, mode) for mode in plan.business_modes)
                notes.append("路由器已核实：" + plan.phy + "上有已启用的" + modes +
                    "接口。扫描跳频可能影响中继上网或当前 Wi-Fi，即使登录走的是网线也会受影响。"
                    "后续抓包若需接管这张网卡，将另行确认临时停用信号。")
            notes.append("可切换到其他频段重新核实，或明确选择保持当前频段扫描。点右上角 X 或按 Esc 取消本次扫描。")
            dialog = ConfirmDialog(
                "同网卡扫描提醒", "\n\n".join(notes),
                page, yes_text="\u5207\u6362\u5230" + other + "\u5e76\u91cd\u65b0\u6838\u5b9e",
                no_text="\u4fdd\u6301" + band + "\u626b\u63cf")
            result = dialog.exec()
            if result == ConfirmDialog.Accepted:
                settings["band"] = other
                page.openwrt_tab.capture_page.tools.band_combo.setCurrentText(other)
                page._save_openwrt_settings()
                self.start(settings)
                return
            if not dialog.secondary_selected:
                self._cancelled()
                return
            if plan.management_at_risk:
                settings["management_scan_allowed_phy"] = plan.phy
            settings["business_scan_allowed_modes"] = list(plan.business_modes)
        settings["management_scan_phy"] = plan.phy
        settings["management_scan_fingerprint"] = plan.fingerprint
        self._launch(settings)

    def _launch(self, settings):
        page = self.page
        if not bool(settings.get("skip_scan_confirmation", False)):
            dialog = ConfirmDialog(
                "\u5f00\u59cb Wi-Fi \u626b\u63cf",
                "\u626b\u63cf\u4f1a\u4e34\u65f6\u521b\u5efa\u672c\u4efb\u52a1\u4e13\u5c5e monitor \u63a5\u53e3\uff0c\u4e0d\u4fee\u6539 UCI \u914d\u7f6e\u3001\u4e0d\u91cd\u8f7d Wi-Fi\u3002\u4efb\u52a1\u7ed3\u675f\u3001\u5931\u8d25\u6216\u53d6\u6d88\u65f6\u4f1a\u6e05\u7406\u4e34\u65f6\u63a5\u53e3\u3002\n\n\u8bf7\u786e\u8ba4\u4f60\u62e5\u6709\u6b64\u8def\u7531\u5668\u548c\u76f8\u5173\u7f51\u7edc\u7684\u7ba1\u7406\u6216\u6d4b\u8bd5\u6388\u6743\u3002",
                page, remember_option_text="\u4e0b\u6b21\u4e0d\u518d\u63d0\u793a")
            if dialog.exec() != ConfirmDialog.Accepted:
                self._cancelled()
                return
            if dialog.remember_checked:
                settings["skip_scan_confirmation"] = True
                page.settings["openwrt"]["skip_scan_confirmation"] = True
                if not page._save_settings_with_feedback():
                    return
        try:
            settings["resolved_output_dir"] = str(CaptureSettingsManager.normalized_output_dir(settings.get("output_dir")))
        except Exception as exc:
            page.info_panel.append_info("\u6293\u5305\u7ed3\u679c\u76ee\u5f55\u65e0\u6548\uff1a" + str(exc), "error")
            return
        if not page.capture_service.scan_openwrt_wifi_async(settings):
            page.info_panel.append_info("\u5f53\u524d\u672a\u8fde\u63a5\u8def\u7531\u5668\u6216\u5df2\u6709\u4efb\u52a1\u8fd0\u884c\uff0c\u4e0d\u80fd\u5f00\u59cb\u626b\u63cf\u3002", "warning")
