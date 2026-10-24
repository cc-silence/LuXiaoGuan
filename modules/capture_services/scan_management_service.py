"""Read-only scan preflight using the shared capture task lifecycle."""
from dataclasses import dataclass
from uuid import uuid4
from PySide6.QtCore import QObject, Signal
from .models import CaptureSessionState
from .openwrt.models import CaptureTaskType, OpenWrtConnectionState
from .openwrt.management_link import ManagementLink, read_management_link


@dataclass(frozen=True)
class ScanManagementPlan:
    phy: str
    link: ManagementLink
    fingerprint: str
    business_modes: tuple[str, ...] = ()

    @property
    def at_risk(self):
        return self.management_at_risk

    @property
    def management_at_risk(self):
        return self.link.status == "wireless" and self.link.phy == self.phy


class ScanManagementService(QObject):
    finished = Signal(str, object, str, bool)

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self._probe_id = ""

    def start(self, settings):
        owner = self.owner
        if owner._shutting_down or owner.task_controller.busy:
            return ""
        if owner.openwrt_backend.snapshot.connection_state != OpenWrtConnectionState.CONNECTED:
            return ""
        probe_id = uuid4().hex
        event = owner.task_controller.begin(CaptureTaskType.WIFI_SCAN.value, probe_id, CaptureSessionState.CHECKING)
        if event is None:
            return ""
        self._probe_id = probe_id
        options = dict(settings)
        owner._start_openwrt_worker(
            probe_id, lambda cancel: self._probe(options, cancel), event,
            lambda plan: self._complete(probe_id, plan, "", event.is_set()),
            lambda summary, detail: self._complete(probe_id, None, summary, event.is_set()))
        return probe_id

    def _probe(self, options, event):
        backend = self.owner.openwrt_backend
        fingerprint = backend.ssh_client.host_fingerprint
        if not fingerprint:
            raise RuntimeError("\u767b\u5f55\u8fde\u63a5\u5df2\u53d8\u5316\uff0c\u8bf7\u91cd\u65b0\u767b\u5f55\u540e\u518d\u626b\u63cf\u3002")
        band = "5G" if options.get("band") == "5G" else "2.4G"
        phy = backend._find_scan_radio(band, event, idle_radio_only=bool(options.get("idle_radio_only", False)))
        link = read_management_link(backend.command_runner, event)
        if event.is_set():
            raise InterruptedError("\u626b\u63cf\u9884\u68c0\u5df2\u53d6\u6d88\u3002")
        return ScanManagementPlan(phy, link, fingerprint)

    def _complete(self, probe_id, plan, error, cancelled):
        if probe_id != self._probe_id:
            return
        self._probe_id = ""
        self.owner.task_controller.finish()
        self.finished.emit(probe_id, plan, str(error or ""), cancelled)
