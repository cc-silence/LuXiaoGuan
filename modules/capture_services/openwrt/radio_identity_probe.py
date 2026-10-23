"""Bounded read-only native identity lookup, including radios with no interfaces."""
import json
import re
from .command_builder import quote_argument
from .command_runner import require_command_completion
from .radio_identity import IDENTITY_MARKER, STATUS_MARKER, uci_radio_bands


def enrich_radio_identity(runner, snapshot, cancel_event=None):
    snapshot = re.sub(r"(?m)^" + IDENTITY_MARKER + r"[^\r\n]*\r?\n?", "", snapshot)
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError("无线硬件身份查询已取消。")
    radios = sorted(uci_radio_bands(snapshot))
    if not radios or len(radios) > 64:
        return snapshot
    chunks = ["printf '__RM_PHYS__\\n'; ls /sys/class/ieee80211/ 2>/dev/null && printf '__RM_PHYS_OK__\\n'"]
    for radio in radios:
        request = quote_argument(json.dumps({"section": radio}, separators=(",", ":")))
        chunks.append("printf '\\n__RM_RADIO__%s\\n' " + quote_argument(radio) +
                      "; ubus -S -t 2 call iwinfo phyname " + request + " 2>/dev/null")
    chunks.append("printf '\\n__RM_END__\\n'")
    try:
        result = runner.run("radio_identity_probe", "; ".join(chunks), timeout=20,
                            cancel_event=cancel_event, output_limit=32768)
    except ValueError:
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("无线硬件身份查询已取消。")
        from .operation_diagnostics import OperationDiagnostics
        journal = getattr(runner, "operation_diagnostics", None)
        if isinstance(journal, OperationDiagnostics):
            journal.record("workflow", operation="radio_identity", stage="identity_lookup",
                           status="blocked", reason="invalid_data")
        return snapshot
    require_command_completion(result)
    data = {"phys": [], "radios": {}, "inventory_complete": False}
    raw = (result.stdout or "").replace("\r\n", "\n")
    if result.exit_code == 0 and len(raw) <= 32768 and raw.startswith("__RM_PHYS__\n") and raw.rstrip().endswith("__RM_END__"):
        parts = re.split(r"(?m)^__RM_RADIO__", raw)
        names = [part.partition("\n")[0] for part in parts[1:]]
        if names == radios:
            head = parts[0]
            if "__RM_PHYS_OK__\n" in head:
                data["inventory_complete"] = True
                data["phys"] = sorted({v for v in head.split() if re.fullmatch(r"phy[0-9]+", v)})[:64]
            for name, part in zip(names, parts[1:]):
                body = part.partition("\n")[2].split("__RM_END__", 1)[0].strip()
                try:
                    value = json.loads(body)
                except (TypeError, ValueError):
                    continue
                phy = value.get("phyname") if isinstance(value, dict) else None
                if isinstance(phy, str) and re.fullmatch(r"phy[0-9]+", phy):
                    data["radios"][name] = phy
    from .operation_diagnostics import OperationDiagnostics
    journal = getattr(runner, "operation_diagnostics", None)
    if isinstance(journal, OperationDiagnostics):
        journal.record("workflow", operation="radio_identity", stage="identity_lookup",
            status="completed",
            reason="native_identity_available" if data["radios"] else "native_identity_unavailable",
            configured_radios=len(radios), identified_radios=len(data["radios"]),
            physical_radios=len(data["phys"]))
    block = IDENTITY_MARKER + json.dumps(data, separators=(",", ":")) + "\n"
    # Keep the existing final status JSON intact for all snapshot consumers.
    pos = snapshot.find(STATUS_MARKER)
    return snapshot[:pos] + block + snapshot[pos:] if pos >= 0 else snapshot + "\n" + block
