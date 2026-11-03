"""One cancellable fresh admission query before any foreground takeover prompt."""
from dataclasses import dataclass, replace
import threading

from PySide6.QtCore import QObject, Signal

from .capture_takeover import takeover_transaction
from .radio_usage import read_radio_usage
from .radio_usage_state import radio_usage_fields
from .radio_usage_evidence import record_usage_conflicts


@dataclass(frozen=True)
class CaptureStartInspection:
    configs: tuple
    usages: dict
    error: str
    connection: object
    fingerprint: str
    cancel: object


class CaptureStartPreflight(QObject):
    finished = Signal(object)

    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self._request = None
        self._thread = None

    def cancel(self):
        if self._request is not None:
            self._request.cancel.set()

    def is_current(self, result):
        ssh = self.service.ssh
        return (not getattr(self.service, "_disposed", False)
                and result is self._request and not result.cancel.is_set()
                and ssh.connected and getattr(ssh, "_client", None) is result.connection
                and getattr(ssh, "host_fingerprint", "") == result.fingerprint)

    def request(self, configs):
        if self._thread is not None and self._thread.is_alive():
            return False
        self.cancel()
        ssh = self.service.ssh
        result = CaptureStartInspection(tuple(dict(item) for item in configs), {}, "",
                                        getattr(ssh, "_client", None),
                                        getattr(ssh, "host_fingerprint", ""), threading.Event())
        self._request = result

        def inspect():
            try:
                if not self.is_current(result):
                    raise RuntimeError("Wireless connection changed before inspection.")
                with takeover_transaction(result.cancel):
                    usages = read_radio_usage(self.service.runner, result.cancel)
                    if any(usage.state == "blocked" for usage in usages.values()):
                        record_usage_conflicts(self.service.runner, usages, result.cancel)
                updated = []
                for item in result.configs:
                    radio = item["radio_info"]
                    usage = usages.get(radio.name)
                    if usage is None:
                        raise RuntimeError("Cannot identify requested radio in current wireless state.")
                    usage.require_available()
                    updated.append(dict(item, radio_info=replace(radio, **radio_usage_fields(usage))))
                completed = replace(result, configs=tuple(updated), usages=usages)
            except Exception as exc:
                completed = replace(result, error=str(exc))
            # Preserve the request identity across the result, including cancellation.
            if self._request is result:
                self._request = completed
            try:
                self.finished.emit(completed)
            except RuntimeError:
                # The owning page may already have been destroyed.
                pass

        self._thread = threading.Thread(target=inspect, name="CaptureStartInspection", daemon=True)
        self._thread.start()
        return True
