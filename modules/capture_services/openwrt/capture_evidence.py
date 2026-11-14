"""Bounded capture-specific error output and streamed system evidence."""
from collections import deque
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import re

TOOL_NAMES = frozenset({'auto_capture_stream', 'deauth_client', 'probe_hcx_dialect', 'probe_hcx_ver_fallback',
    'probe_bpf_flag', 'verify_monitor_state', 'start_hcxdumptool', 'start_airodump-ng',
    'poll_hcxdumptool', 'poll_airodump-ng'})
MAX_TOOLS = 16
MAX_LINES = 128
MAX_STATES = 16
MAX_SCAN_CONTEXTS = 8
STATES = frozenset({'starting', 'running', 'cancelled', 'ended', 'failed', 'unsupported', 'deadline', 'limit', 'disconnected'})


def clean_text(value, masker, limit):
    from .capture_transport_evidence import _sanitize_line
    # Keep head and tail only after redaction. Never retain private-key blocks.
    value = re.sub(r'-----BEGIN [^\r\n]*PRIVATE KEY-----.*?(?:-----END [^\r\n]*PRIVATE KEY-----|\Z)',
                   '[private key omitted]', str(value or ''), flags=re.S)
    value = '\n'.join(_sanitize_line(line, masker) for line in value.splitlines())
    if len(value) > limit:
        value = value[:limit//2] + '\n[output truncated]\n' + value[-limit//2:]
    return value


def sanitize_capture_evidence(data, masker):
    from modules.diagnostic_redaction import valid_timestamp
    if not isinstance(data, dict): raise ValueError('Invalid capture evidence')
    result = {'schema': 1}
    for name, limit in (('tool_failures', MAX_TOOLS), ('system_lines', MAX_LINES), ('stream_states', MAX_STATES), ('scan_contexts', MAX_SCAN_CONTEXTS)):
        rows = data.get(name, [])
        if not isinstance(rows, list) or len(rows) > limit: raise ValueError('Capture evidence limit exceeded')
        result[name] = []
        for row in rows:
            if not isinstance(row, dict): raise ValueError('Invalid capture row')
            clean = {}
            if valid_timestamp(row.get('timestamp')): clean['timestamp'] = row['timestamp']
            if row.get('operation') in TOOL_NAMES: clean['operation'] = row['operation']
            if row.get('state') in STATES: clean['state'] = row['state']
            if row.get('reason') in {'process_exit', 'unexpected_eof'}: clean['reason'] = row['reason']
            if isinstance(row.get('radio'), str) and re.fullmatch(r'radio[0-9]{1,3}', row['radio']): clean['radio'] = row['radio']
            for key in ('exit_code', 'timed_out', 'cancelled', 'elapsed_seconds', 'connected'):
                value = row.get(key)
                if type(value) in (int, bool) or (type(value) is float and -1e12 < value < 1e12): clean[key] = value
            if row.get('band') in ('2.4G', '5G'): clean['band'] = row['band']
            for key in ('duration_seconds', 'refresh_seconds'):
                if type(row.get(key)) is int and 1 <= row[key] <= 120: clean[key] = row[key]
            if isinstance(row.get('interface'), str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,15}', row['interface']):
                clean['interface'] = row['interface']
            for key in ('stdout', 'stderr', 'text'):
                if isinstance(row.get(key), str): clean[key] = clean_text(row[key], masker, (8192 if name == 'scan_contexts' else 2048) if key != 'text' else 512)
            result[name].append(clean)
    return result


class CaptureEvidence:
    """Protected by the owning OperationDiagnostics lock."""
    def __init__(self):
        self.tools = deque(maxlen=MAX_TOOLS)
        self.lines = deque(maxlen=MAX_LINES)
        self.states = deque(maxlen=MAX_STATES)
        self.scan_contexts = deque(maxlen=MAX_SCAN_CONTEXTS)

    def append(self, bucket, row, identity):
        from modules.log_history import HistoryMasker, current_history
        row = dict(row, timestamp=datetime.now(timezone.utc).isoformat())
        history = current_history()
        masker = history.masker if history is not None else HistoryMasker()
        clean = sanitize_capture_evidence({bucket: [row]}, masker)[bucket][0]
        {'tool_failures': self.tools, 'system_lines': self.lines, 'stream_states': self.states, 'scan_contexts': self.scan_contexts}[bucket].append(clean)
        if history is not None and isinstance(identity, str) and identity:
            device = hashlib.sha256(identity.encode()).hexdigest()
            try:
                text = json.dumps(clean, ensure_ascii=True)
                for category in ('auto-capture', '\u6293\u5305'):
                    history.append(category, text, 'diagnostic', channel='capture-evidence', device=device)
            except Exception as exc:
                history.last_error = type(exc).__name__

    def snapshot(self):
        return deepcopy({'schema': 1, 'tool_failures': list(self.tools),
                         'system_lines': list(self.lines), 'stream_states': list(self.states),
                         'scan_contexts': list(self.scan_contexts)})


def record_auto_exit(session, *, reason, exit_code=None):
    from .operation_diagnostics import OperationDiagnostics
    journal = getattr(session.runner, 'operation_diagnostics', None)
    if not isinstance(journal, OperationDiagnostics): return
    row = dict(operation='auto_capture_stream', radio=session.radio_name, reason=reason,
               connected=bool(session.ssh_client.connected))
    if type(exit_code) is int: row['exit_code'] = exit_code
    journal.capture_evidence('tool_failures', row, getattr(session, '_capture_evidence_epoch', -1))
