"""Bounded evidence from existing read-only radio selection queries."""
from collections import deque
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from functools import wraps
from threading import RLock
import re

MAX_ATTEMPTS = 2
MAX_RECORDS = 32
MAX_TEXT_BYTES = 32768
MAX_ATTEMPT_BYTES = 65536
OPERATIONS = frozenset({'list_phys', 'find_scan_radio', 'probe_takeover_needs',
    'probe_phy_channel', 'probe_phy_band', 'read_iface_types', 'check_sta_link', 'check_idle_radio'})


def bounded_text(value, limit=MAX_TEXT_BYTES):
    raw = str(value or '').encode('utf-8')
    return raw[:limit].decode('utf-8', errors='ignore'), len(raw) > limit


class RadioSelectionDiagnostics:
    """Keep two attempts without disk I/O, extra queries or retained widgets."""
    def __init__(self):
        self._lock = RLock()
        self._connection_identity = None
        self.clear()

    def clear(self):
        with self._lock:
            self._attempts = deque(maxlen=MAX_ATTEMPTS)
            self._active = None
            self._output_bytes = 0

    def bind_connection(self, identity):
        """Keep failures across reconnects to the same host key; clear on device change."""
        with self._lock:
            if identity != self._connection_identity:
                self.clear()
                self._connection_identity = identity

    @contextmanager
    def attempt(self, operation, targets, idle_only=False):
        with self._lock:
            record = {'started_at': datetime.now(timezone.utc).isoformat(),
                'operation': operation, 'idle_only': bool(idle_only),
                'targets': [{'index': i + 1, 'band': str(t.band), 'channel': int(t.channel or 0)}
                            for i, t in enumerate(targets[:256])],
                'targets_omitted': max(0, len(targets) - 256), 'status': 'running',
                'queries': [], 'checks': [], 'outcomes': [], 'records_omitted': 0}
            if len(self._attempts) == MAX_ATTEMPTS:
                latest_failure = next((r for r in reversed(self._attempts)
                                       if r['status'] in ('failed', 'rejected')), None)
                if latest_failure is self._attempts[0]:
                    self._attempts.pop()
            self._attempts.append(record)
            self._active = record
            self._output_bytes = 0
        try:
            yield self
        except Exception as exc:
            with self._lock:
                record['status'] = 'failed'
                record['error_type'] = type(exc).__name__
                record['error'] = bounded_text(exc, 2048)[0]
            raise
        finally:
            with self._lock:
                if record['status'] == 'running':
                    record['status'] = 'rejected' if any(r.get('error') for r in record['outcomes']) else 'completed'
                record['finished_at'] = datetime.now(timezone.utc).isoformat()
                self._active = None

    def wants(self, operation):
        with self._lock:
            return self._active is not None and operation in OPERATIONS

    def query(self, operation, command, result=None, error=None, elapsed_seconds=None):
        with self._lock:
            if self._active is None or operation not in OPERATIONS:
                return
            row = {'operation': operation}
            phy = re.search(r"\biw\s+phy\s+['\"]?(phy\d+)\b", command)
            if phy:
                row['phy'] = phy.group(1)
            if result is not None:
                for key in ('exit_code', 'timed_out', 'cancelled', 'elapsed_seconds'):
                    row[key] = getattr(result, key, None)
                for key in ('stdout', 'stderr'):
                    budget = max(0, min(MAX_TEXT_BYTES, MAX_ATTEMPT_BYTES - self._output_bytes))
                    text, truncated = bounded_text(getattr(result, key, ''), budget)
                    self._output_bytes += len(text.encode('utf-8'))
                    row[key] = text
                    row[key + '_truncated'] = truncated
            if error is not None:
                row['error_type'] = type(error).__name__
                row['error'] = bounded_text(error, 2048)[0]
            if elapsed_seconds is not None:
                row['elapsed_seconds'] = elapsed_seconds
            self._append('queries', row)

    def channel_check(self, phy, channel, reason, frequency=0):
        with self._lock:
            self._append('checks', {'phy': phy, 'channel': int(channel),
                'frequency_mhz': frequency, 'reason': reason})

    def outcome(self, row):
        with self._lock:
            self._append('outcomes', row)

    def _append(self, key, row):
        if self._active is None:
            return
        if len(self._active[key]) < MAX_RECORDS:
            self._active[key].append(row)
        else:
            self._active['records_omitted'] += 1

    def snapshot(self):
        with self._lock:
            return {'schema_version': 1, 'attempts': deepcopy(list(self._attempts))}


def _journal(backend):
    value = getattr(backend.command_runner, 'radio_diagnostics', None)
    return value if isinstance(value, RadioSelectionDiagnostics) else None


def record_scan_selection(method):
    @wraps(method)
    def recorded(self, band, cancel_event, target_channel=0, idle_radio_only=False, message_callback=None):
        journal = _journal(self)
        if journal is None:
            return method(self, band, cancel_event, target_channel, idle_radio_only, message_callback)
        from types import SimpleNamespace
        with journal.attempt('radio_selection', (SimpleNamespace(band=band, channel=target_channel),), idle_radio_only):
            phy = method(self, band, cancel_event, target_channel, idle_radio_only, message_callback)
            journal.outcome({'index': 1, 'band': band, 'channel': target_channel, 'phy': phy})
            return phy
    return recorded


def record_takeover_probe(method):
    @wraps(method)
    def recorded(self, targets, settings, cancel_event=None):
        journal = _journal(self)
        if journal is None:
            return method(self, targets, settings, cancel_event)
        targets = tuple(targets or ())
        with journal.attempt('handshake_preflight', targets, (settings or {}).get('idle_radio_only', False)):
            entries = method(self, targets, settings, cancel_event)
            for i, entry in enumerate(entries):
                journal.outcome({'index': i + 1, 'band': entry.band, 'channel': entry.channel,
                    'phy': entry.phy, 'phy_channel': entry.phy_channel,
                    'need_takeover': entry.need_takeover, 'error': bounded_text(entry.error, 2048)[0]})
            return entries
    return recorded



def sanitized_radio_evidence(data, masker):
    """Export only schema fields; never accept an arbitrary nested JSON payload."""
    from modules.log_services import sanitized_text
    if not isinstance(data, dict) or data.get('schema_version') != 1:
        raise ValueError('unsupported radio diagnostic evidence')
    def fields(source, strings=(), numbers=()):
        if not isinstance(source, dict):
            raise ValueError('invalid radio evidence record')
        result = {}
        for key in strings:
            value = source.get(key)
            if isinstance(value, str):
                limit = MAX_TEXT_BYTES if key in ('stdout', 'stderr') else 2048
                value = bounded_text(value, limit)[0]
                result[key] = sanitized_text(value, masker)
        for key in numbers:
            value = source.get(key)
            if value is None or isinstance(value, (bool, int, float)):
                result[key] = value
        return result
    def rows(source, key, limit, strings, numbers):
        values = source.get(key, [])
        if not isinstance(values, list) or len(values) > limit:
            raise ValueError('radio evidence list exceeds bounds: ' + key)
        return [fields(v, strings, numbers) for v in values]
    attempts = data.get('attempts', [])
    if not isinstance(attempts, list) or len(attempts) > MAX_ATTEMPTS:
        raise ValueError('radio evidence attempt count exceeds bounds')
    result = []
    for source in attempts:
        row = fields(source, ('started_at', 'finished_at', 'operation', 'status', 'error_type', 'error'),
                     ('idle_only', 'targets_omitted', 'records_omitted'))
        row['targets'] = rows(source, 'targets', 256, ('band',), ('index', 'channel'))
        row['queries'] = rows(source, 'queries', MAX_RECORDS,
            ('operation', 'phy', 'stdout', 'stderr', 'error_type', 'error'),
            ('exit_code', 'timed_out', 'cancelled', 'elapsed_seconds', 'stdout_truncated', 'stderr_truncated'))
        row['checks'] = rows(source, 'checks', MAX_RECORDS, ('phy', 'reason'), ('channel', 'frequency_mhz'))
        row['outcomes'] = rows(source, 'outcomes', MAX_RECORDS, ('band', 'phy', 'error'),
            ('index', 'channel', 'phy_channel', 'need_takeover'))
        result.append(row)
    return {'schema_version': 1, 'attempts': result}
