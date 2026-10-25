"""Build bounded cleanup for explicitly finished capture workspaces."""
import re

_SCRIPT = r'''
[ ! -L /tmp/winbox-capture ] || exit 1
reaped=0
same_process() {
 current=$(sed 's/^.*) //' "/proc/$pid/stat" 2>/dev/null | cut -d ' ' -f 20)
 [ "$current" = "$token" ] || return 1
 first=$(tr '\0' '\n' <"/proc/$pid/cmdline" 2>/dev/null | sed -n '1p')
 [ "$first" = "$executable" ]
}
for sid in __SESSIONS__; do
 d="/tmp/winbox-capture/$sid/"
 [ -d "$d" ] && [ ! -L "${d%/}" ] || continue
 for c in /proc/[0-9]*/cmdline; do
  pid=${c#/proc/}; pid=${pid%/cmdline}
  case "$pid" in ''|*[!0-9]*|1) continue;; esac
  [ "$pid" = "$$" ] && continue
  token=$(sed 's/^.*) //' "/proc/$pid/stat" 2>/dev/null | cut -d ' ' -f 20)
  case "$token" in ''|*[!0-9]*) continue;; esac
  executable=$(tr '\0' '\n' <"$c" 2>/dev/null | sed -n '1p')
  case "${executable##*/}" in airodump-ng|hcxdumptool|reaver|aireplay-ng|airbase-ng) :;; *) continue;; esac
  line=$(tr '\0' ' ' <"$c" 2>/dev/null) || continue
  case "$line" in *"$d"*) :;; *) continue;; esac
  same_process || continue
  kill "$pid" 2>/dev/null || continue
  reaped=$((reaped+1))
  n=0; while same_process && [ $n -lt 2 ]; do sleep 1; n=$((n+1)); done
  same_process && kill -9 "$pid" 2>/dev/null
 done
done
printf 'reaped=%s\n' "$reaped"
'''


def reap_command(session_ids):
    sessions=sorted({str(sid) for sid in (session_ids or ())
                     if re.fullmatch(r"[A-Za-z0-9]+",str(sid) or "")})
    if not sessions:
        return "printf 'reaped=0\\n'"
    return _SCRIPT.replace('__SESSIONS__',' '.join(sessions))
