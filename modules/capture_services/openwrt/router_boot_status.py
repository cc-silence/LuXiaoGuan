"""Read-only login probe; never execute a discovered startup script."""
import hashlib
from .router_policy_plan import BOOT_SCRIPT
from .command_runner import require_command_completion

EXPECTED=hashlib.sha256(BOOT_SCRIPT.encode()).hexdigest()
STATUS_SH=r'''
path=/etc/init.d/luxiaoguan-wifi-policy
if test -f /etc/rc.local && sed '/^[[:space:]]*#/d' /etc/rc.local | grep -Eq 'watch_wifi[.]sh|keep_wifi[.]sh'; then printf unknown;exit 0;fi
if test -L "$path";then printf unknown;exit 0;fi
if test ! -e "$path";then
 for link in /etc/rc.d/S??luxiaoguan-wifi-policy;do
  if test -e "$link" -o -L "$link";then printf unknown;exit 0;fi
 done
 printf on;exit 0
fi
digest=$(sha256sum "$path" 2>/dev/null) || { printf unknown;exit 0; }
digest=$(printf '%s' "$digest" | cut -d ' ' -f 1)
test "$digest" = "__EXPECTED__" || { printf unknown;exit 0; }
for link in /etc/rc.d/S??luxiaoguan-wifi-policy;do
 if test ! -e "$link" -a ! -L "$link";then continue;fi
 test "$link" = /etc/rc.d/S18luxiaoguan-wifi-policy && test -L "$link" &&
 test "$(readlink "$link")" = ../init.d/luxiaoguan-wifi-policy && test -x "$link" ||
 { printf unknown;exit 0; }
done
if test -L /etc/rc.d/S18luxiaoguan-wifi-policy;then printf off;else printf on;fi
'''.replace("__EXPECTED__",EXPECTED)


def read_boot_status(manager,cancel):
    ssh=manager.ssh
    connection,fingerprint=ssh._client,ssh.host_fingerprint
    value=manager.runner.run("router_policy_status",STATUS_SH,timeout=8,cancel_event=cancel,output_limit=64)
    require_command_completion(value)
    if ssh._client is not connection or ssh.host_fingerprint!=fingerprint or not ssh.connected:
        return dict(boot_state=None)
    state=value.stdout.strip()
    return dict(boot_state=(state=="on") if value.exit_code==0 and state in {"on","off"} else None)
