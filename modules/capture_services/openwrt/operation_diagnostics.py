"""Bounded, device-scoped command metadata; never retain commands or output."""
from collections import deque
from copy import deepcopy
from datetime import datetime, timezone
from math import isfinite
from threading import RLock, local
from contextlib import contextmanager
from ..management_diagnostic_schema import ACTIONS,KINDS,CODES,SITE_MODULES

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
SAFE_VALUES["operation"] |= ACTIONS | {"configuration_apply"}
SAFE_VALUES["stage"] |= {"queue","progress","preflight","commit","download","publish","reboot","validation","connect","capabilities","session","stage","apply","verify","confirm","rollback","restore_verify","discard","finish"}
SAFE_VALUES["reason"] |= {"accepted","unchanged","reply_lost","verified","verification_false","native_rpc_error","exception","restored","restore_unconfirmed","renewal_busy",
    "saved_values","runtime_missing","runtime_pending","runtime_up","runtime_proto","runtime_device","runtime_ipv4","runtime_ipv4_mask","runtime_gateway4","runtime_gateway6","runtime_ipv6","runtime_old_ipv6","runtime_prefix6","runtime_invalid"}
SAFE_VALUES["reason"] |= {"wireless_interface_pending","wireless_configuration_pending","wireless_identity_pending","wireless_ap_pending","wireless_association_pending","wireless_runtime_pending"}
SAFE_VALUES["reason"] |= {"wireless_config_"+key for key in ("mode","ssid","encryption","key","macaddr","channel","htmode","country","txpower")}
SAFE_VALUES["kind"] = KINDS
SAFE_VALUES["outcome"] = frozenset({"reconnect","uncertain","saved","applied","restored"})
SAFE_VALUES["reason"] |= CODES


def _safe_protocol_value(name,value):
    if name == 'timestamp':
        from modules.diagnostic_redaction import valid_timestamp
        return valid_timestamp(value)
    if value in SAFE_VALUES.get(name,()):return True
    if name=="last_stage" and value in SAFE_VALUES["stage"]:return True
    if name=="cause_reason" and value in SAFE_VALUES["reason"]:return True
    if name=="last_reason" and value in SAFE_VALUES["reason"]:return True
    if name in {"code_site","cause_site"}:
        stem,sep,line=value.partition(":")
        return bool(sep and stem in SITE_MODULES and line.isascii() and line.isdigit() and len(line)<=6)
    return False


MAX_EVENTS = 64
MAX_FAILURES = 8
MAX_WORKFLOWS = 32
MAX_MANAGEMENT = 128
MAX_MANAGEMENT_FAILURES = 16
MAX_CONFIGURATION = 128
MAX_CONFIGURATION_FAILURES = 16
STRING_FIELDS = frozenset({"timestamp", "event", "operation", "status", "stage", "kind",
    "exception_type", "cause_reason", "cause_type", "cause_site", "reason", "code_site", "last_stage", "last_reason", "outcome", "transport", "band", "source", "task_band"})
NUMBER_FIELDS = frozenset({"id", "errno", "winerror", "operation_id", "elapsed_seconds", "connected_before",
    "connected_after", "exit_code", "timed_out", "cancelled", "request_bytes",
    "force_apply", "rollback", "trace_id", "connect_attempts", "verify_attempts", "restore_attempts", "native_error", "candidate_index", "original_target", "remaining_seconds", "restored",
    "task_id", "saved", "applied", "verified", "changed", "reboot_requested", "boot_state",
    "selected_count", "operation_count", "skipped_count", "identity_count",
    "completed_count", "row_count", "target_count", "removed_count", "mac_count",
    "completed", "total", "uncancellable", "queue_depth", "queue_seconds",
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
        self._epoch = 0
        self._context = local()
        self._events = deque(maxlen=MAX_EVENTS)
        self._failures = deque(maxlen=MAX_FAILURES)
        self._workflows = deque(maxlen=MAX_WORKFLOWS)
        self._management = deque(maxlen=MAX_MANAGEMENT)
        self._management_failures = deque(maxlen=MAX_MANAGEMENT_FAILURES)
        self._action_failures = {}
        self._configuration = deque(maxlen=MAX_CONFIGURATION)
        self._configuration_failures = deque(maxlen=MAX_CONFIGURATION_FAILURES)

    def bind_connection(self, identity):
        with self._lock:
            if identity != self._identity:
                self._epoch += 1
                self._events.clear()
                self._failures.clear()
                self._workflows.clear()
                self._management.clear()
                self._management_failures.clear()
                self._action_failures.clear()
                self._configuration.clear()
                self._configuration_failures.clear()
                self._identity = identity

    @property
    def epoch(self):
        with self._lock:
            return self._epoch

    @contextmanager
    def management_scope(self, task_id, epoch):
        previous=getattr(self._context,"management",None)
        self._context.management=(task_id,epoch)
        try:yield
        finally:self._context.management=previous

    def record(self, event, *, expected_epoch=None, **fields):
        with self._lock:
            scope=getattr(self._context,"management",None)
            if scope is not None:
                fields.setdefault("task_id",scope[0])
                if expected_epoch is None:expected_epoch=scope[1]
            if expected_epoch is not None and expected_epoch!=self._epoch:
                return None
            self._sequence += 1
            if event=="management" and fields.get("task_id") is None:
                fields["task_id"]=self._sequence
            row = _fields(dict(fields, event=event, id=self._sequence,
                timestamp=datetime.now(timezone.utc).isoformat()))
            self._events.append(row)
            if event=="management" or (scope is not None and event=="command"):
                self._management.append(row)
                if row.get("status") in {"failed","timeout","cancelled","blocked","uncertain"}:
                    self._management_failures.append(row)
                    if event=="management" and row.get("operation") in ACTIONS:
                        self._action_failures[row["operation"]]=row
            if event == "configuration":
                self._configuration.append(row)
                if row.get("stage")=="finish" and row.get("status") in {"failed","uncertain"}:
                    self._configuration_failures.append(row)
            if event == "workflow":
                self._workflows.append(row)
            if row.get("status") in {"failed", "timeout", "cancelled", "blocked", "uncertain"}:
                self._failures.append(row)
            return self._sequence

    def snapshot(self):
        with self._lock:
            result = {"schema_version": 1, "events": deepcopy(list(self._events)),
                    "failures": deepcopy(list(self._failures)),
                    "workflows": deepcopy(list(self._workflows)),
                    "management": deepcopy(list(self._management)),
                    "management_failures": deepcopy(list(self._management_failures)),
                    "action_failures": deepcopy(list(self._action_failures.values())),
                    "configuration": deepcopy(list(self._configuration)),
                    "configuration_failures": deepcopy(list(self._configuration_failures))}
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
    for key, limit in (("events", MAX_EVENTS), ("failures", MAX_FAILURES), ("workflows", MAX_WORKFLOWS),
                       ("action_failures", len(ACTIONS)), ("management", MAX_MANAGEMENT), ("management_failures", MAX_MANAGEMENT_FAILURES),
                       ("configuration", MAX_CONFIGURATION), ("configuration_failures", MAX_CONFIGURATION_FAILURES)):
        rows = data.get(key, [])
        if not isinstance(rows, list) or len(rows) > limit:
            raise ValueError("operation diagnostic record limit exceeded")
        result[key] = [{name: (value if _safe_protocol_value(name,value) else sanitized_text(value, masker)) if isinstance(value, str) else value
                        for name, value in _fields(row).items()} for row in rows]
    if "login_attempts" in data:
        from .login_diagnostics import sanitized_login_attempts
        result["login_attempts"] = sanitized_login_attempts(data["login_attempts"])
    return result
