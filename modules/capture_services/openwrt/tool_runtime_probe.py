"""Read-only version/help probes distinguish installed files from usable binaries."""

TOOL_RUNTIME_SH = r'''
rm_tool_runtime() {
 rm_probe_path=$(command -v "$1" 2>/dev/null)
 test -n "$rm_probe_path" && test -x "$rm_probe_path" || return 1
 case "$1" in
  iw)
   rm_probe_output=$("$rm_probe_path" --version 2>&1); rm_probe_code=$?
   test "$rm_probe_code" = 0 || return 1
   case "$rm_probe_output" in *"iw version"*) return 0;; esac;;
  wpa_cli)
   rm_probe_output=$("$rm_probe_path" -v 2>&1); rm_probe_code=$?
   test "$rm_probe_code" = 0 || return 1
   case "$rm_probe_output" in *"wpa_cli v"*) return 0;; esac;;
  curl)
   rm_probe_output=$("$rm_probe_path" --version 2>&1); rm_probe_code=$?
   test "$rm_probe_code" = 0 || return 1
   case "$rm_probe_output" in *"curl "*"Protocols:"*"https"*) return 0;; esac;;
  hcxdumptool)
   rm_probe_output=$("$rm_probe_path" -h 2>&1); rm_probe_code=$?
   test "$rm_probe_code" = 0 || return 1
   case "$rm_probe_output" in *"hcxdumptool "*"(C)"*) return 0;; esac;;
  iwinfo)
   rm_probe_output=$("$rm_probe_path" -h 2>&1); rm_probe_code=$?
   test "$rm_probe_code" = 0 || test "$rm_probe_code" = 1 || return 1
   case "$rm_probe_output" in *"Usage:"*) return 0;; esac;;
 esac
 return 1
}
'''


def tool_probe_command(command):
    if command not in {"iw", "wpa_cli", "iwinfo", "curl", "hcxdumptool"}:
        raise ValueError("Unsupported tool runtime probe")
    return (TOOL_RUNTIME_SH + "\np=$(command -v " + command + " 2>/dev/null); "
            + "if test -z \"$p\" || ! test -x \"$p\"; then echo MISSING; "
            + "elif rm_tool_runtime " + command + "; then echo READY; else echo BROKEN; fi")
