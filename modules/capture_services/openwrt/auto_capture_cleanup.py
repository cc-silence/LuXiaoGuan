"""Verified monitor removal shared by preflight and final capture cleanup."""
import re
from .command_builder import quote_argument as q
from .command_runner import require_command_completion

def monitor_removal_command(interface, phy):
    if not re.fullmatch(r"hcxmon[0-9]+",interface) or not re.fullmatch(r"phy[0-9]+",phy):
        raise RuntimeError("Cannot verify monitor interface ownership")
    path=q('/sys/class/net/'+interface)
    return (
        'if [ ! -e '+path+' ]; then printf ABSENT; exit 0; fi; '
        'info=$(iw dev '+q(interface)+' info) || exit 41; '
        'printf "%s\\n" "$info" | grep -Eq "^[[:space:]]*type monitor[[:space:]]*$" || exit 42; '
        'printf "%s\\n" "$info" | grep -Eq "^[[:space:]]*wiphy '+phy[3:]+'[[:space:]]*$" || exit 43; '
        'iw dev '+q(interface)+' del || exit 44; '
        '[ ! -e '+path+' ] || exit 45; printf REMOVED'
    )

def verified_monitor_result(result):
    require_command_completion(result)
    if result.exit_code != 0 or result.stdout.strip() not in {'ABSENT','REMOVED'}:
        stages={41:'read_interface',42:'interface_type',43:'interface_phy',44:'delete_interface',45:'verify_absent'}
        detail=str(result.stderr or result.stdout or 'no diagnostic output')[:2048]
        raise RuntimeError('Monitor removal could not be verified: stage='+
                           stages.get(result.exit_code,'response')+
                           ' exit='+str(result.exit_code)+'; '+detail)
    return result.stdout.strip()

def remove_monitor(runner, interface, phy):
    result=runner.run('del_mon_iface',monitor_removal_command(interface,phy),timeout=8)
    return verified_monitor_result(result)


def finished_attempt_reset_command(interface, phy, capture_file):
    """Only after SSH reports remote exit; never signal an already exited PID."""
    if (not re.fullmatch(r"hcxmon[0-9]+", interface)
            or not re.fullmatch(r"phy[0-9]+", phy)
            or not re.fullmatch(r"/root/cap/" + re.escape(interface) + r"_[0-9]{8}_[0-9]{6}\.pcapng", capture_file)):
        raise ValueError("Invalid finished capture attempt")
    path = q('/sys/class/net/' + interface)
    return (
        'info=$(iw dev ' + q(interface) + ' info) || exit 41; '
        'printf "%s\\n" "$info" | grep -Eq "^[[:space:]]*type monitor[[:space:]]*$" || exit 42; '
        'printf "%s\\n" "$info" | grep -Eq "^[[:space:]]*wiphy ' + phy[3:] + '[[:space:]]*$" || exit 43; '
        'if [ ! -s ' + q(capture_file) + ' ]; then rm -f ' + q(capture_file) + ' || exit 46; fi; '
        'rm -f ' + q('/tmp/.hcx_' + interface + '.pid') + ' || exit 47; '
        'ip link set ' + q(interface) + ' up || exit 48; '
        'flags=$(cat ' + path + '/flags) || exit 49; '
        'case "$flags" in 0x*) :;; *) exit 50;; esac; '
        'digits=${flags#0x}; case "$digits" in ""|*[!0-9a-fA-F]*) exit 50;; esac; '
        '[ "$((flags & 1))" = 1 ] || exit 51; printf READY'
    )


def reset_finished_attempt(runner, interface, phy, capture_file, cancel_event=None):
    result = runner.run('reset_iface', finished_attempt_reset_command(interface, phy, capture_file),
                        timeout=5, cancel_event=cancel_event, output_limit=4096)
    require_command_completion(result)
    if result.exit_code != 0 or result.stdout.strip() != 'READY':
        stages = {41:'read_interface',42:'interface_type',43:'interface_phy',46:'empty_artifact',
                  47:'pidfile',48:'set_up',49:'read_flags',50:'invalid_flags',51:'verify_up'}
        detail = str(result.stderr or result.stdout or 'no diagnostic output')[:1024]
        raise RuntimeError('Capture retry preparation failed: stage=' +
                           stages.get(result.exit_code,'response') + ' exit=' + str(result.exit_code) + '; ' + detail)


def owned_capture_stop_command(interface):
    # Verify the current PID identity before every signal, including after reuse.
    if not re.fullmatch(r"hcxmon[0-9]+", interface):
        raise ValueError("Invalid capture interface")
    return 'PIDFILE=' + q('/tmp/.hcx_' + interface + '.pid') + '; IFACE=' + q(interface) + '; ' + r"""
uncertain_identity() { printf PROCESS_IDENTITY_UNCONFIRMED >&2; exit 47; }
own_live() {
 if ! kill -0 "$PID" 2>/dev/null; then
  [ ! -d /proc/"$PID" ] || uncertain_identity
  return 1
 fi
 comm=$(cat /proc/"$PID"/comm 2>/dev/null) || uncertain_identity
 [ "$comm" = hcxdumptool ] || return 1
 state=$(awk '$1=="State:" {print $2;exit}' /proc/"$PID"/status 2>/dev/null)
 [ -n "$state" ] || uncertain_identity
 [ "$state" != Z ] && [ "$state" != z ] || return 1
 args=$(head -c 8193 /proc/"$PID"/cmdline 2>/dev/null | tr '\000' '\n')
 [ -n "$args" ] && [ "${#args}" -le 8192 ] || uncertain_identity
 if printf '%s\n' "$args" | awk -v iface="$IFACE" '
  previous=="-i" && $0==iface {found=1}
  $0=="-i" iface || $0=="--interface=" iface {found=1}
  {previous=$0}
  END {exit !found}'; then return 0;
 else result=$?; [ "$result" = 1 ] || uncertain_identity; return 1; fi
}
wait_owned_exit() {
 budget=$1
 while own_live && [ "$budget" -gt 0 ]; do
  if usleep 200000 2>/dev/null; then budget=$((budget-1));
  else sleep 1; budget=$((budget-5)); fi
 done
 ! own_live
}
if [ -f "$PIDFILE" ]; then
 PID=$(cat "$PIDFILE") || exit 44
 case "$PID" in ''|*[!0-9]*) :;; *)
  if [ "$PID" -gt 1 ] 2>/dev/null && own_live; then
   own_live && kill -TERM "$PID" 2>/dev/null
   if ! wait_owned_exit 10; then
    own_live && kill -KILL "$PID" 2>/dev/null
    wait_owned_exit 5 || exit 45
   fi
  fi;;
 esac
 rm -f "$PIDFILE" || exit 46
fi
printf PROCESS_STOPPED
"""
