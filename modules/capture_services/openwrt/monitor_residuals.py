"""Login-time collection of idle, positively owned capture interfaces.

No UCI edits, radio reloads, process killing or name-only ownership guesses.
"""
import re
from .command_runner import require_command_completion

# POSIX shell / BusyBox utilities shared by the supported OpenWrt releases.
SCRIPT = r'''LC_ALL=C
export LC_ALL
for tool in iw cat head tr grep awk; do
 command -v "$tool" >/dev/null 2>&1 || exit 71
done
[ -d /sys/class/net ] && [ -r /proc/net/packet ] || exit 71
busy() {
 # Check packet sockets too: a program need not mention its interface in argv.
 awk -v i="$index" 'NR==1 {if ($5!="Iface") exit 2;next} NF<9 || $5!~/^[0-9]+$/ || NR>4096 {exit 2} $5==i || $5==0 {found=1} END {if(NR==0) exit 2; if(found) exit 1}' /proc/net/packet || return 0
 n=0
 for p in /proc/[0-9]*; do
  [ "${p##*/}" = "$$" ] && continue
  n=$((n+1)); [ "$n" -le 1024 ] || return 0
  [ -d "$p" ] || continue
  [ -r "$p/cmdline" ] || { [ ! -d "$p" ] && continue; return 0; }
  args=$(head -c 8193 "$p/cmdline" 2>/dev/null | tr '\000' '\n')
  [ -n "$args" ] || { [ ! -d "$p" ] || [ "$(cat "$p/stat" 2>/dev/null | awk '{print $3}')" = Z ] || {
   # Kernel threads have an empty cmdline and no userspace memory.
   [ -r "$p/status" ] && ! grep '^VmSize:' "$p/status" >/dev/null || return 0
  }; continue; }
  [ "${#args}" -le 8192 ] || return 0
  if printf '%s\n' "$args" | grep -F -x -e "$iface" -e "-i$iface" -e "--interface=$iface" >/dev/null; then return 0; fi
 done
 return 1
}
report_kept() {
 if [ "__MODE__" = preview ]; then printf 'row|%s|%s|%s|%s|kept\n' "$iface" "$phy" "$index" "$owner";
 else printf 'kept=%s\n' "$iface"; fi
}
count=0
for path in /sys/class/net/wbmon* /sys/class/net/hcxmon*; do
 [ -e "$path" ] || continue
 iface=${path##*/}
 case "$iface" in hcxmon*) [ "__MODE__" != auto ] || continue; suffix=${iface#hcxmon};
  case "$suffix" in ''|*[!0-9]*) continue;; esac;;
 *) suffix=${iface#wbmon};; esac
 case "$suffix" in ''|*[!a-zA-Z0-9]*) continue;; esac
 [ "${#suffix}" -le 8 ] || continue
 case " __SELECTED__ " in *" $iface "*) :;; *) [ "__MODE__" != cleanup ] || continue;; esac
 phy=unknown; index=0; owner=-
 case " __PROTECTED__ " in *" $iface "*) report_kept; continue;; esac
 count=$((count+1)); [ "$count" -le 64 ] || exit 72
 raw_owner=$(cat "$path/ifalias" 2>/dev/null) || { report_kept; continue; }
 token=${raw_owner#routermanager:}
 valid_owner=0
 case "$token" in *[!a-f0-9]*|'') :;; *)
  if [ "$raw_owner" != "$token" ] && [ "${#token}" = 32 ]; then valid_owner=1; owner=$raw_owner; fi;; esac
 if [ "$valid_owner" = 0 ]; then
  case "$iface:$raw_owner:__MODE__" in hcxmon*::preview|hcxmon*::cleanup) :;; *) report_kept;continue;; esac
 fi
 phy=$(cat "$path/phy80211/name" 2>/dev/null) || exit 73
 phy_number=${phy#phy}
 [ "$phy_number" != "$phy" ] || exit 73
 case "$phy_number" in ''|*[!0-9]*) exit 73;; esac
 index=$(cat "$path/ifindex" 2>/dev/null) || exit 73
 case "$index" in ''|*[!0-9]*) exit 73;; esac
 kind=$(iw dev "$iface" info 2>/dev/null | awk '$1=="type" {print $2}')
 case "$kind" in monitor) :;; *) report_kept; continue;; esac
 [ ! -e "$path/master" ] || { report_kept; continue; }
 if [ "__FORCE__" != 1 ] && busy; then
  if [ "__MODE__" = preview ]; then printf 'row|%s|%s|%s|%s|busy\n' "$iface" "$phy" "$index" "$owner";
  else report_kept; fi
  continue
 fi
 if [ "__MODE__" = preview ]; then
  printf 'row|%s|%s|%s|%s|idle\n' "$iface" "$phy" "$index" "$owner"
  continue
 fi
 if [ "__MODE__" = cleanup ]; then
  case " __EXPECTED__ " in *" $iface|$phy|$index|$owner "*) :;; *) exit 76;; esac
 fi
 # Revalidate kernel identity immediately before the mutation.
 [ "$(cat "$path/ifalias" 2>/dev/null)" = "$raw_owner" ] &&
 [ "$(cat "$path/phy80211/name" 2>/dev/null)" = "$phy" ] &&
 [ "$(cat "$path/ifindex" 2>/dev/null)" = "$index" ] || exit 73
 iw dev "$iface" del || exit 74
 [ ! -e "$path" ] || exit 75
 printf 'removed=%s\n' "$iface"
done
printf 'done\n'
'''


def build_script(*, mode="auto", protected=(), selected=(), expected=(), force=False):
    if mode not in {"auto", "preview", "cleanup"} or type(force) is not bool or (force and mode != "cleanup"):
        raise ValueError("Invalid monitor operation")
    values = {"__FORCE__": str(int(force)), "__MODE__": mode, "__PROTECTED__": " ".join(protected),
              "__SELECTED__": " ".join(selected), "__EXPECTED__": " ".join(expected)}
    # All substitutions are protocol identifiers, never arbitrary user text.
    if any(not re.fullmatch(r"[A-Za-z0-9_:| /+-]*", value) for value in values.values()):
        raise ValueError("Invalid monitor identity")
    script=SCRIPT
    for key,value in values.items():script=script.replace(key,value)
    return script


def recover_idle_monitors(runner, cancel_event=None, *, protected=()):
    """Return removed/retained counts; incomplete inspection is an error."""
    protected = tuple(protected)
    if any(not isinstance(n, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,15}", n) for n in protected):
        raise ValueError("Invalid protected monitor")
    result = runner.run("recover_idle_monitors", build_script(protected=protected),
                        timeout=20, cancel_event=cancel_event, output_limit=8192)
    require_command_completion(result)
    lines = result.stdout.splitlines()
    if result.exit_code != 0 or not lines or lines[-1] != "done" or len(lines)>65:
        raise RuntimeError("Temporary capture interface recovery was not confirmed")
    removed = kept = 0
    seen = set()
    for line in lines[:-1]:
        match = re.fullmatch(r"(removed|kept)=(wbmon[A-Za-z0-9]{1,8})", line)
        if not match or match[2] in seen:
            raise RuntimeError("Invalid capture interface recovery response")
        seen.add(match[2])
        removed += match[1] == "removed"
        kept += match[1] == "kept"
    return removed, kept


def login_monitor_cleanup(backend, cancel_event=None):
    """Keep login usable for diagnostics when an unknown interface is retained."""
    record = backend.radio_session.record
    protected = (record.interface,) if record is not None else ()
    journal = getattr(backend.command_runner, "operation_diagnostics", None)
    def record(status, **fields):
        if journal is not None:
            journal.record("recovery", operation="recover_idle_monitors", status=status, **fields)
    record("started")
    try:
        removed, kept = recover_idle_monitors(backend.command_runner, cancel_event, protected=protected)
    except InterruptedError:
        record("cancelled")
        raise
    except Exception as exc:
        record("failed", exception_type=type(exc).__name__, reason="restore_unconfirmed")
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("Capture recovery cancelled")
        return "\u4e34\u65f6\u6293\u5305\u63a5\u53e3\u6e05\u7406\u672a\u786e\u8ba4\uff0c\u8bf7\u4fdd\u6301\u8def\u7531\u5668\u5728\u7ebf\u540e\u91cd\u65b0\u767b\u5f55\u3002"
    record("blocked" if kept else "completed", removed_count=removed, skipped_count=kept,
           wireless_cleanup_confirmed=not bool(kept))
    note = (f"\u5df2\u6e05\u7406 {removed} \u4e2a\u9057\u7559\u6293\u5305\u63a5\u53e3\u3002" if removed else "")
    if kept:
        note += f"{kept} \u4e2a\u4e34\u65f6\u63a5\u53e3\u4ecd\u5728\u4f7f\u7528\u6216\u5f52\u5c5e\u672a\u786e\u8ba4\uff0c\u5df2\u4fdd\u7559\u5e76\u8bb0\u5f55\u8bca\u65ad\u3002"
    return note
