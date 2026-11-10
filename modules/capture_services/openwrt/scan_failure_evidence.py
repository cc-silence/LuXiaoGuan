"""Privacy-preserving evidence for a failed wireless setup scan."""
import re

from .operation_diagnostics import SAFE_VALUES


def record_scan_failure(runner, environment, radio, interface, error):
    journal = getattr(runner, "operation_diagnostics", None)
    if journal is None:
        return
    fields = {"operation": "wireless_scan_failure", "stage": "execute",
              "status": "failed", "scan_temporary": not bool(interface)}
    actual_interface = getattr(error, "scan_interface", None)
    if isinstance(actual_interface, str):
        interface = actual_interface
    for key, value in (("scan_radio", radio.get("name")), ("scan_interface", interface)):
        if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", value):
            fields[key] = value
    band = radio.get("band")
    if not band:
        band = {"11g": "2g", "11b": "2g", "11a": "5g"}.get(radio.get("hwmode"))
    if band in SAFE_VALUES["band"]:
        fields["band"] = band
    rows, active = environment.get("rows"), environment.get("active")
    if isinstance(rows, list) and isinstance(active, list):
        fields["bridge_active_count"] = sum(1 for row in rows if isinstance(row, dict)
            and row.get("mode") == "sta" and row.get("device") == radio.get("name")
            and row.get("section") in active)
    target = getattr(error, "scan_target", None)
    if target in SAFE_VALUES["scan_target"]:
        fields["scan_target"] = target
    code = getattr(error, "code", "")
    match = re.fullmatch(r"rpc_iwinfo_scan_([0-9]{1,3})", code) if isinstance(code, str) else None
    if match and int(match.group(1)) <= 255:
        fields.update(scan_error="rpc_iwinfo_scan", native_error=int(match.group(1)))
    else:
        fields["scan_error"] = code if code in SAFE_VALUES["scan_error"] else "other"
        native = getattr(error, "native_error", None)
        if type(native) is int and 0 <= native <= 255:
            fields["native_error"] = native
    journal.record("workflow", **fields)
