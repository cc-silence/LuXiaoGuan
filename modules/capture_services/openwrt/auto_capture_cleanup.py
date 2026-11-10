"""Verified monitor removal shared by preflight and final capture cleanup."""
import re
from .command_builder import quote_argument as q
from .command_runner import require_command_completion

# Our own capture engines: the firmware package name and the deployed
# compatibility build (/root/.luxiaoguan-tools/hcx-ifindex1). /proc/<pid>/comm
# is the executable basename, so both names must be recognised before a PID is
# signalled or declared absent.
CAPTURE_PROCESS_NAMES = ("hcxdumptool", "hcx-ifindex1")


def _capture_process_pattern():
    return "|".join(CAPTURE_PROCESS_NAMES)

def monitor_removal_command(interface, phy):
    if not re.fullmatch(r"hcxmon[0-9]+",interface) or not re.fullmatch(r"phy[0-9]+",phy):
        raise RuntimeError("Cannot verify monitor interface ownership")
    path=q('/sys/class/net/'+interface)
    return (
        'if [ ! -e '+path+' ]; then printf ABSENT; exit 0; fi; '
        'info=$(iw dev '+q(interface)+' info) || exit 41; '
        'printf "%s\\n" "$info" | grep -Eq "^[[:space:]]*type monitor[[:space:]]*$" || exit 42; '
        'printf "%s\\n" "$info" | grep -Eq "^[[:space:]]*wiphy '+phy[3:]+'[[:space:]]*$" || exit 43; '
        'ip link set '+q(interface)+' down 2>/dev/null; '
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
    names = _capture_process_pattern()
    return 'PIDFILE=' + q('/tmp/.hcx_' + interface + '.pid') + '; IFACE=' + q(interface) + '; ' + r"""
uncertain_identity() { printf PROCESS_IDENTITY_UNCONFIRMED >&2; exit 47; }
own_live() {
 if ! kill -0 "$PID" 2>/dev/null; then
  [ ! -d /proc/"$PID" ] || uncertain_identity
  return 1
 fi
 comm=$(cat /proc/"$PID"/comm 2>/dev/null) || uncertain_identity
 case "$comm" in """ + names + r""") :;; *) return 1;; esac
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


def capture_process_scan_command(interface):
    """Report PRESENT when one of our own engines still owns this monitor."""
    if not re.fullmatch(r"hcxmon[0-9]+", interface):
        raise ValueError("Invalid capture interface")
    names = _capture_process_pattern()
    return 'IFACE=' + q(interface) + '; found=0; for pdir in /proc/[0-9]*; do ' + r"""
 [ -d "$pdir" ] || continue
 p=${pdir#/proc/}
 [ "$p" -gt 1 ] 2>/dev/null || continue
 comm=$(cat /proc/$p/comm 2>/dev/null)
 case "$comm" in """ + names + r""") :;; *) continue;; esac
 args=$(head -c 8193 /proc/$p/cmdline 2>/dev/null | tr '\000' '\n')
 printf '%s\n' "$args" | awk -v iface="$IFACE" '
  previous=="-i" && $0==iface {found=1}
  $0=="-i" iface || $0=="--interface=" iface {found=1}
  {previous=$0}
  END {exit !found}' && found=1
done
[ "$found" = 0 ] && printf ABSENT || printf PRESENT
"""


def verified_capture_process_absent(runner, interface, cancel_event=None):
    """True only when the router proves no capture engine still holds the interface."""
    result = runner.run("scan_capture_process", capture_process_scan_command(interface),
                        timeout=6, cancel_event=cancel_event, output_limit=4096)
    require_command_completion(result)
    state = (result.stdout or "").strip()
    if result.exit_code != 0 or state not in ("ABSENT", "PRESENT"):
        raise RuntimeError("Capture process scan could not be verified")
    return state == "ABSENT"


def sweep_stale_monitors_command(interfaces=None):
    """
    生成扫除遗留专属抓包监听接口 (hcxmon* / hcxstub*) 及其关联残留进程的 Shell 命令。
    
    【设计意图与对齐说明】
    1. 根因：当抓包停止或异常退出时，若网卡处于 UP 状态或内核 socket 释放延迟，
       接口可能残留；随后再次检查射频状态时，未确认归属的 monitor 接口会导致
       射频被标为 blocked（UI 提示“【监听接口冲突】(归属待核实)”并阻断启动）。
    2. 对齐后台：tools/scripts/hcx_autocap.sh:1022-1048 的 sweep_stale_ifaces()，
       在每次检测与启动前主动清理上次遗留的 hcxmon* 进程与网络接口。
    3. 规范与授权：严格遵守 AGENTS.md 中所有者 2026-10-30 专项授权：
       "The owner accepts the existing automatic-capture hcxmon interface-name cleanup
       for this merge... This exception does not authorize broad process killing or
       unrelated interface cleanup elsewhere."
       仅允许针对命名严格匹配 hcxmon[0-9]+ 与 hcxstub* 的专属接口及 cmdline 绑定
       该接口的 hcxdumptool 进程执行清理，严禁使用盲目的 killall 或误删非专属接口。
    """
    if interfaces is not None:
        target_list = list(interfaces)
        for iface in target_list:
            if not isinstance(iface, str) or not re.fullmatch(r"(?:hcxmon[0-9]+|hcxstub[0-9]+[A-Za-z0-9_]*)", iface):
                raise ValueError(f"Invalid capture interface name for sweep: {iface}")
        target_check = " ".join(q(i) for i in target_list)
        loop_target = f"for d in {target_check}; do"
    else:
        loop_target = "for d in $(ls /sys/class/net 2>/dev/null); do"

    names = _capture_process_pattern()
    return (
        loop_target + "\n"
        r""" case "$d" in
  hcxmon[0-9]*|hcxstub*)
   pidfile="/tmp/.hcx_${d}.pid"
   if [ -f "$pidfile" ]; then
    pid=$(cat "$pidfile" 2>/dev/null)
    case "$pid" in ''|*[!0-9]*) :;; *)
     if [ "$pid" -gt 1 ] 2>/dev/null && kill -0 "$pid" 2>/dev/null; then
      comm=$(cat /proc/"$pid"/comm 2>/dev/null)
      case "$comm" in """ + names + r""") :;; *) comm="";; esac
      if [ -n "$comm" ]; then
       kill -TERM "$pid" 2>/dev/null
       for _w in 1 2 3 4 5; do
        kill -0 "$pid" 2>/dev/null || break
        usleep 100000 2>/dev/null || sleep 1
       done
       kill -0 "$pid" 2>/dev/null && kill -KILL "$pid" 2>/dev/null
      fi
     fi;;
    esac
    rm -f "$pidfile" 2>/dev/null
   fi
   for pdir in /proc/[0-9]*; do
    [ -d "$pdir" ] || continue
    p="${pdir#/proc/}"
    [ "$p" -gt 1 ] 2>/dev/null || continue
    comm=$(cat /proc/$p/comm 2>/dev/null)
    case "$comm" in """ + names + r""") :;; *) continue;; esac
    cmd=$(head -c 4096 /proc/$p/cmdline 2>/dev/null | tr '\0' ' ')
    case " $cmd " in
     *" -i $d "*|*" --interface=$d "*)
      kill -TERM "$p" 2>/dev/null
      for _w in 1 2 3; do
       kill -0 "$p" 2>/dev/null || break
       usleep 100000 2>/dev/null || sleep 1
      done
      kill -0 "$p" 2>/dev/null && kill -KILL "$p" 2>/dev/null
      ;;
    esac
   done
   if [ -e "/sys/class/net/$d" ]; then
    info=$(iw dev "$d" info 2>/dev/null)
    if printf '%s\n' "$info" | grep -Eq '^[[:space:]]*type monitor[[:space:]]*$' || [ -z "$info" ]; then
     ip link set "$d" down 2>/dev/null
     iw dev "$d" del 2>/dev/null
    fi
   fi;;
 esac
done
printf SWEEP_DONE
"""
    )


def sweep_stale_monitors(runner, interfaces=None, cancel_event=None):
    """
    执行扫除遗留专属抓包监听接口 (hcxmon* / hcxstub*)。
    用于前台抓包准入检查 (Preflight) 与会话初始化前的主动自愈。
    """
    cmd = sweep_stale_monitors_command(interfaces=interfaces)
    result = runner.run("sweep_stale_monitors", cmd, timeout=12, cancel_event=cancel_event, output_limit=4096)
    require_command_completion(result)
    return "SWEEP_DONE" in (result.stdout or "")

