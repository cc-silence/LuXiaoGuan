"""Bounded, device-scoped command metadata; never retain commands or output."""
from collections import deque
from copy import deepcopy
from datetime import datetime, timezone
from math import isfinite
from threading import RLock

# Exact protocol constants may survive text redaction; arbitrary strings may not.
SAFE_VALUES = {
    "operation": frozenset({"verification_prepare", "radio_identity", "radio_identity_probe",
        "password_backup", "password_read_ifaces", "password_find_zone", "password_list_radios",
        "password_setup_env", "password_reload", "password_ifup", "password_set_cred",
        "password_check_link", "password_restore", "password_restore_fallback", "password_net_restart"}),
    "stage": frozenset({"execute", "backup", "select_radio", "configure", "restore", "identity_lookup", "finish_control"}),
    "reason": frozenset({"cancelled", "timeout", "io_error", "invalid_data", "runtime_error",
        "unexpected_error", "restore_reported_errors", "phase_completed", "native_identity_available",
        "native_identity_unavailable", "configuration_untouched"}),
}
MAX_EVENTS = 64
MAX_FAILURES = 8
MAX_WORKFLOWS = 32
STRING_FIELDS = frozenset({"timestamp", "event", "operation", "status", "stage", "kind",
    "exception_type", "reason", "transport", "band", "source", "task_band"})
NUMBER_FIELDS = frozenset({"id", "operation_id", "elapsed_seconds", "connected_before",
    "connected_after", "exit_code", "timed_out", "cancelled", "request_bytes",
    "channel", "pending_processes", "takeover", "blocked", "primary_failed",
    "wireless_cleanup_confirmed", "monitor_active", "result_rows", "accepted_rows",
    "rejected_rows", "frequency_rows", "band_rows", "legacy_rows", "configured_radios", "identified_radios", "physical_radios"})


def _fields(row):
    if not isinstance(row, dict):
        raise ValueError("invalid operation diagnostic record")
    result = {key: value[:128] for key, value in row.items()
              if key in STRING_FIELDS and isinstance(value, str)}
    for key in NUMBER_FIELDS:
        value = row.get(key)
        if key in row and (value is None or type(value) in (bool, int, float)):
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if abs(value) > 10 ** 15 or not isfinite(value):
                    continue
            result[key] = value
    return result


class OperationDiagnostics:
    """Keep recent events and failures independently across same-device reconnects."""
    def __init__(self, login_diagnostics=None):
        self._login_diagnostics = login_diagnostics
        self._lock = RLock()
        self._identity = None
        self._sequence = 0
        self._events = deque(maxlen=MAX_EVENTS)
        self._failures = deque(maxlen=MAX_FAILURES)
        self._workflows = deque(maxlen=MAX_WORKFLOWS)

    def bind_connection(self, identity):
        with self._lock:
            if identity != self._identity:
                self._events.clear()
                self._failures.clear()
                self._workflows.clear()
                self._identity = identity

    def record(self, event, **fields):
        with self._lock:
            self._sequence += 1
            row = _fields(dict(fields, event=event, id=self._sequence,
                timestamp=datetime.now(timezone.utc).isoformat()))
            self._events.append(row)
            if event == "workflow":
                self._workflows.append(row)
            if row.get("status") in {"failed", "timeout", "cancelled", "blocked"}:
                self._failures.append(row)
            return self._sequence

    def snapshot(self):
        with self._lock:
            result = {"schema_version": 1, "events": deepcopy(list(self._events)),
                    "failures": deepcopy(list(self._failures)),
                    "workflows": deepcopy(list(self._workflows))}
            from .login_diagnostics import LoginDiagnostics
            if isinstance(self._login_diagnostics, LoginDiagnostics):
                result["login_attempts"] = self._login_diagnostics.snapshot()
            return result


def sanitized_operation_evidence(data, masker):
    """Apply the same allowlist again at the ZIP export boundary."""
    from modules.log_services import sanitized_text
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("unsupported operation diagnostic evidence")
    result = {"schema_version": 1}
    for key, limit in (("events", MAX_EVENTS), ("failures", MAX_FAILURES), ("workflows", MAX_WORKFLOWS)):
        rows = data.get(key, [])
        if not isinstance(rows, list) or len(rows) > limit:
            raise ValueError("operation diagnostic record limit exceeded")
        result[key] = [{name: (value if value in SAFE_VALUES.get(name, ()) else sanitized_text(value, masker)) if isinstance(value, str) else value
                        for name, value in _fields(row).items()} for row in rows]
    if "login_attempts" in data:
        from .login_diagnostics import sanitized_login_attempts
        result["login_attempts"] = sanitized_login_attempts(data["login_attempts"])
    return result
