"""Read-only router-side SSH return-path evidence and radio conflict policy."""
from dataclasses import dataclass
import ipaddress
import re
from .command_runner import require_command_completion

# Read the route to the actual SSH peer, then resolve bridge/VLAN egress.
MANAGEMENT_LINK_COMMAND = r"""
set -f
set -- ${SSH_CONNECTION:-}
[ "$#" -eq 4 ] || exit 81
peer="$1"; local_ip="$3"
case "$peer$local_ip" in *[!0-9A-Fa-f:.%]*) exit 82;; esac
# Restore pathname expansion for the sysfs interface and lower-device loops.
set +f
case "$peer" in *:*) family=-6;; *) family=-4;; esac
route=$(ip "$family" route get "$peer" from "$local_ip" 2>/dev/null) || exit 83
dev=$(printf '%s\n' "$route" | awk 'NR==1 {for(i=1;i<NF;i++) if($i=="dev"){print $(i+1);exit}}')
next=$(printf '%s\n' "$route" | awk 'NR==1 {for(i=1;i<NF;i++) if($i=="via"){print $(i+1);exit}}')
[ -n "$next" ] || next="$peer"
case "$dev" in ''|*[!A-Za-z0-9_.:@-]*) exit 84;; esac
printf '__MGLINK_ROUTE__\n%s\n' "$route"
printf '__MGLINK_NEIGH__\n'
ip "$family" neigh show to "$next" dev "$dev" 2>/dev/null
printf '__MGLINK_FDB__\n'
bridge fdb show br "$dev" 2>/dev/null
printf '__MGLINK_BRCTL__\n'
brctl showmacs "$dev" 2>/dev/null
printf '__MGLINK_INTERFACES__\n'
for path in /sys/class/net/*; do
    [ -e "$path" ] || continue
    name=${path##*/}
    phy=$(cat "$path/phy80211/name" 2>/dev/null)
    type=$(cat "$path/type" 2>/dev/null)
    port=$(cat "$path/brport/port_no" 2>/dev/null)
    kind=link; [ ! -d "$path/bridge" ] || kind=bridge
    lowers=
    for lower in "$path"/lower_*; do
        [ -e "$lower" ] || continue
        lower=${lower##*/lower_}
        lowers="$lowers $lower"
    done
    printf '%s|%s|%s|%s|%s|%s\n' "$name" "$phy" "$type" "$kind" "$port" "$lowers"
done
printf '__MGLINK_END__\n'
"""


@dataclass(frozen=True)
class ManagementLink:
    status: str = "unknown"
    interface: str = ""
    phy: str = ""
    reason: str = "unverified"


def _sections(text):
    result = {}
    current = None
    for line in text.splitlines():
        if line.startswith("__MGLINK_") and line.endswith("__"):
            current = line[9:-2].lower()
            result[current] = []
        elif current is not None:
            result[current].append(line.strip())
    return result


def parse_management_link(text):
    if not isinstance(text, str) or len(text.encode("utf-8")) > 256 * 1024:
        return ManagementLink(reason="invalid_report")
    parts = _sections(text)
    if not {"route", "neigh", "fdb", "brctl", "interfaces", "end"} <= parts.keys():
        return ManagementLink(reason="incomplete_report")
    routes = [line.split() for line in parts["route"] if " dev " in " " + line + " "]
    if len(routes) != 1:
        return ManagementLink(reason="ambiguous_route")
    fields = routes[0]
    try:
        dev = fields[fields.index("dev") + 1]
        next_hop = fields[fields.index("via") + 1] if "via" in fields else fields[0]
        ipaddress.ip_address(next_hop.split("%")[0])
    except (ValueError, IndexError):
        return ManagementLink(reason="invalid_route")
    links = {}
    for line in parts["interfaces"]:
        row = line.split("|")
        if len(row) != 6 or row[0] in links:
            return ManagementLink(reason="invalid_interfaces")
        name, phy, kind_num, kind, port, lower = row
        links[name] = (phy, kind_num, kind, port, lower.split())
    if not links or len(links) > 256:
        return ManagementLink(reason="invalid_interfaces")

    # A direct route through a bridge needs a unique forwarding port for its
    # actual next-hop MAC; SSID and PC adapter state are never consulted.
    def bridge_port(bridge):
        neighbors = set()
        for line in parts["neigh"]:
            row = line.split()
            if not row or row[0].split("%")[0] != next_hop.split("%")[0]:
                continue
            if "FAILED" in row or "INCOMPLETE" in row or "lladdr" not in row:
                continue
            try:
                mac = row[row.index("lladdr") + 1].lower()
            except IndexError:
                continue
            if re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", mac):
                neighbors.add(mac)
        if len(neighbors) != 1:
            return ""
        mac = next(iter(neighbors))
        candidates = set()
        for line in parts["fdb"]:
            row = line.split()
            if not row or row[0].lower() != mac or "dev" not in row or "self" in row:
                continue
            try:
                candidates.add(row[row.index("dev") + 1])
            except IndexError:
                continue
        if not candidates:
            ports = set()
            for line in parts["brctl"]:
                row = line.split()
                if len(row) >= 3 and row[1].lower() == mac and row[2].lower() == "no":
                    try:
                        ports.add(int(row[0], 10))
                    except ValueError:
                        pass
            for name, info in links.items():
                try:
                    if name in links[bridge][4] and info[3] and int(info[3], 0) in ports:
                        candidates.add(name)
                except ValueError:
                    continue
        candidates.discard(bridge)
        return next(iter(candidates)) if len(candidates) == 1 else ""

    seen = set()
    for _ in range(8):
        if dev in seen or dev not in links:
            return ManagementLink(reason="unresolved_interface")
        seen.add(dev)
        phy, kind_num, kind, _port, lower = links[dev]
        if phy:
            if re.fullmatch(r"phy[0-9]+", phy):
                return ManagementLink("wireless", dev, phy, "ssh_return_path")
            return ManagementLink(reason="invalid_phy")
        if kind == "bridge":
            dev = bridge_port(dev)
            if not dev:
                return ManagementLink(reason="unresolved_bridge")
        elif len(lower) == 1:
            dev = lower[0]
        elif not lower and kind_num == "1":
            return ManagementLink("wired", dev, "", "ssh_return_path")
        else:
            return ManagementLink(reason="unsupported_link")
    return ManagementLink(reason="topology_depth")


def read_management_link(runner, cancel_event=None):
    result = runner.run("management_link_probe", MANAGEMENT_LINK_COMMAND, 12, cancel_event)
    require_command_completion(result)
    link = parse_management_link(result.stdout) if result.exit_code == 0 else ManagementLink(reason="probe_failed")
    journal = getattr(runner, "operation_diagnostics", None)
    if journal is not None:
        journal.record("management_link", operation="ssh_return_path", source="router_route_fdb",
                       status=link.status, transport=link.status, reason=link.reason)
    return link


def require_management_link(runner, target_phy, cancel_event=None):
    link = read_management_link(runner, cancel_event)
    blocked = link.status == "unknown" or (link.status == "wireless" and link.phy == target_phy)
    journal = getattr(runner, "operation_diagnostics", None)
    if journal is not None:
        journal.record("safety_gate", operation="management_link_check", source="router_route_fdb",
                       transport=link.status, status="blocked" if blocked else "allowed", blocked=blocked,
                       reason="unverified_path" if link.status == "unknown" else
                              "management_radio" if link.phy == target_phy else "independent_path")
    if link.status == "unknown":
        raise RuntimeError("\u65e0\u6cd5\u6838\u5b9e\u8def\u7531\u5668\u5f53\u524d\u767b\u5f55\u8fde\u63a5\u7684\u5b9e\u9645\u63a5\u53e3\uff0c\u5df2\u505c\u6b62\u672c\u6b21\u65e0\u7ebf\u64cd\u4f5c\u3002\u8bf7\u4fdd\u6301\u8fde\u63a5\u5e76\u5bfc\u51fa\u65e5\u5fd7\u6838\u67e5\u3002")
    if blocked:
        raise RuntimeError("\u8def\u7531\u5668\u5df2\u6838\u5b9e\uff1a\u5f53\u524d SSH \u767b\u5f55\u8fde\u63a5\u7ecf\u8fc7 " + link.interface +
                           "\uff08" + link.phy + "\uff09\uff0c\u672c\u6b21\u64cd\u4f5c\u4f1a\u5360\u7528\u540c\u4e00\u5f20\u65e0\u7ebf\u7f51\u5361\uff0c\u5df2\u505c\u6b62\u3002\u8bf7\u6539\u7528\u6709\u7ebf\u6216\u5176\u4ed6\u7f51\u5361\u767b\u5f55\u540e\u91cd\u8bd5\u3002")
    return link
