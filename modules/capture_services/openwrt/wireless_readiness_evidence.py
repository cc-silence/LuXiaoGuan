"""Bounded readiness transitions and a retained final observation, without secrets."""
from contextlib import contextmanager
import time
from .operation_diagnostics import OperationDiagnostics, SAFE_VALUES
from .readiness_diagnostics import FLAGS as READINESS_FLAGS, readiness_evidence


class WirelessReadinessEvidence:
    MAX_TRANSITIONS = 12
    FLAGS = READINESS_FLAGS

    def __init__(self, journal, mode, band="", source="session_apply"):
        self.journal = journal if isinstance(journal, OperationDiagnostics) else None
        self.epoch = self.journal.epoch if self.journal else None
        self.started = time.monotonic()
        self.kind = mode if mode in ("ap", "sta") else "read"
        self.band = band if band in ("2g", "5g", "6g") else ""
        self.source = source if source in ("session_apply", "direct_toggle", "repair") else "session_apply"
        self.latest = {}
        self.attempts = 0
        self.transitions = 0
        self.trace_id = None

    def _emit(self, stage, status):
        if self.journal is None:
            return
        identity = self.journal.record("configuration", expected_epoch=self.epoch,
            operation="wireless_readiness", kind=self.kind, band=self.band, source=self.source, stage=stage, status=status,
            elapsed_seconds=time.monotonic()-self.started,
            verify_attempts=self.attempts, trace_id=self.trace_id, **self.latest)
        if self.trace_id is None:
            self.trace_id = identity

    def sample(self, value):
        self.attempts += 1
        fields = readiness_evidence(value)
        reason = value.get("reason")
        if not reason:
            reason = ("verified" if value.get("ready") is True else
                      "wireless_configuration_pending" if value.get("configuration_pending") is True else
                      "wireless_identity_pending" if value.get("identity_pending") is True else
                      "wireless_runtime_pending")
        fields["reason"] = reason if isinstance(reason, str) and reason in SAFE_VALUES["reason"] else "wireless_runtime_pending"
        state = value.get("interface_state")
        if isinstance(state, str) and state in SAFE_VALUES["interface_state"]:
            fields["interface_state"] = state
        changed = fields != self.latest
        self.latest = fields
        if changed and self.transitions < self.MAX_TRANSITIONS:
            self.transitions += 1
            self._emit("verify", "progress")

    @contextmanager
    def observing(self):
        try:
            yield self
        except BaseException as exc:
            code = getattr(exc, "code", "")
            if not self.latest.get("reason") and isinstance(code, str) and code in SAFE_VALUES["reason"]:
                self.latest["reason"] = code
            cancelled = isinstance(exc, InterruptedError) or code == "cancelled"
            self._emit("finish", "cancelled" if cancelled else "failed")
            raise
        else:
            self._emit("finish", "completed")
