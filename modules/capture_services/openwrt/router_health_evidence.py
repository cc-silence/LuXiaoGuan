"""Read-only boot/resource evidence; no timers, recovery actions or router writes."""
from collections import deque
from copy import deepcopy
from datetime import datetime, timezone
from threading import RLock
import re

MAX_SAMPLES = 16
MAX_INCIDENTS = 8
BEGIN = "__RM_HEALTH_BEGIN__"
END = "__RM_HEALTH_END__"
SAMPLE_COMMAND = r"""
printf '__RM_HEALTH_BEGIN__\n'
if [ -r /proc/sys/kernel/random/boot_id ]; then
 IFS= read -r boot < /proc/sys/kernel/random/boot_id
 printf 'BOOT %s\n' "$boot"
fi
awk '{printf "UPTIME %.0f\n", $1}' /proc/uptime 2>/dev/null
awk '/^MemTotal:/ {print "TOTAL " $2} /^MemFree:/ {print "FREE " $2} /^MemAvailable:/ {print "AVAILABLE " $2}' /proc/meminfo 2>/dev/null
printf '__RM_HEALTH_END__\n'
"""
# No version-specific logread switches, dmesg clearing, mounts or file writes.
_PATTERN = 'out of memory|oom-kill|killed process|kernel panic|watchdog|soft lockup|hard lockup|call trace|kernel bug|mt76|mt7603|mt76x2|firmware.*(crash|fail)|reset|reboot'
LOG_COMMAND = r"""
printf '__RM_FAULT_BEGIN__\n'
if command -v logread >/dev/null 2>&1; then
 printf 'SOURCE logread\n'
 logread 2>/dev/null | grep -i -E 'PATTERN' | tail -n 20 | cut -c 1-512 | sed 's/^/LOG /'
else printf 'MISSING logread\n'; fi
if command -v dmesg >/dev/null 2>&1; then
 printf 'SOURCE dmesg\n'
 dmesg 2>/dev/null | grep -i -E 'PATTERN' | tail -n 20 | cut -c 1-512 | sed 's/^/LOG /'
else printf 'MISSING dmesg\n'; fi
if [ -d /sys/fs/pstore ] && [ -r /sys/fs/pstore ]; then
 printf 'SOURCE pstore\n'
 count=0
 for f in /sys/fs/pstore/dmesg-ramoops* /sys/fs/pstore/console-ramoops*; do
  [ -f "$f" ] && [ -r "$f" ] || continue
  [ "$count" -lt 2 ] || break
  count=$((count + 1))
  head -c 4096 "$f" 2>/dev/null | grep -i -E 'PATTERN' | tail -n 20 | cut -c 1-512 | sed 's/^/LOG /'
 done
else printf 'MISSING pstore\n'; fi
printf '__RM_FAULT_END__\n'
""".replace('PATTERN', _PATTERN)


def stamp():
    return datetime.now(timezone.utc).isoformat()


def parse_sample(text):
    lines = text.strip().splitlines()
    if len(lines) > 8 or not lines or lines[0] != BEGIN or lines[-1] != END:
        raise ValueError("Invalid health evidence")
    result = {}
    fields = {'UPTIME': 'uptime_seconds', 'TOTAL': 'mem_total_kb',
              'FREE': 'mem_free_kb', 'AVAILABLE': 'mem_available_kb'}
    for line in lines[1:-1]:
        key, _, value = line.partition(' ')
        name = fields.get(key, 'boot_id' if key == 'BOOT' else '')
        if not name or name in result:
            raise ValueError("Invalid health field")
        if key == 'BOOT':
            if not re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', value):
                raise ValueError("Invalid boot identity")
            result[name] = value.lower()
        elif value.isascii() and value.isdigit() and len(value) <= 13:
            result[name] = int(value)
        else:
            raise ValueError("Invalid health number")
    return result


def parse_faults(text):
    from modules.log_history import HistoryMasker
    from .capture_transport_evidence import _sanitize_line
    lines = text.strip().splitlines()
    if len(lines) > 90 or not lines or lines[0] != '__RM_FAULT_BEGIN__' or lines[-1] != '__RM_FAULT_END__':
        raise ValueError("Invalid fault evidence")
    result = {'sources_present': [], 'sources_missing': [], 'lines': []}
    masker = HistoryMasker()
    for line in lines[1:-1]:
        key, _, value = line.partition(' ')
        if key in ('SOURCE', 'MISSING') and value in ('logread', 'dmesg', 'pstore'):
            result['sources_present' if key == 'SOURCE' else 'sources_missing'].append(value)
        elif key == 'LOG' and len(result['lines']) < 80:
            result['lines'].append(_sanitize_line(value, masker))
        else:
            raise ValueError("Invalid fault field")
    return result


def sanitize_health(data, masker):
    from modules.diagnostic_redaction import valid_timestamp
    from .capture_transport_evidence import _sanitize_line
    if not isinstance(data, dict):
        raise ValueError("Invalid health journal")
    result = {'schema': 1, 'samples': [], 'incidents': []}
    for name, limit in (('samples', MAX_SAMPLES), ('incidents', MAX_INCIDENTS)):
        rows = data.get(name, [])
        if not isinstance(rows, list) or len(rows) > limit:
            raise ValueError("Health journal limit exceeded")
        for row in rows:
            clean = {}
            for key in ('timestamp', 'last_sample_at'):
                if valid_timestamp(row.get(key)): clean[key] = row[key]
            for key in ('uptime_seconds', 'mem_total_kb', 'mem_free_kb', 'mem_available_kb'):
                value = row.get(key)
                if type(value) is int and 0 <= value <= 10**13: clean[key] = value
            boot = row.get('boot_id', '')
            if isinstance(boot, str) and re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', boot):
                clean['boot_id'] = boot
            for key, allowed in (('phase', {'connection', 'guard', 'capture_start', 'capture_check', 'capture_failure'}),
                                 ('kind', {'connection_lost', 'reconnected', 'collection', 'capture_failure'}),
                                 ('boot_comparison', {'changed', 'same', 'uptime_decreased', 'unknown'}),
                                 ('collection_status', {'completed', 'failed', 'timeout', 'cancelled'})):
                if row.get(key) in allowed: clean[key] = row[key]
            for key in ('sources_present', 'sources_missing'):
                if isinstance(row.get(key), list):
                    clean[key] = [s for s in row[key][:3] if s in ('logread', 'dmesg', 'pstore')]
            if isinstance(row.get('lines'), list):
                clean['lines'] = [_sanitize_line(s, masker) for s in row['lines'][:80] if isinstance(s, str)]
            result[name].append(clean)
    return result


class RouterHealthEvidence:
    def __init__(self):
        self._lock = RLock()
        self._samples = deque(maxlen=MAX_SAMPLES)
        self._incidents = deque(maxlen=MAX_INCIDENTS)
        self._pending = False

    @property
    def pending(self):
        with self._lock: return self._pending

    def sample(self, data, phase):
        with self._lock:
            prior = self._samples[-1] if self._samples else {}
            row = dict(data, phase=phase, timestamp=stamp())
            if phase == 'connection' and self._pending:
                comparison = 'unknown'
                if prior.get('boot_id') and row.get('boot_id'):
                    comparison = 'same' if prior['boot_id'] == row['boot_id'] else 'changed'
                elif 'uptime_seconds' in prior and 'uptime_seconds' in row and row['uptime_seconds'] < prior['uptime_seconds']:
                    comparison = 'uptime_decreased'
                self._incidents.append(dict(row, kind='reconnected', boot_comparison=comparison))
                self._pending = False
            self._samples.append(row)

    def disconnected(self):
        with self._lock:
            if not self._pending:
                row = {'timestamp': stamp(), 'kind': 'connection_lost'}
                if self._samples: row['last_sample_at'] = self._samples[-1]['timestamp']
                self._incidents.append(row)
            self._pending = True

    def collection(self, status, data=None):
        with self._lock:
            self._incidents.append(dict(data or {}, timestamp=stamp(), kind='collection', collection_status=status))

    def snapshot(self):
        with self._lock:
            return deepcopy({'schema': 1, 'samples': list(self._samples), 'incidents': list(self._incidents)})


def accept_guard(runner, text, epoch, connection=None):
    """Return the original resource output even if optional health parsing fails."""
    resource, separator, extra = text.partition(BEGIN)
    journal = getattr(runner, 'operation_diagnostics', None)
    current = getattr(getattr(runner, "ssh_client", None), "_client", None)
    if connection is not None and current is not connection:
        return resource
    if separator and journal is not None:
        try: journal.health_sample(parse_sample(BEGIN + extra), 'guard', epoch)
        except (ValueError, AttributeError): pass
    return resource


def collect_connection(runner, cancel=None):
    """Run in the login worker, after authenticated host-key identity binding."""
    from .operation_diagnostics import OperationDiagnostics
    journal = getattr(runner, 'operation_diagnostics', None)
    if isinstance(journal, OperationDiagnostics):
        _collect(runner, 'connection', cancel, faults=journal.health.pending)


CAPTURE_PROGRAMS = frozenset({'airodump-ng', 'hcxdumptool'})


def _collect(runner, phase, cancel=None, *, faults=False):
    """One optional snapshot at task start/failure, never a new polling loop."""
    from .operation_diagnostics import OperationDiagnostics
    journal = getattr(runner, 'operation_diagnostics', None)
    if not isinstance(journal, OperationDiagnostics):
        return
    epoch = journal.epoch
    ssh = getattr(runner, 'ssh_client', None)
    connection = getattr(ssh, '_client', None)
    if getattr(ssh, 'connected', True) is False:
        journal.health_disconnected()
        journal.health_collection('failed', {'phase': phase}, epoch)
        return
    try:
        result = runner.run('router_health' if phase == 'connection' else 'capture_health',
                            SAMPLE_COMMAND + (LOG_COMMAND if faults else ''),
                            timeout=4, cancel_event=cancel, output_limit=49152)
        if connection is not None and getattr(ssh, '_client', None) is not connection:
            return
        if result.cancelled: status = 'cancelled'
        elif result.timed_out: status = 'timeout'
        elif result.exit_code != 0: status = 'failed'
        else:
            text, sep, logs = result.stdout.partition('__RM_FAULT_BEGIN__')
            journal.health_sample(parse_sample(text), phase, epoch)
            if faults:
                if not sep: raise ValueError('Missing fault evidence')
                journal.health_collection('completed', dict(parse_faults(sep + logs), phase=phase), epoch)
            return
    except Exception:
        status = 'failed'
    journal.health_collection(status, {'phase': phase}, epoch)


def collect_capture(runner, phase, cancel=None, *, failed=False):
    _collect(runner, phase, cancel, faults=failed)


def run_process_probe(runner, program, command, cancel=None):
    """Piggyback health on existing read-only PID checks, preserving exit codes."""
    from dataclasses import replace
    from .operation_diagnostics import OperationDiagnostics
    journal = getattr(runner, 'operation_diagnostics', None)
    if program not in CAPTURE_PROGRAMS or not isinstance(journal, OperationDiagnostics):
        return runner.run('poll_' + program, command, timeout=5, cancel_event=cancel)
    epoch = journal.epoch
    connection = getattr(getattr(runner, 'ssh_client', None), '_client', None)
    wrapped = '(\n' + command + '\n)\nrm_health_status=$?\nprintf "\\n"\n' + SAMPLE_COMMAND + '\nexit "$rm_health_status"'
    result = runner.run('poll_' + program, wrapped, timeout=5, cancel_event=cancel, output_limit=4096)
    text = result.stdout
    prefix, sep, extra = text.partition(BEGIN)
    if sep:
        current = getattr(getattr(runner, 'ssh_client', None), '_client', None)
        if connection is None or current is connection:
            try: journal.health_sample(parse_sample(BEGIN + extra), 'capture_check', epoch)
            except ValueError: pass
        result = replace(result, stdout=prefix.removesuffix('\n'))
    return result
