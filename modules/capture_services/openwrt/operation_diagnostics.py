"""Bounded, device-scoped command metadata; never retain commands or output."""
from collections import deque
from copy import deepcopy
from datetime import datetime, timezone
from math import isfinite
from threading import RLock, local
from contextlib import contextmanager
from .readiness_diagnostics import FLAGS as READINESS_FLAGS, NUMBERS as READINESS_NUMBERS, FAILURES as AP_FAILURES
from ..management_diagnostic_schema import ACTIONS,KINDS,CODES,SITE_MODULES
from .password_control_diagnostics import ENUMS as CONTROL_ENUMS, NUMBERS as CONTROL_NUMBERS, STAGES as CONTROL_STAGES, REASONS as CONTROL_REASONS

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
from .verification_diagnostics import RESTORE_OPERATIONS, RESTORE_STAGES, RESTORE_REASONS
SAFE_VALUES["operation"] |= RESTORE_OPERATIONS
SAFE_VALUES["stage"] |= RESTORE_STAGES
SAFE_VALUES["reason"] |= RESTORE_REASONS
SAFE_VALUES["operation"] |= {"password_auth", "password_control_probe"}
SAFE_VALUES.update(CONTROL_ENUMS)
SAFE_VALUES["stage"] |= CONTROL_STAGES
SAFE_VALUES["reason"] |= CONTROL_REASONS
SAFE_VALUES["reason"] |= {"native_redraw_reply_recovered"}
SAFE_VALUES["operation"] |= {"password_recovery_check"}
SAFE_VALUES["reason"] |= {"recovery_record_io", "recovery_record_json", "recovery_record_validation", "recovery_directory"}
SAFE_VALUES["reason"] |= {"native_"+mode+"_"+state for mode in ("pty","pipe") for state in ("ready","unavailable")}
SAFE_VALUES["reason"] |= {"native_rpc_"+word+"_timeout" for word in ("ping","help","pmksa_flush","disconnect","add_network","remove_network","set_network","select_network","disable_network","status","unknown")}
SAFE_VALUES["reason"] |= {"authenticated", "wrong_key", "authentication_unconfirmed", "native_control_ready", "native_control_unavailable"}
SAFE_VALUES["operation"] |= ACTIONS | {"configuration_apply", "wireless_readiness", "wireless_scan_interface", "wireless_scan_failure", "wireless_scan_fallback"}
SAFE_VALUES["stage"] |= {"queue","progress","preflight","commit","download","publish","reboot","validation","connect","capabilities","session","stage","apply","verify","confirm","rollback","restore_verify","discard","finish"}
SAFE_VALUES["reason"] |= {"accepted","unchanged","reply_lost","verified","verification_false","native_rpc_error","exception","restored","restore_unconfirmed","renewal_busy",
    "saved_values","runtime_missing","runtime_pending","runtime_up","runtime_proto","runtime_device","runtime_ipv4","runtime_ipv4_mask","runtime_gateway4","runtime_gateway6","runtime_ipv6","runtime_old_ipv6","runtime_prefix6","runtime_invalid"}
SAFE_VALUES["reason"] |= {"wireless_interface_pending","wireless_configuration_pending","wireless_identity_pending","wireless_ap_pending","wireless_association_pending","wireless_runtime_pending", "wireless_config_disabled"}
SAFE_VALUES["reason"] |= {"wireless_config_"+key for key in ("mode","ssid","encryption","key","macaddr","channel","htmode","country","txpower")}
from modules.platform_services.network_diagnostics import STAGES as NETWORK_STAGES, REASONS as NETWORK_REASONS, NUMBERS as NETWORK_NUMBERS
SAFE_VALUES["stage"] |= NETWORK_STAGES
SAFE_VALUES["reason"] |= NETWORK_REASONS
SAFE_VALUES["connection_reason"] = NETWORK_REASONS
SAFE_VALUES["selection_source"] = frozenset({"ssh_socket","system_route","unknown"})
SAFE_VALUES["source"] = frozenset({"session_apply", "direct_toggle", "repair"})
SAFE_VALUES["interface_state"] = frozenset({"active", "inactive", "unknown", "missing"})
SAFE_VALUES["scan_target"] = frozenset({"radio", "interface"})
SAFE_VALUES["scan_error"] = frozenset({"scan_native", "scan_interface_read", "scan_no_interface", "scan_interface_busy", "scan_recovery", "radio", "response", "transport", "timeout", "disconnected", "scan_format", "rpc_iwinfo_scan", "rpc_iwinfo_info", "other"})
SAFE_VALUES["band"] = frozenset({"2g", "5g", "6g"})
SAFE_VALUES["ap_failure"] = AP_FAILURES
SAFE_VALUES["ap_state"] = frozenset({"UNINITIALIZED", "DISABLED", "COUNTRY_UPDATE", "ACS", "HT_SCAN", "DFS", "ENABLED"})
SAFE_VALUES["kind"] = KINDS
SAFE_VALUES["outcome"] = frozenset({"reconnect","uncertain","saved","applied","restored"})
SAFE_VALUES["reason"] |= CODES


from .wireless_saved_diagnostics import FIELDS as SAVED_VERIFICATION_FIELDS
SAFE_VALUES["verification_field"] = frozenset(SAVED_VERIFICATION_FIELDS)
SAFE_VALUES["verification_actual_type"] = frozenset({"nil","string","table","boolean","number"})
SAFE_VALUES["verification_expected_type"] = SAFE_VALUES["verification_actual_type"]


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
MAX_COMMAND_FAILURES = 32
MAX_CAPTURE_FAILURES = 16
MAX_TRANSPORT_FAILURES = 8
MAX_WORKFLOWS = 32
MAX_MANAGEMENT = 128
MAX_MANAGEMENT_FAILURES = 16
MAX_CONFIGURATION = 128
MAX_CONFIGURATION_FAILURES = 16
STRING_FIELDS = frozenset({"timestamp", "event", "operation", "status", "stage", "kind",
    "selection_source", "connection_reason", "exception_type", "cause_reason", "cause_type", "cause_site", "reason", "code_site", "last_stage", "last_reason", "outcome", "transport", "band", "source", "task_band", "ap_state", "ap_failure", "interface_state", "scan_interface", "scan_radio", "scan_error", "scan_target"})
STRING_FIELDS |= frozenset(CONTROL_ENUMS) | {"verification_field", "verification_actual_type", "verification_expected_type"}
NUMBER_FIELDS = frozenset({"id", "errno", "winerror", "operation_id", "elapsed_seconds", "connected_before",
    "connected_after", "exit_code", "timed_out", "cancelled", "request_bytes",
    "force_apply", "rollback", "trace_id", "connect_attempts", "verify_attempts", "restore_attempts", "native_error", "candidate_index", "candidate_count", "unavailable_count", "original_target", "remaining_seconds", "restored",
    "task_id", "saved", "applied", "verified", "changed", "reboot_requested", "boot_state",
    "selected_count", "operation_count", "skipped_count", "identity_count",
    "completed_count", "row_count", "target_count", "removed_count", "mac_count", "bridge_active_count", "scan_temporary",
    "completed", "total", "uncancellable", "queue_depth", "queue_seconds",
    "channel", "pending_processes", "takeover", "blocked", "primary_failed",
    "wireless_cleanup_confirmed", "monitor_active", "result_rows", "accepted_rows",
    "rejected_rows", "frequency_rows", "band_rows", "legacy_rows", "configured_radios", "identified_radios", "physical_radios"}) | NETWORK_NUMBERS | {"snapshot_cached", "rollback_seconds", "confirm_reserve_seconds", "radio_up", "runtime_pending", "interface_present", "interface_up", "configuration_pending", "identity_pending", "frequency", "radio_disabled", "retry_setup_failed", "cac_active", "cac_seconds", "requested_enabled", "configured_enabled", "ap_control_available", "kernel_ap_started", "ap_carrier"}

NUMBER_FIELDS |= {"link_report_valid", "link_route_rows", "link_neigh_rows", "link_fdb_rows",
                  "link_brctl_rows", "link_interfaces_rows", "link_neighbor_usable",
                  "link_bridge_count", "link_port_count", "link_lower_count",
                  "link_ip_available", "link_bridge_available", "link_brctl_available",
                  "link_neighbor_mac_count", "link_fdb_peer_rows", "link_brctl_peer_rows"}

NUMBER_FIELDS |= set(READINESS_FLAGS) | set(READINESS_NUMBERS) | CONTROL_NUMBERS


from .initial_security_evidence import FLAGS as INITIAL_SECURITY_FLAGS
NUMBER_FIELDS |= INITIAL_SECURITY_FLAGS


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
        from .capture_evidence import CaptureEvidence
        self._capture_evidence = CaptureEvidence()
        from .router_health_evidence import RouterHealthEvidence
        self.health = RouterHealthEvidence()
        self._sequence = 0
        self._epoch = 0
        self._context = local()
        self._events = deque(maxlen=MAX_EVENTS)
        self._failures = deque(maxlen=MAX_FAILURES)
        self._command_failures = deque(maxlen=MAX_COMMAND_FAILURES)
        self._capture_initialization_scenes = deque(maxlen=8)
        self._wireless_failures = deque(maxlen=8)
        self._radio_usage_failures = deque(maxlen=8)
        self._radio_usage_probes = deque(maxlen=32)
        self._capture_failures = deque(maxlen=MAX_CAPTURE_FAILURES)
        self._transport_failures = deque(maxlen=MAX_TRANSPORT_FAILURES)
        self._saved_verification = deque(maxlen=16)
        self._initial_security = deque(maxlen=8)
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
                self._command_failures.clear()
                self._capture_initialization_scenes.clear()
                self._wireless_failures.clear()
                self._radio_usage_failures.clear()
                self._radio_usage_probes.clear()
                self._capture_failures.clear()
                self._transport_failures.clear()
                self._saved_verification.clear()
                self._initial_security.clear()
                self._workflows.clear()
                self._management.clear()
                self._management_failures.clear()
                self._action_failures.clear()
                self._configuration.clear()
                self._configuration_failures.clear()
                self._identity = identity
                from .capture_evidence import CaptureEvidence
                self._capture_evidence = CaptureEvidence()
                from .router_health_evidence import RouterHealthEvidence
                self.health = RouterHealthEvidence()

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
                if scope[1] != self._epoch:
                    return None
                if expected_epoch is None:expected_epoch=scope[1]
            if expected_epoch is not None and expected_epoch!=self._epoch:
                return None
            self._sequence += 1
            if event=="management" and fields.get("task_id") is None:
                fields["task_id"]=self._sequence
            row = _fields(dict(fields, event=event, id=self._sequence,
                timestamp=datetime.now(timezone.utc).isoformat()))
            if row.get("verification_field") in SAVED_VERIFICATION_FIELDS:
                self._saved_verification.append(row)
            if row.get("initial_security") is True:
                self._initial_security.append(row)
            self._events.append(row)
            if row.get("connected_before") is True and row.get("connected_after") is False:
                self.health.disconnected()
            if event=="command" and row.get("status") in {"failed","timeout"}:
                self._command_failures.append(row)
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

    def wireless_failure(self, scene, expected_epoch):
        from .wireless_failure_evidence import sanitize_scene
        from modules.log_history import HistoryMasker
        clean=sanitize_scene(scene,HistoryMasker())
        with self._lock:
            if expected_epoch==self._epoch:self._wireless_failures.append(deepcopy(clean))

    def claim_radio_usage_probe(self, token, radio, expected_epoch):
        with self._lock:
            key = (token, radio)
            if expected_epoch != self._epoch or key in self._radio_usage_probes:
                return False
            self._radio_usage_probes.append(key)
            return True

    def radio_usage_failure(self, scene, expected_epoch):
        from .radio_usage_evidence import sanitize_usage_scene
        from modules.log_history import HistoryMasker
        clean = sanitize_usage_scene(scene, HistoryMasker())
        with self._lock:
            if expected_epoch == self._epoch:
                self._radio_usage_failures.append(deepcopy(clean))

    def capture_initialization_scene(self, scene, expected_epoch):
        from .capture_initialization_evidence import sanitize_scene
        clean = sanitize_scene(scene)
        with self._lock:
            if expected_epoch == self._epoch:
                self._capture_initialization_scenes.append(deepcopy(clean))

    def capture_failure(self, radio, attempt, first_log, last_log, masker, expected_epoch=None):
        from .capture_failure_evidence import sanitize_capture_failure
        clean=sanitize_capture_failure(dict(radio=radio,attempt=attempt,first_log=first_log,last_log=last_log,
            timestamp=datetime.now(timezone.utc).isoformat()),masker)
        with self._lock:
            if expected_epoch is not None and expected_epoch!=self._epoch:return
            matching=[row for row in self._capture_failures if row['radio']==clean['radio']]
            if len(matching)>=8:self._capture_failures.remove(matching[0])
            self._capture_failures.append(clean)

    def transport_failure(self, scene, expected_epoch=None):
        from .capture_transport_evidence import sanitize_transport_failure
        from modules.log_history import HistoryMasker
        clean = sanitize_transport_failure(scene, HistoryMasker())
        with self._lock:
            if expected_epoch is not None and expected_epoch != self._epoch:return
            self._transport_failures.append(deepcopy(clean))

    def capture_evidence(self, bucket, row, expected_epoch):
        with self._lock:
            if expected_epoch == self._epoch:
                self._capture_evidence.append(bucket, row, self._identity)

    def health_sample(self, data, phase, expected_epoch):
        with self._lock:
            if expected_epoch == self._epoch:
                self.health.sample(data, phase)

    def health_collection(self, status, data, expected_epoch):
        with self._lock:
            if expected_epoch == self._epoch:
                self.health.collection(status, data)

    def health_disconnected(self):
        with self._lock:
            self.health.disconnected()

    def snapshot(self):
        with self._lock:
            result = {"schema_version": 1, "saved_verification_failures": deepcopy(list(self._saved_verification)),
                    "initial_security": deepcopy(list(self._initial_security)), "events": deepcopy(list(self._events)),
                    "failures": deepcopy(list(self._failures)),
                    "command_failures": deepcopy(list(self._command_failures)),
                    "wireless_failures": deepcopy(list(self._wireless_failures)),
                    "capture_initialization_scenes": deepcopy(list(self._capture_initialization_scenes)),
                    "radio_usage_failures": deepcopy(list(self._radio_usage_failures)),
                    "capture_failures": deepcopy(list(self._capture_failures)),
                    "capture_transport_failures": deepcopy(list(self._transport_failures)),
                    "workflows": deepcopy(list(self._workflows)),
                    "management": deepcopy(list(self._management)),
                    "management_failures": deepcopy(list(self._management_failures)),
                    "action_failures": deepcopy(list(self._action_failures.values())),
                    "configuration": deepcopy(list(self._configuration)),
                    "configuration_failures": deepcopy(list(self._configuration_failures))}
            from modules.platform_services.network_policy import policy_snapshot
            result["network_policy"] = policy_snapshot()
            result["router_health"] = self.health.snapshot()
            result["capture_evidence"] = self._capture_evidence.snapshot()
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
    from .capture_evidence import sanitize_capture_evidence
    result["capture_evidence"] = sanitize_capture_evidence(data.get("capture_evidence", {}), masker)
    from .router_health_evidence import sanitize_health
    result["router_health"] = sanitize_health(data.get("router_health", {}), masker)
    from modules.platform_services.network_policy import sanitized_policy_rows
    result["network_policy"] = sanitized_policy_rows(data.get("network_policy", []))
    for key, limit in (("saved_verification_failures", 16), ("initial_security", 8), ("events", MAX_EVENTS), ("failures", MAX_FAILURES), ("command_failures", MAX_COMMAND_FAILURES), ("workflows", MAX_WORKFLOWS),
                       ("action_failures", len(ACTIONS)), ("management", MAX_MANAGEMENT), ("management_failures", MAX_MANAGEMENT_FAILURES),
                       ("configuration", MAX_CONFIGURATION), ("configuration_failures", MAX_CONFIGURATION_FAILURES)):
        rows = data.get(key, [])
        if not isinstance(rows, list) or len(rows) > limit:
            raise ValueError("operation diagnostic record limit exceeded")
        result[key] = [{name: (value if _safe_protocol_value(name,value) else sanitized_text(value, masker)) if isinstance(value, str) else value
                        for name, value in _fields(row).items()} for row in rows]
    from .wireless_failure_evidence import sanitize_scene, MAX_SCENES
    from .capture_failure_evidence import sanitize_capture_failure
    from .capture_transport_evidence import sanitize_transport_failure
    from .radio_usage_evidence import sanitize_usage_scene
    from .capture_initialization_evidence import sanitize_scene as sanitize_initialization
    for key,limit,sanitize in (("capture_initialization_scenes",8,sanitize_initialization),
                               ("radio_usage_failures",8,sanitize_usage_scene),
                               ("wireless_failures",MAX_SCENES,sanitize_scene),
                               ("capture_failures",MAX_CAPTURE_FAILURES,sanitize_capture_failure),
                               ("capture_transport_failures",MAX_TRANSPORT_FAILURES,sanitize_transport_failure)):
        rows=data.get(key,[])
        if not isinstance(rows,list) or len(rows)>limit:raise ValueError("failure scene limit exceeded")
        result[key]=[sanitize(row,masker) for row in rows]
    if "login_attempts" in data:
        from .login_diagnostics import sanitized_login_attempts
        result["login_attempts"] = sanitized_login_attempts(data["login_attempts"])
    return result
