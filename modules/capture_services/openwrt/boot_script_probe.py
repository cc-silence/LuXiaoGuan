"""Read-only identity check for supported background script names."""
BOOT_SCRIPT_RUNNING_PROBE = r'''
R_RUNNING=0
for RM_SCRIPT in /root/hcx_autocap.sh /root/autocap.sh; do
 RM_BASE=${RM_SCRIPT##*/}
 RM_BASE=${RM_BASE%.*}
 RM_LOCK="/tmp/.${RM_BASE}.lock"
 if [ -f "$RM_LOCK" ]; then
  PID=
  IFS= read -r PID < "$RM_LOCK"
  case "$PID" in
   ''|*[!0-9]*) ;;
   *)
    if kill -0 "$PID" 2>/dev/null &&
       head -c 8192 "/proc/$PID/cmdline" 2>/dev/null | tr '\000' '\n' |
       grep -F -x -e "$RM_SCRIPT" >/dev/null; then
     R_RUNNING=1
     break
    fi
    ;;
  esac
 fi
done
'''
