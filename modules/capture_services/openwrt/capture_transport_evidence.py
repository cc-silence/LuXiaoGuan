"""Capture-channel transport evidence: bounded, redacted, failure only.

Paramiko reports a server-side refusal of an exec request as
``SSHException("Channel closed.")`` because it closes the local channel on
SSH_MSG_CHANNEL_FAILURE (paramiko.channel.Channel._request_failed). Dropbear
answers CHANNEL_FAILURE when it cannot spawn the command (fork failure) or
when the command exceeds MAX_CMD_LEN (dropbear svr-chansession.c
sessioncommand/ptycommand). The same exception also covers a real channel
close. This scene keeps the router-side facts needed to tell those apart on
the next occurrence; it never changes capture behaviour.
"""
from datetime import datetime, timezone
import re

MAX_SCENES = 8
MAX_LOG_LINES = 20
MAX_LOG_LINE = 512
STAGES = frozenset({"channel_open", "pty_request", "exec_request", "result", "exit_status"})
STATUSES = frozenset({"completed", "failed", "timeout", "cancelled", "disconnected"})
_ADDRESS = re.compile(r'(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?::\d{1,5})?(?![\w.])')
_MAC = re.compile(r'(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}(?![0-9A-Fa-f:])')

# logread without options exists on every supported OpenWrt release (ubox log/logread.c);
# filtering and tailing happen on the router so only a few lines cross the SSH channel.
TRANSPORT_SCENE_COMMAND = r"""
printf '__RM_TRANSPORT_BEGIN__\n'
up=$(cut -d. -f1 /proc/uptime 2>/dev/null)
case "$up" in ''|*[!0-9]*) :;; *) printf 'UPTIME %s\n' "$up";; esac
mf=$(awk '/^MemFree:/{print $2;exit}' /proc/meminfo 2>/dev/null)
case "$mf" in ''|*[!0-9]*) :;; *) printf 'MEMFREE %s\n' "$mf";; esac
ma=$(awk '/^MemAvailable:/{print $2;exit}' /proc/meminfo 2>/dev/null)
case "$ma" in ''|*[!0-9]*) :;; *) printf 'MEMAVAIL %s\n' "$ma";; esac
if command -v logread >/dev/null 2>&1; then
 printf 'LOGREAD 1\n'
 logread 2>/dev/null | grep -i -E 'dropbear|sshd' | tail -n 20 | sed 's/[0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*/[address omitted]/g; s/[0-9a-fA-F][0-9a-fA-F]:[0-9a-fA-F][0-9a-fA-F]:[0-9a-fA-F][0-9a-fA-F]:[0-9a-fA-F][0-9a-fA-F]:[0-9a-fA-F][0-9a-fA-F]:[0-9a-fA-F][0-9a-fA-F]/[mac omitted]/g' | while IFS= read -r line; do printf 'LOG %s\n' "$line"; done
else
 printf 'LOGREAD 0\n'
fi
printf '__RM_TRANSPORT_END__\n'
"""


def _integer(value, maximum):
    return type(value) is int and 0 <= value <= maximum


def parse_transport_scene(text):
    """Parse the bounded command output; raise ValueError on malformed evidence."""
    if not isinstance(text, str):
        raise ValueError("invalid transport evidence")
    lines = text.strip().splitlines()
    if len(lines) < 2 or len(lines) > 64:
        raise ValueError("invalid transport evidence")
    if lines[0] != "__RM_TRANSPORT_BEGIN__" or lines[-1] != "__RM_TRANSPORT_END__":
        raise ValueError("invalid transport evidence")
    data = {"schema": 1, "logread": None, "service_log": []}
    for line in lines[1:-1]:
        key, _, value = line.partition(" ")
        if key == "UPTIME":
            data["uptime_seconds"] = int(value) if value.isdigit() else None
        elif key == "MEMFREE":
            data["mem_free_kb"] = int(value) if value.isdigit() else None
        elif key == "MEMAVAIL":
            data["mem_available_kb"] = int(value) if value.isdigit() else None
        elif key == "LOGREAD":
            if value not in ("0", "1"):
                raise ValueError("invalid transport evidence")
            data["logread"] = value == "1"
        elif key == "LOG":
            if len(data["service_log"]) >= MAX_LOG_LINES:
                raise ValueError("invalid transport evidence")
            data["service_log"].append(value[:MAX_LOG_LINE])
        else:
            raise ValueError("invalid transport evidence")
    return data


def sanitize_transport_failure(row, masker):
    """Keep only allowlisted transport facts; mask anything free-form."""
    from modules.log_services import sanitized_text
    from modules.diagnostic_redaction import valid_timestamp
    if not isinstance(row, dict):
        raise ValueError("invalid transport failure")
    radio = row.get("radio", "")
    result = {"schema": 1,
              "radio": radio if isinstance(radio, str) and re.fullmatch(r"radio[0-9]{1,3}", radio) else "other",
              "collection_status": row.get("collection_status")
              if row.get("collection_status") in STATUSES else "failed"}
    stage = row.get("stage")
    result["stage"] = stage if stage in STAGES else "unknown"
    if valid_timestamp(row.get("timestamp")):
        result["timestamp"] = row["timestamp"]
    if row.get("logread") in (True, False):
        result["logread"] = row["logread"]
    for key, maximum in (("uptime_seconds", 2 ** 32), ("mem_free_kb", 2 ** 40),
                         ("mem_available_kb", 2 ** 40)):
        if _integer(row.get(key), maximum):
            result[key] = row[key]
    values = row.get("service_log", [])
    result["service_log"] = [_sanitize_line(line, masker)
                             for line in values[:MAX_LOG_LINES]
                             if isinstance(line, str)] if isinstance(values, list) else []
    return result


def _sanitize_line(line, masker):
    """Address, MAC, secret and identity masking before the line is truncated."""
    from modules.log_services import sanitized_text
    text = _ADDRESS.sub("[address omitted]", line)
    text = _MAC.sub("[mac omitted]", text)
    text = sanitized_text(text, masker)
    text = masker.text(text) if hasattr(masker, "text") else text
    return text[:MAX_LOG_LINE]


def collect_transport_failure(runner, radio, stage, cancel=None):
    """Record one failure-only scene. Never replaces the original exception."""
    from .operation_diagnostics import OperationDiagnostics
    journal = getattr(runner, "operation_diagnostics", None)
    if not isinstance(journal, OperationDiagnostics):
        return
    epoch = journal.epoch
    data = {"schema": 1, "radio": radio, "stage": stage,
            "timestamp": datetime.now(timezone.utc).isoformat()}
    if cancel is not None and cancel.is_set():
        data["collection_status"] = "cancelled"
    elif getattr(getattr(runner, "ssh_client", None), "connected", True) is False:
        data["collection_status"] = "disconnected"
    else:
        try:
            result = runner.run("capture_transport_scene", TRANSPORT_SCENE_COMMAND,
                                timeout=4, cancel_event=cancel, output_limit=16384)
            if result.cancelled:
                data["collection_status"] = "cancelled"
            elif result.timed_out:
                data["collection_status"] = "timeout"
            elif result.exit_code != 0:
                data["collection_status"] = "failed"
            else:
                data["collection_status"] = "completed"
                data.update(parse_transport_scene(result.stdout))
        except Exception:
            data["collection_status"] = "failed"
    try:
        from modules.log_history import HistoryMasker
        journal.transport_failure(sanitize_transport_failure(data, HistoryMasker()), epoch)
    except Exception:
        return
