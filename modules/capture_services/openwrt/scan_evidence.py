"""Optional read-only scan scene on an isolated, bounded diagnostic channel."""
import re
import time
from threading import Event
from .command_builder import quote_argument
from modules.capture_services.capture_logging import LOGGER

BEGIN = '__RM_SCAN_CONTEXT_BEGIN__'
END = '__RM_SCAN_CONTEXT_END__'
MAX_OUTPUT = 32768
COMMAND = r"""
printf '__RM_SCAN_CONTEXT_BEGIN__\n'
printf 'KERNEL '; uname -r
printf 'IW '; iw --version 2>&1 | head -n 1
airodump-ng --help 2>&1 | head -n 3
iface=@INTERFACE@
phy_path=/sys/class/net/$iface/phy80211
phy=$(basename "$(readlink "$phy_path" 2>/dev/null)")
printf 'PHY %s\n' "$phy"
printf 'DRIVER '; readlink "$phy_path/device/driver" 2>/dev/null
for name in version srcversion; do
 f="$phy_path/device/driver/module/$name"
 if [ -r "$f" ]; then printf 'MODULE %s ' "$name"; head -c 256 "$f"; printf '\n'; fi
done
iw dev "$iface" info 2>/dev/null | sed '/ssid /d; /addr /d' | head -n 20
case "$phy" in phy[0-9]*)
 iw phy "$phy" info 2>/dev/null | grep 'MHz.*\[' | head -n 64;;
esac
if command -v dmesg >/dev/null 2>&1; then
 dmesg 2>/dev/null | grep -i -E 'firmware|mt76|mt79|watchdog' | tail -n 24 | cut -c 1-512
fi
printf '__RM_SCAN_CONTEXT_END__\n'
"""


def collect_scan_context(runner, interface, band, duration, refresh, cancel=None, *, command=""):
    """Failure/cancellation cannot invalidate the login or replace a scan result."""
    from .operation_diagnostics import OperationDiagnostics
    journal = getattr(runner, 'operation_diagnostics', None)
    if not isinstance(journal, OperationDiagnostics):
        return
    ssh = getattr(runner, 'ssh_client', None)
    connection = getattr(ssh, '_client', None)
    epoch = journal.epoch
    row = dict(interface=interface, band=band, duration_seconds=duration,
               refresh_seconds=refresh, text=command, state='failed')
    token = cancel if cancel is not None else Event()
    channel = None
    output = bytearray()
    deadline = time.monotonic() + 4
    try:
        if token.is_set():
            row['state'] = 'cancelled'
            return
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,15}', str(interface)):
            return
        if connection is None or not ssh.connected:
            return
        command = COMMAND.replace('@INTERFACE@', quote_argument(interface))
        channel = ssh.open_diagnostic_stream(connection, command, token, timeout=2)
        while not token.is_set() and time.monotonic() < deadline:
            if ssh._client is not connection:
                row['state'] = 'disconnected'
                return
            if channel.recv_ready():
                chunk = channel.recv(min(8192, MAX_OUTPUT - len(output)))
                if not chunk:
                    break
                output.extend(chunk[:MAX_OUTPUT - len(output)])
                if len(output) >= MAX_OUTPUT:
                    row['state'] = 'limit'
                    return
            elif channel.exit_status_ready() or channel.closed:
                break
            else:
                token.wait(.05)
        if token.is_set():
            row['state'] = 'cancelled'
        elif time.monotonic() >= deadline:
            row.update(state='deadline', timed_out=True)
        else:
            text = output.decode('utf-8', errors='replace')
            if BEGIN in text and END in text:
                row['state'] = 'ended'
    except Exception as exc:
        row['stderr'] = type(exc).__name__
    finally:
        if channel is not None:
            try:
                channel.close()
            except Exception:
                row['state'] = 'failed'
        row['stdout'] = output.decode('utf-8', errors='replace')
        if connection is getattr(ssh, '_client', None):
            try:
                journal.capture_evidence('scan_contexts', row, epoch)
            except Exception as exc:
                LOGGER.warning('Scan diagnostic record failed type=%s', type(exc).__name__)
