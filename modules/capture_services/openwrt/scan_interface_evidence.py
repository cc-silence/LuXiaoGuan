"""Sanitized native scan candidate choice; no SSIDs or scan results retained."""
import re
from .operation_diagnostics import OperationDiagnostics


def record_scan_selection(runner, result, radio):
    journal = getattr(runner, "operation_diagnostics", None)
    row = result.get("interface_selection") if isinstance(result, dict) else None
    if not isinstance(journal, OperationDiagnostics) or not isinstance(row, dict):
        return
    total, skipped, selected = (row.get(key) for key in ("candidates", "unavailable", "selected_index"))
    if (any(type(value) is not int for value in (total, skipped, selected))
            or not 1 <= selected <= total <= 64 or not 0 <= skipped < selected):
        return
    interface = row.get("interface")
    if not isinstance(interface, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", interface):
        return
    journal.record("workflow", operation="wireless_scan_interface", status="completed", stage="select_radio",
                   reason="verified", candidate_count=total, unavailable_count=skipped,
                   candidate_index=selected, scan_interface=interface)
