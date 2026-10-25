"""Bounded, credential-free update evidence and shared background ZIP export."""
from collections import deque
from contextvars import ContextVar
from uuid import uuid4
from datetime import datetime
import json
import hashlib
import logging
import os
import re
import threading
import time
from urllib.parse import urlsplit

MARKER = 'UPDATE_DIAGNOSTIC '
_lock = threading.RLock()
_events = deque(maxlen=256)
_omitted = 0
_operation = ContextVar("update_diagnostic_operation", default=None)
_FIELDS = {'stage', 'route', 'host', 'status', 'error', 'error_type', 'chain',
           'elapsed_ms', 'received', 'expected', 'blob', 'version', 'network_mode',
           'routes', 'result', 'manual', 'testing', 'qt_error', 'ssl_errors',
           'revision', 'free_bytes', 'required_bytes', 'connect_timeout_s', 'read_timeout_s',
           'deadline_s', 'cf_ray', 'returncode', 'safe_to_restart', 'cleanup_pending', 'route_id', 'module'}


def exception_chain(exc):
    from .failure_codes import failure_code
    pending, result, seen = [exc], [], set()
    while pending and len(result) < 8:
        item = pending.pop(0)
        if not isinstance(item, BaseException) or id(item) in seen:
            continue
        seen.add(id(item))
        record = type(item).__name__
        code = failure_code(item)
        if code:record += " code=" + code
        for key in ('errno', 'winerror', 'verify_code', 'reason', 'library'):
            value = getattr(item, key, None)
            if isinstance(value, int) or isinstance(value, str) and re.fullmatch(r'[A-Z0-9_]{1,80}', value):
                record += f' {key}={value}'
        result.append(record)
        pending.extend([item.__cause__, item.__context__, getattr(item, 'reason', None)])
        pending.extend(value for value in item.args if isinstance(value, BaseException))
    return ' -> '.join(result)


def startup_evidence(output):
    text = output[-16384:].decode('utf-8', errors='replace') if isinstance(output, bytes) else ''
    result = {'error': 'startup_process_exit'}
    errors = re.findall(r'(?m)^([A-Za-z_][A-Za-z0-9_]*(?:Error|Exception)):', text)
    if errors:result['error_type'] = errors[-1]
    missing = re.search(r"No module named ['\"]([A-Za-z_][A-Za-z0-9_.]{0,120})['\"]", text)
    if missing:result.update(error='startup_module_missing', module=missing[1])
    if 'Could not load the Qt platform plugin' in text or 'Could not find the Qt platform plugin' in text:
        result['error'] = 'startup_qt_plugin_failed'
    return result


def endpoint_host(url):
    try:
        return (urlsplit(url).hostname or '')[:200]
    except ValueError:
        return 'invalid_endpoint'


def begin_operation(kind, version='', revision=''):
    _operation.set({'operation_id': uuid4().hex, 'operation': kind,
                    'operation_version': str(version)[:64], 'revision': str(revision)[:64]})


def route_identity(proxy_url):
    if not proxy_url:return 'direct'
    try:
        parsed=urlsplit(proxy_url)
        identity=f'{parsed.scheme.lower()}://{parsed.hostname}:{parsed.port}'
        return hashlib.sha256(identity.encode('utf-8')).hexdigest()[:16]
    except ValueError:
        return 'invalid_proxy'


def response_trace(value):
    try:
        text = value if isinstance(value, str) else bytes(value).decode('ascii')
    except (ValueError, UnicodeError, TypeError):
        return ''
    return text if re.fullmatch(r'[0-9a-fA-F]{16,32}-[A-Za-z]{2,8}', text) else ''


def _encoded_context(rows):
    rows = list(rows)
    omitted = 0
    while True:
        payload = json.dumps({'schema': 1, 'events': rows, 'context_records_omitted': omitted}, ensure_ascii=True)
        if len(payload.encode('utf-8')) <= 48 * 1024 or not rows:
            return MARKER + payload
        rows.pop(0)
        omitted += 1


def record(event, **fields):
    global _omitted
    # Arbitrary exception messages, headers, proxy URLs and local paths never enter this store.
    row = {'time': datetime.now().astimezone().isoformat(timespec='seconds'),
           'event': event, 'process_id': os.getpid(), **(_operation.get() or {})}
    for key, value in fields.items():
        if key not in _FIELDS:
            continue
        if value is None or isinstance(value, (bool, int, float)):
            row[key] = value
        elif isinstance(value, str):
            row[key] = value[:600]
    with _lock:
        if len(_events) == _events.maxlen:
            _omitted += 1
        _events.append(row)
        context = tuple(_events)[-32:] if event.endswith('failed') else None
    if context:
        logging.getLogger('routermanager.update').error(_encoded_context(context))


def snapshot():
    with _lock:
        rows = tuple((json.dumps(row, ensure_ascii=True), 'info') for row in _events)
        if _omitted:
            rows += ((json.dumps({'session_records_omitted': _omitted}), 'warning'),)
        return rows


def persist_operation(root):
    """The independent installer has no application logging hooks or Qt runtime."""
    from modules.error_logs import ErrorLogStore
    try:
        context = _operation.get() or {}
        with _lock:
            rows = tuple(row for row in _events if row.get('operation_id') == context.get('operation_id'))
        store = ErrorLogStore(root / 'RouterManagerData' / 'error-logs', context.get('operation_version', ''))
        store.append(_encoded_context(rows))
        return True
    except OSError:
        # Diagnostics cannot change a completed installation or its rollback result.
        return False
