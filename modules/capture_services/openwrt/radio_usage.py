"""Shared, fresh capture admission: interface existence is not wireless business."""
from dataclasses import dataclass, replace
import re
from .command_runner import require_command_completion
from .radio_identity import snapshot_radio_mapping, uci_radio_bands

RUNTIME_PROBE = r"""
test -d /sys/class/ieee80211 && test -r /sys/class/net && test -x /sys/class/net || exit 81
printf '__RM_USAGE_BEGIN__\n'
for path in /sys/class/ieee80211/*; do
 test -e "$path" || continue
 phy=${path##*/}
 printf 'P|%s\n' "$phy"
done
for path in /sys/class/net/*; do
 test -e "$path/phy80211" || continue
 phy=$(cat "$path/phy80211/name") || exit 83
 iface=${path##*/}
 case "$iface" in ''|*[!A-Za-z0-9_.:@-]*) exit 84;; esac
 info=$(iw dev "$iface" info 2>/dev/null) || exit 85
 index=$(printf '%s\n' "$info" | awk '$1=="wiphy" {print $2;exit}')
 test "phy$index" = "$phy" || exit 86
 mode=$(printf '%s\n' "$info" | awk '$1=="type" {print $2;exit}')
 if [ "$mode" = monitor ] && [ "$(cat "$path/ifalias" 2>/dev/null)" = routermanager:autocapture-stub ]; then
  continue
 fi
 flags=$(cat "$path/flags") || exit 87
 case "$flags" in 0x*) :;; *) exit 88;; esac
 digits=${flags#0x}
 case "$digits" in ''|*[!0-9a-fA-F]*) exit 88;; esac
 up=$((flags & 1))
 linked=0
 if [ "$up" = 1 ] && [ "$mode" = managed ]; then
  link=$(iw dev "$iface" link 2>/dev/null) || exit 89
  case "$link" in *'Not connected.'*) linked=0;; *'Connected to '*) linked=1;; *) exit 90;; esac
 elif [ "$up" = 1 ]; then
  linked=$(cat "$path/carrier") || exit 91
  case "$linked" in 0|1) :;; *) exit 92;; esac
 fi
 printf 'I|%s|%s|%s|%s|%s\n' "$phy" "$iface" "$mode" "$up" "$linked"
done
printf '__RM_USAGE_END__\n'
"""

@dataclass(frozen=True)
class RadioUsage:
    radio: str
    phy: str
    state: str  # idle, business, blocked, unknown
    reason: str
    interfaces: tuple = ()
    observations: tuple = ()
    evidence_epoch: int | None = None
    connection_id: int | None = None
    probe_token: str = ""

    @property
    def free(self):
        return self.state == "idle" if self.state != "unknown" else None

    def require_available(self):
        if self.state not in ("idle", "business"):
            raise RuntimeError(self.reason)
        return self


def _configuration(text):
    sections = {}
    for line in text.splitlines():
        match = re.fullmatch(r"wireless\.([A-Za-z0-9_@\[\]-]+)(?:\.(device|disabled|mode))?=(.*)", line.strip())
        if match:
            name, prop, value = match.groups()
            sections.setdefault(name, {})[prop or "type"] = value.strip().strip("'\"")
    return sections


def configured_radios_idle(text):
    """Configuration intent only, never a substitute for runtime admission."""
    def disabled(row):
        value = row.get("disabled", "0").lower()
        if value in ("1", "true", "yes", "on", "disabled"):
            return True
        if value in ("0", "false", "no", "off"):
            return False
        return None

    config = _configuration(text)
    result = {}
    for name, row in config.items():
        if row.get("type") != "wifi-device":
            continue
        bound = [disabled(r) for r in config.values()
                 if r.get("type") == "wifi-iface" and r.get("device") == name]
        if disabled(row) is True or all(v is True for v in bound):
            result[name] = True
        elif disabled(row) is None or any(v is None for v in bound):
            result[name] = None
        else:
            result[name] = False
    return result


def parse_radio_usage(snapshot, runtime, owned_interface=""):
    lines = runtime.strip().splitlines()
    if (not lines or lines[0] != "__RM_USAGE_BEGIN__" or lines[-1] != "__RM_USAGE_END__" or len(lines) > 1026):
        raise RuntimeError("\u65e0\u6cd5\u5b8c\u6574\u6838\u5b9e\u5c04\u9891\u4e1a\u52a1\u72b6\u6001\uff0c\u8bf7\u5237\u65b0\u65e0\u7ebf\u72b6\u6001\u3002")
    phys, rows, seen = set(), [], set()
    for line in lines[1:-1]:
        parts = line.split("|")
        if len(parts) == 2 and parts[0] == "P" and re.fullmatch(r"phy[0-9]+", parts[1]) and parts[1] not in phys:
            phys.add(parts[1])
        elif (len(parts) == 6 and parts[0] == "I" and re.fullmatch(r"phy[0-9]+", parts[1])
              and re.fullmatch(r"[A-Za-z0-9_.:@-]+", parts[2]) and re.fullmatch(r"[A-Za-z0-9_/.-]+", parts[3])
              and parts[4] in ("0", "1") and parts[5] in ("0", "1") and parts[2] not in seen):
            seen.add(parts[2])
            rows.append((parts[1], parts[2], parts[3].lower(), parts[4] == "1", parts[5] == "1"))
        else:
            raise RuntimeError("Invalid radio usage evidence; refresh wireless status.")
    if any(row[0] not in phys for row in rows):
        raise RuntimeError("Wireless hardware changed during inspection; refresh wireless status.")
    interfaces = {p: tuple(r[1] for r in rows if r[0] == p) for p in phys}
    mapping = snapshot_radio_mapping(snapshot, interfaces)
    idle = configured_radios_idle(snapshot)
    result = {}
    for radio in uci_radio_bands(snapshot):
        phy = mapping.get(radio, "")
        current = [r for r in rows if r[0] == phy and r[1] != owned_interface]
        state, reason = "unknown", "\u65e0\u6cd5\u786e\u8ba4\u5c04\u9891\u5f52\u5c5e\uff0c\u8bf7\u5237\u65b0\u65e0\u7ebf\u72b6\u6001\u3002"
        if phy in phys:
            if any(r[2] == "monitor" or re.fullmatch(r"(?:hcxmon[0-9]+|wbmon[A-Za-z0-9]{1,8})", r[1]) for r in current):
                state, reason = "blocked", "\u68c0\u6d4b\u5230\u76d1\u542c\u6216\u6293\u5305\u4e13\u7528\u63a5\u53e3\uff0c\u5f52\u5c5e\u5c1a\u672a\u786e\u8ba4\uff1b\u5df2\u505c\u6b62\u63a5\u7ba1\uff0c\u8bf7\u5bfc\u51fa\u8bca\u65ad\u65e5\u5fd7\u3002"
            elif any(r[3] and (r[4] or r[2] not in ("managed", "ap", "ap/vlan")) for r in current):
                state, reason = "business", "\u5c04\u9891\u4ecd\u6709\u65e0\u7ebf\u4e1a\u52a1\u8fd0\u884c\u3002"
            elif idle.get(radio) is True:
                state, reason = "idle", "\u65e0\u542f\u7528\u7684\u65e0\u7ebf\u4e1a\u52a1\u4e14\u65e0\u6d3b\u52a8\u8fde\u63a5\u3002"
            elif idle.get(radio) is False:
                state, reason = "business", "\u5c04\u9891\u4ecd\u6709\u542f\u7528\u7684\u65e0\u7ebf\u4e1a\u52a1\uff08\u5305\u62ec\u7b49\u5f85\u91cd\u8fde\u7684\u6865\u63a5\uff09\u3002"
        result[radio] = RadioUsage(radio, phy, state, reason, interfaces.get(phy, ()), tuple(current))
    if not result:
        raise RuntimeError("No verifiable wireless configuration; refresh wireless status.")
    return result


def read_radio_usage(runner, cancel_event=None, *, snapshot=None, owned_interface=""):
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError("Radio usage inspection cancelled.")
    from .operation_diagnostics import OperationDiagnostics
    journal = getattr(runner, "operation_diagnostics", None)
    epoch = journal.epoch if isinstance(journal, OperationDiagnostics) else None
    client = getattr(getattr(runner, "ssh_client", None), "_client", None)
    if snapshot is None:
        from .radio_takeover import OpenWrtRadioTakeover
        snapshot = OpenWrtRadioTakeover(runner).snapshot_wireless(cancel_event)
    result = runner.run("radio_usage", RUNTIME_PROBE, timeout=20, cancel_event=cancel_event, output_limit=65536)
    require_command_completion(result)
    if result.exit_code != 0:
        raise RuntimeError("Cannot inspect wireless business; refresh wireless status.")
    if (getattr(getattr(runner, "ssh_client", None), "_client", None) is not client
            or isinstance(journal, OperationDiagnostics) and journal.epoch != epoch):
        raise RuntimeError("Wireless connection changed during usage inspection; refresh wireless status.")
    from uuid import uuid4
    token = uuid4().hex
    return {name: replace(usage, evidence_epoch=epoch, connection_id=id(client), probe_token=token)
            for name, usage in parse_radio_usage(snapshot, result.stdout, owned_interface).items()}


def usage_for_phy(usages, phy, *, runner=None, cancel_event=None):
    matches = [u for u in usages.values() if u.phy == phy]
    if len(matches) != 1:
        raise RuntimeError("Cannot uniquely identify radio ownership; refresh wireless status.")
    usage = matches[0]
    if usage.state == "blocked" and runner is not None:
        from .radio_usage_evidence import record_usage_conflicts
        record_usage_conflicts(runner, {usage.radio: usage}, cancel_event)
    return usage.require_available()
