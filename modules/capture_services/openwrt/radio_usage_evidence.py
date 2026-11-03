"""Failure-only radio conflicts: bounded identities and matching process metadata."""
from datetime import datetime, timezone
import re
from modules.diagnostic_redaction import valid_timestamp

MAX_SCENES = 8
TOOLS = frozenset(("hcxdumptool", "tcpdump", "airodump-ng", "aireplay-ng",
                   "dumpcap", "tshark", "kismet", "wash", "reaver"))
STATUSES = frozenset(("completed", "partial", "limited", "failed", "timeout",
                      "cancelled", "disconnected", "invalid_response"))


def sanitize_usage_scene(data, masker):
    from modules.log_services import sanitized_text
    if not isinstance(data, dict):
        return {}
    def identifier(value, pattern):
        return sanitized_text(value, masker) if isinstance(value, str) and re.fullmatch(pattern, value) else "other"
    result = dict(schema=1, radio=identifier(data.get("radio"), r"radio[0-9]{1,3}"),
                  phy=identifier(data.get("phy"), r"phy[0-9]{1,3}"),
                  state="blocked", process_scan=data.get("process_scan") if isinstance(data.get("process_scan"), str) and data["process_scan"] in STATUSES else "invalid_response")
    if valid_timestamp(data.get("timestamp")):
        result["timestamp"] = data["timestamp"]
    result["interfaces"] = []
    for row in data.get("interfaces", [])[:64] if isinstance(data.get("interfaces"), list) else []:
        if not isinstance(row, dict):
            continue
        clean = dict(name=identifier(row.get("name"), r"[A-Za-z0-9_.:@-]{1,64}"),
                     mode=row.get("mode") if row.get("mode") in ("monitor", "managed", "ap", "ap/vlan") else "other")
        for key in ("up", "linked", "conflict"):
            if type(row.get(key)) is bool:
                clean[key] = row[key]
        result["interfaces"].append(clean)
    result["processes"] = []
    for row in data.get("processes", [])[:64] if isinstance(data.get("processes"), list) else []:
        if (not isinstance(row, dict) or not isinstance(row.get("tool"), str) or row["tool"] not in TOOLS
                or type(row.get("pid")) is not int or not 0 < row["pid"] < 2**31):
            continue
        result["processes"].append(dict(pid=row["pid"], tool=row["tool"],
            interface=identifier(row.get("interface"), r"[A-Za-z0-9_.:@-]{1,64}"), match="interface_argument"))
    result["ownership"] = "unconfirmed"
    return result


def process_probe(interfaces):
    from .command_builder import quote_argument
    names = tuple(dict.fromkeys(interfaces))[:64]
    if any(not re.fullmatch(r"[A-Za-z0-9_.:@-]{1,64}", name) for name in names):
        raise ValueError("Invalid diagnostic interface")
    return r"""
for tool in cat head tr grep; do command -v "$tool" >/dev/null 2>&1 || exit 71;done
printf '__RM_PROCESS_BEGIN__\n'
n=0
matches=0
limited=0
partial=0
for p in /proc/[0-9]*; do
 n=$((n+1));if [ "$n" -gt 1024 ]; then limited=1;break;fi
 comm=$(cat "$p/comm" 2>/dev/null) || { partial=1;continue; }
 case "$comm" in hcxdumptool|tcpdump|airodump-ng|aireplay-ng|dumpcap|tshark|kismet|wash|reaver) :;; *) continue;; esac
 args=$(head -c 8193 "$p/cmdline" 2>/dev/null | tr '\000' '\n') || { partial=1;continue; }
 if [ -z "$args" ] || [ "${#args}" -gt 8192 ]; then partial=1;continue;fi
 for iface in """ + " ".join(quote_argument(name) for name in names) + r"""; do
  if printf '%s\n' "$args" | grep -F -x -e "$iface" -e "-i$iface" -e "--interface=$iface" >/dev/null; then
   matches=$((matches+1));if [ "$matches" -gt 64 ]; then limited=1;break 2;fi
   printf 'P|%s|%s|%s\n' "$iface" "${p##*/}" "$comm"
  fi
 done
done
printf 'S|%s|%s\n__RM_PROCESS_END__\n' "$limited" "$partial"
"""


def collect_usage_conflicts(runner, usages, cancel, epoch, connection):
    from .operation_diagnostics import OperationDiagnostics
    journal = getattr(runner, "operation_diagnostics", None)
    blocked = [usage for usage in usages.values() if usage.state == "blocked"][:MAX_SCENES]
    if not isinstance(journal, OperationDiagnostics) or not blocked:
        return
    names = [name for usage in blocked for name in usage.interfaces]
    status, processes = "failed", []
    ssh = getattr(runner, "ssh_client", None)
    if cancel is not None and cancel.is_set():
        return
    if ssh is not None and (ssh.connected is False or getattr(ssh, "_client", None) is not connection):
        return
    try:
        result = runner.run("radio_usage_process_scene", process_probe(names), timeout=3,
                            cancel_event=cancel, output_limit=16384)
        status = "cancelled" if result.cancelled else "timeout" if result.timed_out else "failed"
        if not result.cancelled and not result.timed_out and result.exit_code == 0:
            lines = result.stdout.strip().splitlines()
            if (not 3 <= len(lines) <= 67 or lines[0] != "__RM_PROCESS_BEGIN__"
                    or lines[-1] != "__RM_PROCESS_END__"):
                raise ValueError("Invalid process scene")
            summary = lines[-2].split("|")
            if len(summary) != 3 or summary[0] != "S" or any(v not in ("0", "1") for v in summary[1:]):
                raise ValueError("Missing process scan completion")
            status = "limited" if summary[1] == "1" else "partial" if summary[2] == "1" else "completed"
            for line in lines[1:-2]:
                parts = line.split("|")
                if (len(parts) == 4 and parts[0] == "P" and parts[1] in names
                      and parts[2].isdigit() and parts[3] in TOOLS):
                    processes.append(dict(interface=parts[1], pid=int(parts[2]), tool=parts[3]))
                else:
                    raise ValueError("Invalid process metadata")
    except Exception:
        status, processes = "failed", []
    if (cancel is not None and cancel.is_set()) or (ssh is not None and
            (ssh.connected is False or getattr(ssh, "_client", None) is not connection)):
        return
    if len(set(names)) > 64 and status == "completed":
        status = "limited"
    for usage in blocked:
        interfaces = [dict(name=row[1], mode=row[2], up=row[3], linked=row[4],
                           conflict=row[2] == "monitor" or bool(re.fullmatch(r"(?:hcxmon[0-9]+|wbmon[A-Za-z0-9]{1,8})", row[1])))
                      for row in usage.observations[:64]]
        scene = dict(radio=usage.radio, phy=usage.phy, process_scan=status,
                     interfaces=interfaces, processes=[p for p in processes if p["interface"] in usage.interfaces],
                     timestamp=datetime.now(timezone.utc).isoformat())
        journal.radio_usage_failure(scene, epoch)


def record_usage_conflicts(runner, usages, cancel=None):
    from .operation_diagnostics import OperationDiagnostics
    journal = getattr(runner, "operation_diagnostics", None)
    if not isinstance(journal, OperationDiagnostics):
        return
    epoch = journal.epoch
    connection = getattr(getattr(runner, "ssh_client", None), "_client", None)
    current = {name: usage for name, usage in usages.items()
               if usage.state == "blocked" and (usage.evidence_epoch is None or usage.evidence_epoch == epoch)
               and (usage.connection_id is None or usage.connection_id == id(connection))}
    current = {name: usage for name, usage in current.items()
               if not usage.probe_token or journal.claim_radio_usage_probe(usage.probe_token, name, epoch)}
    try:
        collect_usage_conflicts(runner, current, cancel, epoch, connection)
    except Exception:
        # Secondary diagnostics must not replace the original admission refusal.
        return
