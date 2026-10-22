"""Read active business modes on the exact selected physical radio."""
import re
from .command_runner import require_command_completion
from .command_builder import quote_argument

PROBE = r"""
phy=__PHY__
test -d /sys/class/ieee80211/"$phy" || exit 81
printf '__RADIO_BUSINESS_BEGIN__\n'
for path in /sys/class/net/*; do
 test -e "$path" || continue
 owner=$(cat "$path/phy80211/name" 2>/dev/null) || continue
 test "$owner" = "$phy" || continue
 iface=${path##*/}
 case "$iface" in ''|*[!A-Za-z0-9_.:@-]*) exit 82;; esac
 info=$(iw dev "$iface" info 2>/dev/null) || exit 83
 index=$(printf '%s\n' "$info" | awk '$1=="wiphy" {print $2;exit}')
 test "phy$index" = "$phy" || exit 84
 mode=$(printf '%s\n' "$info" | awk '$1=="type" {print $2;exit}')
 flags=$(cat "$path/flags" 2>/dev/null) || exit 85
 case "$flags" in 0x*) :;; *) exit 86;; esac
 digits=${flags#0x}
 case "$digits" in ''|*[!0-9a-fA-F]*) exit 86;; esac
 printf '%s|%s|%s\n' "$iface" "$mode" "$((flags & 1))"
done
printf '__RADIO_BUSINESS_END__\n'
"""

def read_radio_business(runner, phy, cancel_event=None):
    if not isinstance(phy,str) or not re.fullmatch(r"phy[0-9]+",phy):
        raise ValueError("Invalid physical radio")
    result=runner.run("radio_business_probe",PROBE.replace("__PHY__",quote_argument(phy)),12,cancel_event)
    require_command_completion(result)
    lines=result.stdout.strip().splitlines()
    if (result.exit_code!=0 or not lines or lines[0]!="__RADIO_BUSINESS_BEGIN__"
            or lines[-1]!="__RADIO_BUSINESS_END__" or len(lines)>258):
        raise RuntimeError("\u65e0\u6cd5\u5b8c\u6574\u6838\u5b9e\u76ee\u6807\u7f51\u5361\u7684\u4e1a\u52a1\u5360\u7528\uff0c\u672a\u5f00\u59cb\u626b\u63cf\u3002")
    modes=set()
    seen=set()
    for line in lines[1:-1]:
        row=line.split("|")
        if len(row)!=3 or not re.fullmatch(r"[A-Za-z0-9_.:@-]+",row[0]) or row[0] in seen or row[2] not in ("0","1") or not re.fullmatch(r"[A-Za-z0-9_/.-]+",row[1]):
            raise RuntimeError("\u7f51\u5361\u4e1a\u52a1\u72b6\u6001\u8fd4\u56de\u4e0d\u5b8c\u6574\uff0c\u672a\u5f00\u59cb\u626b\u63cf\u3002")
        seen.add(row[0])
        if row[2]=="1" and row[1].lower() not in ("monitor","p2p-device"):
            modes.add(row[1].lower())
    return tuple(sorted(modes))

def require_radio_business_consent(runner,phy,approved,cancel_event=None):
    current=read_radio_business(runner,phy,cancel_event)
    if not isinstance(approved,(list,tuple)) or any(not isinstance(v,str) for v in approved):
        approved=()
    if not set(current)<=set(approved):
        raise RuntimeError("\u76ee\u6807\u65e0\u7ebf\u7f51\u5361\u7684\u4e2d\u7ee7\u6216\u53d1\u5c04\u4e1a\u52a1\u672a\u786e\u8ba4\u626b\u63cf\u98ce\u9669\uff0c\u8bf7\u91cd\u65b0\u70b9\u51fb\u626b\u63cf\u5e76\u786e\u8ba4\u63d0\u9192\u3002")
    return current
