"""Fresh radio preflight and explicit takeover consent before a WPS task."""
from PySide6.QtCore import QObject
from modules.common_widgets.common_dialogs import ConfirmDialog
from modules.config_services.capture_settings_manager import CaptureSettingsManager
from modules.user_errors import RESULT_DIR_INVALID
from .openwrt.confirmation_helpers import confirm_radio_takeover


class WpsStartController(QObject):
    def __init__(self, page):
        super().__init__(page)
        self.page = page
        self._pending = None
        page.capture_service.takeoverProbeFinished.connect(self._ready)

    def start(self, settings, selected, mode, entry):
        if self._pending is not None:
            return
        options = dict(settings)
        options.pop("allow_radio_takeover", None)
        service = self.page.capture_service
        fingerprint = service.openwrt_backend.ssh_client.host_fingerprint
        probe_id = service.probe_openwrt_takeover_async(options, selected)
        if not probe_id:
            self._message("\u5f53\u524d\u6709\u4efb\u52a1\u8fd0\u884c\u6216\u8fde\u63a5\u4e0d\u53ef\u7528\uff0cPIN \u672a\u542f\u52a8\u3002")
            return
        self._pending = (probe_id, options, tuple(selected), mode, entry, fingerprint)

    def _message(self, text):
        self.page.info_panel.append_info(text, "warning")

    def _current(self, selected, fingerprint):
        service = self.page.capture_service
        snapshot = service.openwrt_snapshot
        return (snapshot.connection_state.value == "connected"
                and service.openwrt_backend.ssh_client.host_fingerprint == fingerprint
                and {p.bssid for p in snapshot.access_points if p.selected} == set(selected))

    def _ready(self, probe_id, entries, error, cancelled):
        pending = self._pending
        if pending is None or pending[0] != probe_id:
            return
        self._pending = None
        _, settings, selected, mode, entry, fingerprint = pending
        if cancelled:
            self._message("\u5df2\u53d6\u6d88\uff0cPIN \u672a\u542f\u52a8\u3002")
            return
        if error:
            self._message("\u7f51\u5361\u68c0\u6d4b\u5931\u8d25\uff0cPIN \u672a\u542f\u52a8\uff1a" + str(error))
            return
        if not self._current(selected, fingerprint):
            self._message("\u8fde\u63a5\u6216\u6240\u9009\u76ee\u6807\u5df2\u53d8\u5316\uff0c\u8bf7\u91cd\u65b0\u5f00\u59cb\u3002")
            return
        by_target = {str(row.bssid).upper(): row for row in entries or ()}
        rows = [by_target.get(str(bssid).upper()) for bssid in selected]
        if any(row is None or row.error for row in rows):
            self._message("\u672a\u80fd\u786e\u8ba4\u6240\u9009\u76ee\u6807\u7684\u53ef\u7528\u7f51\u5361\uff0cPIN \u672a\u542f\u52a8\u3002")
            return
        takeovers = [row for row in rows if row.need_takeover]
        if takeovers:
            if settings.get("idle_radio_only", False):
                self._message("\u5f53\u524d\u4ec5\u5141\u8bb8\u7a7a\u95f2\u7f51\u5361\uff0cPIN \u672a\u542f\u52a8\u3002")
                return
            if not settings.get("skip_radio_takeover_confirmation", False):
                snapshot = self.page.capture_service.openwrt_snapshot
                bands = "/".join(sorted({p.band for p in snapshot.access_points if p.bssid in selected and p.band}))
                channels = "/".join(sorted({str(row.phy_channel) for row in takeovers if row.phy_channel}))
                accepted, remember = confirm_radio_takeover(
                    self.page, bands, "PIN \u4efb\u52a1", channels or "\u672a\u77e5")
                if not accepted:
                    return
                if remember:
                    self.page.settings["openwrt"]["skip_radio_takeover_confirmation"] = True
                    if not self.page._save_settings_with_feedback():
                        return
            settings["allow_radio_takeover"] = True
        self._launch(settings, selected, mode, entry, fingerprint)

    def _launch(self, settings, selected, mode, entry, fingerprint):
        page = self.page
        snapshot = page.capture_service.openwrt_snapshot
        names = [
            point.ssid or point.bssid
            for point in snapshot.access_points
            if point.bssid in selected
        ][:3]
        scope_text = "、".join(names)
        if len(selected) > 3:
            scope_text += f"等 {len(selected)} 个目标"
        mode_text = {"normal": "常规 Pin", "second": "秒 Pin", "offline": "离线 Pin"}.get(mode, "组合 Pin")
        from modules.capture_services.openwrt.wps_defaults import wps_mode_description
        timing = wps_mode_description(mode)
        if ConfirmDialog(
            f"开始{mode_text}（高风险）",
            f"将对{scope_text}逐个跑 {mode_text}。\n\n"
            f"{timing}期间占用无线网卡，多目标会依次处理；部分设备会锁定 WPS，使后续 Pin 尝试暂时无法进行。"
            "只能用于自有或已获明确授权的网络。\n\n"
            "请确认你拥有相关设备和网络的管理或测试授权。",
            page,
            yes_text="确认并开始",
            no_text="取消",
        ).exec() != ConfirmDialog.Accepted:
            return
        if not self._current(selected, fingerprint):
            self._message("\u8fde\u63a5\u6216\u6240\u9009\u76ee\u6807\u5df2\u53d8\u5316\uff0cPIN \u672a\u542f\u52a8\u3002")
            return
        try:
            settings["resolved_output_dir"] = str(
                CaptureSettingsManager.normalized_output_dir(settings.get("output_dir"))
            )
        except Exception as exc:
            page.info_panel.append_info(RESULT_DIR_INVALID, "error")
            return
        if not page.capture_service.capture_openwrt_wps_async(settings, selected, mode, entry=entry):
            page.info_panel.append_info("当前任务条件不满足或已有任务运行，不能开始 WPS。", "warning")
