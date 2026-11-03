"""Bounded, read-only kernel/netlink/hcx snapshots around capture initialization."""
from datetime import datetime, timezone
import re

MAX_SCENES = 8
NAME = r"[A-Za-z0-9_.:@-]{1,64}"
PHY = r"phy[0-9]{1,3}"
PHASES = {"prepared", "engine_failed", "after_cleanup"}
STATUSES = {"completed", "partial", "failed", "timeout", "cancelled", "disconnected", "invalid_response", "not_requested"}

TOPOLOGY_COMMAND = r"""
test -d /sys/class/net && test -r /sys/class/net && test -x /sys/class/net || exit 70
printf '__RM_CAPTURE_BEGIN__\n'
n=0
for p in /sys/class/net/*; do
 [ -e "$p/phy80211" ] || continue
 n=$((n+1)); [ "$n" -le 64 ] || exit 77
 name=${p##*/}
 phy=$(cat "$p/phy80211/name") || exit 71
 index=$(cat "$p/ifindex") || exit 72
 flags=$(cat "$p/flags") || exit 73
 stub=0
 [ "$(cat "$p/ifalias" 2>/dev/null)" != routermanager:autocapture-stub ] || stub=1
 printf 'K|%s|%s|%s|%s|%s\n' "$phy" "$name" "$index" "$flags" "$stub"
done
inventory=$(iw dev 2>/dev/null)
status=$?
printf 'R|%s\n' "$status"
printf '%s\n' "$inventory" | awk '
 /^phy#[0-9]+/ { print "P|" substr($1,5) }
 $1=="Interface" { print "I|" $2 }
 $1=="ifindex" { print "D|" $2 }
 $1=="wiphy" { print "W|" $2 }
 $1=="type" { print "T|" $2 }
'
printf '__RM_CAPTURE_END__\n'
"""


def _identity(value, pattern):
    return value if isinstance(value, str) and re.fullmatch(pattern, value) else "other"


def sanitize_scene(data, masker=None):
    from modules.diagnostic_redaction import valid_timestamp
    if not isinstance(data, dict):
        return {}
    result = dict(schema=1, radio=_identity(data.get("radio"), r"radio[0-9]{1,3}"),
                  phy=_identity(data.get("phy"), PHY), target=_identity(data.get("target"), NAME),
                  phase=data.get("phase") if isinstance(data.get("phase"), str) and data["phase"] in PHASES else "other")
    if valid_timestamp(data.get("timestamp")):
        result["timestamp"] = data["timestamp"]
    for key in ("status", "engine_status"):
        value = data.get(key)
        result[key] = value if isinstance(value, str) and value in STATUSES else "invalid_response"
    for key in ("iw_exit", "engine_exit"):
        if type(data.get(key)) is int and -1 <= data[key] <= 255:
            result[key] = data[key]
    for key in ("kernel", "netlink", "engine"):
        result[key] = []
        for row in data.get(key, [])[:64] if isinstance(data.get(key), list) else []:
            if not isinstance(row, dict):
                continue
            clean = dict(name=_identity(row.get("name"), NAME), phy=_identity(row.get("phy"), PHY))
            if type(row.get("index")) is int and 0 < row["index"] < 2**31:
                clean["index"] = row["index"]
            for flag in ("up", "app_stub_marker", "monitor_capable"):
                if type(row.get(flag)) is bool:
                    clean[flag] = row[flag]
            if row.get("mode") in ("monitor", "managed", "ap", "ap/vlan"):
                clean["mode"] = row["mode"]
            result[key].append(clean)
    for key in ("target_in_kernel", "target_in_netlink", "target_in_engine", "target_index_matches"):
        if type(data.get(key)) is bool:
            result[key] = data[key]
    registered = data.get("registered_placeholders", [])
    result["registered_placeholders"] = [name for name in registered[:64] if isinstance(name, str) and re.fullmatch(r"hcxstub[0-9]+", name)] if isinstance(registered, list) else []
    flags = data.get("engine_errors", [])
    if not isinstance(flags, list):
        flags = []
    result["engine_errors"] = [flag for flag in flags[:8] if flag in (
        "failed to get interface list", "failed to arm interface", "failed to open control sockets")]
    return result


def parse_topology(text):
    lines = text.strip().splitlines()
    if not lines or lines[0] != "__RM_CAPTURE_BEGIN__" or lines[-1] != "__RM_CAPTURE_END__" or len(lines) > 520:
        raise ValueError("Incomplete capture topology")
    data = dict(kernel=[], netlink=[])
    phy, current = "", None
    for line in lines[1:-1]:
        parts = line.split("|")
        if parts[0] == "K" and len(parts) == 6:
            _, item_phy, name, index, flags, stub = parts
            if not (re.fullmatch(PHY, item_phy) and re.fullmatch(NAME, name)
                    and index.isdecimal() and 0 < int(index) < 2**31
                    and re.fullmatch(r"0x[0-9a-fA-F]{1,8}", flags) and stub in ("0", "1")):
                raise ValueError("Invalid kernel interface")
            data["kernel"].append(dict(phy=item_phy, name=name, index=int(index),
                                       up=bool(int(flags, 16) & 1), app_stub_marker=stub == "1"))
        elif len(parts) == 2:
            kind, value = parts
            if kind == "R" and value.isdecimal() and 0 <= int(value) <= 255:
                data["iw_exit"] = int(value)
            elif kind == "P" and re.fullmatch(r"[0-9]{1,3}", value):
                phy, current = "phy" + value, None
            elif kind == "I" and re.fullmatch(NAME, value) and re.fullmatch(PHY, phy):
                current = dict(phy=phy, name=value)
                data["netlink"].append(current)
            elif kind == "D" and current is not None and value.isdecimal() and 0 < int(value) < 2**31:
                current["index"] = int(value)
            elif kind == "W" and current is not None and "phy" + value == phy:
                pass
            elif kind == "T" and current is not None and re.fullmatch(r"[A-Za-z0-9_/.-]{1,32}", value):
                current["mode"] = value.lower()
            else:
                raise ValueError("Invalid netlink interface")
        else:
            raise ValueError("Invalid capture topology record")
    if "iw_exit" not in data or any(len(data[key]) > 64 for key in ("kernel", "netlink")):
        raise ValueError("Invalid capture topology size")
    return data


def parse_engine_list(text):
    rows = []
    for line in text.splitlines():
        match = re.match(r"^\s*(\d{1,3})\s+(\d+)\s+[0-9a-fA-F]{12}\s+[0-9a-fA-F]{12}\s+([*+-])\s+(" + NAME + r")\s+", line)
        if match:
            phy, index, mode, name = match.groups()
            if 0 < int(index) < 2**31:
                rows.append(dict(phy="phy" + phy, index=int(index), name=name, monitor_capable=mode in ("*", "+")))
            if len(rows) == 64:
                break
    return rows


def collect_scene(session, phase, *, enumerate_engine=False):
    from .operation_diagnostics import OperationDiagnostics
    journal = getattr(session.runner, "operation_diagnostics", None)
    if not isinstance(journal, OperationDiagnostics):
        return
    epoch = journal.epoch
    data = dict(radio=session.radio_name, phy=session.phy, target=session.mon_iface, phase=phase,
                timestamp=datetime.now(timezone.utc).isoformat(), engine_status="not_requested")
    ssh = session.ssh_client
    sessions = getattr(getattr(session, "service", None), "sessions", {})
    data["registered_placeholders"] = []
    if isinstance(sessions, dict):
        for owner in list(sessions.values())[:64]:
            if (getattr(owner, "_bound_client", None) is session._bound_client
                    and getattr(owner, "_bound_fingerprint", None) == session._bound_fingerprint):
                names = getattr(owner, "_created_stubs", [])
                if isinstance(names, (list, tuple)):
                    data["registered_placeholders"].extend(names[:64])
                data["registered_placeholders"] = data["registered_placeholders"][:64]
    def bound():
        return (ssh.connected and getattr(ssh, "_client", None) is session._bound_client
                and getattr(ssh, "host_fingerprint", "") == session._bound_fingerprint)
    def status(result):
        return "cancelled" if result.cancelled else "timeout" if result.timed_out else "completed" if result.exit_code == 0 else "failed"
    try:
        if not bound():
            data["status"] = "disconnected"
        elif session._stop_event.is_set():
            data["status"] = "cancelled"
        else:
            result = session.runner.run("capture_interface_scene", TOPOLOGY_COMMAND, timeout=3,
                                        cancel_event=session._stop_event, output_limit=16384)
            data["status"] = status(result)
            if data["status"] == "completed":
                data.update(parse_topology(result.stdout))
                if data["iw_exit"]:
                    data["status"] = "partial"
                for source in ("kernel", "netlink"):
                    if source == "netlink" and data["iw_exit"]:
                        continue
                    data["target_in_" + source] = any(row["name"] == session.mon_iface and row["phy"] == session.phy for row in data[source])
            if enumerate_engine and bound() and not session._stop_event.is_set():
                from .command_builder import quote_argument
                tool = quote_argument(session._hcx_bin)
                command = (f"if {tool} -h 2>&1 | grep -F -- 'show INTERFACE list and terminate' >/dev/null; "
                           f"then {tool} -L 2>&1; else exit 78; fi")
                result = session.runner.run("capture_engine_interface_scene", command, timeout=3,
                                            cancel_event=session._stop_event, output_limit=16384)
                data["engine_status"], data["engine_exit"] = status(result), result.exit_code
                data["engine"] = parse_engine_list(result.stdout)
                data["engine_errors"] = [flag for flag in ("failed to get interface list", "failed to arm interface", "failed to open control sockets") if flag in result.stdout]
                if data["engine_status"] == "completed" and not data["engine_errors"] and "available wlan devices:" in result.stdout:
                    data["target_in_engine"] = any(row["name"] == session.mon_iface and row["phy"] == session.phy for row in data["engine"])
                    kernel_target = [row for row in data.get("kernel", []) if row["name"] == session.mon_iface and row["phy"] == session.phy]
                    engine_target = [row for row in data["engine"] if row["name"] == session.mon_iface and row["phy"] == session.phy]
                    if len(kernel_target) == len(engine_target) == 1:
                        data["target_index_matches"] = kernel_target[0]["index"] == engine_target[0]["index"]
                elif data["engine_status"] == "completed":
                    data["engine_status"] = "invalid_response"
    except ValueError:
        data["status"] = "invalid_response"
    except Exception:
        data["status"] = "failed"
    # Diagnostics must never mask the original startup or cleanup error.
    try:
        if bound():
            journal.capture_initialization_scene(data, epoch)
    except Exception:
        pass
