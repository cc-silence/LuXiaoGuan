"""Bounded readiness evidence shared by snapshots and operation history."""
FLAGS = ("radio_up", "runtime_pending", "interface_present", "interface_up",
         "configuration_pending", "identity_pending", "radio_disabled", "retry_setup_failed",
         "cac_active", "requested_enabled", "configured_enabled", "ap_control_available",
         "kernel_ap_started", "ap_carrier", "kernel_flags_readable", "kernel_interface_up",
         "kernel_info_available", "ap_mode_matches", "ssid_matches", "frequency_matches")
NUMBERS = {"frequency": 100000, "native_error": 256, "device_error": 256, "channel": 1000, "cac_seconds": 3601, "cac_seconds_left": 3601, "cac_event": 10**15}
FAILURES = frozenset({"none", "invalid_ssid", "interface_down", "control_state", "control_error",
    "legacy_frequency", "legacy_clients", "legacy_info", "kernel_flags", "kernel_down",
    "kernel_carrier", "kernel_iw", "kernel_output", "kernel_identity", "kernel_mode",
    "kernel_ssid", "kernel_frequency", "ssid_mismatch", "interface_missing",
    "ambiguous_interface", "radio_pending", "radio_down", "device_state_unavailable"})
STATES = frozenset({"UNINITIALIZED", "DISABLED", "COUNTRY_UPDATE", "ACS", "HT_SCAN", "DFS", "ENABLED"})


def readiness_evidence(value):
    if not isinstance(value, dict):
        return {}
    result = {key: value[key] for key in FLAGS if type(value.get(key)) is bool}
    result.update({key: value[key] for key, upper in NUMBERS.items()
                   if type(value.get(key)) is int and 0 <= value[key] < upper})
    for key, allowed in (("ap_failure", FAILURES), ("ap_state", STATES)):
        if isinstance(value.get(key), str) and value[key] in allowed:
            result[key] = value[key]
    return result
