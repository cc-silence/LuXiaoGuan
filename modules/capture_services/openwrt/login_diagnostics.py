"""Bounded SSH login evidence without credentials, addresses or raw errors."""
from collections import deque
from copy import deepcopy
from datetime import datetime, timezone
from functools import wraps
from math import isfinite
from threading import RLock, local
import re
import time

MAX_ATTEMPTS = 8
AUTH_METHODS = frozenset({'none', 'password', 'publickey', 'keyboard-interactive',
                          'gssapi-with-mic', 'gssapi-keyex', 'hostbased'})
ENUMS = {
    'status': {'started', 'completed', 'failed', 'timeout', 'confirmation_required', 'blocked'},
    'stage': {'connect_or_handshake', 'host_key', 'authentication', 'configuration', 'complete'},
    'reason': {'pending', 'authenticated', 'existing_connection', 'authentication_rejected',
               'authentication_timeout', 'authentication_interrupted', 'auth_method_not_allowed',
               'host_key_confirmation_required', 'host_key_changed', 'connection_timeout',
               'connection_refused', 'connection_error', 'windows_access_denied', 'name_resolution_failed',
               'key_passphrase_required', 'key_file_unavailable', 'configuration_error', 'dependency_unavailable', 'handshake_error', 'unexpected_error'},
    'auth_method': {'password', 'private_key'},
    'username_kind': {'root', 'other', 'empty'},
    'server_family': {'dropbear', 'openssh', 'paramiko', 'other', 'unknown'},
    'host_key_type': {'ssh-rsa', 'ssh-dss', 'ssh-ed25519', 'ecdsa-sha2-nistp256',
                      'ecdsa-sha2-nistp384', 'ecdsa-sha2-nistp521', 'unknown'},
}
BOOLS = {'password_present', 'password_boundary_whitespace', 'private_key_selected',
         'host_key_verified', 'transport_active', 'server_identified', 'allowed_auth_known'}
NUMBERS = {'id', 'port', 'timeout_seconds', 'elapsed_seconds', 'errno', 'winerror'}
EXCEPTIONS = {'AuthenticationException', 'BadAuthenticationType', 'PasswordRequiredException',
              'HostKeyConfirmationRequired', 'HostKeyChangedError', 'TimeoutError', 'OSError',
              'ConnectionRefusedError', 'ConnectionResetError', 'gaierror', 'EOFError',
              'SSHException', 'IncompatiblePeer', 'NoValidConnectionsError', 'ValueError',
              'FileNotFoundError', 'ImportError', 'Exception'}


def safe_row(row):
    if not isinstance(row, dict):
        raise ValueError('invalid login diagnostic record')
    out = {k: v for k, v in row.items() if k in ENUMS and isinstance(v, str) and v in ENUMS[k]}
    out.update({k: v for k, v in row.items() if k in BOOLS and type(v) is bool})
    for key in NUMBERS:
        value = row.get(key)
        if type(value) in (int, float) and isfinite(value) and abs(value) <= 10**10:
            out[key] = value
    timestamp = row.get('timestamp')
    if isinstance(timestamp, str) and re.fullmatch(r'[0-9T:+.Z-]{1,40}', timestamp):
        out['timestamp'] = timestamp
    version = row.get('server_version')
    if isinstance(version, str) and re.fullmatch(r'[0-9]+(?:\.[0-9]+){0,3}', version) and len(version) <= 32:
        out['server_version'] = version
    exception = row.get('exception_type')
    if isinstance(exception, str) and exception in EXCEPTIONS:
        out['exception_type'] = exception
    methods = row.get('allowed_auth_methods')
    if isinstance(methods, list):
        out['allowed_auth_methods'] = sorted({v for v in methods[:16] if isinstance(v, str) and v in AUTH_METHODS})
    return out


def sanitized_login_attempts(rows):
    if not isinstance(rows, list) or len(rows) > MAX_ATTEMPTS:
        raise ValueError('login diagnostic record limit exceeded')
    return [safe_row(row) for row in rows]


class LoginDiagnostics:
    def __init__(self):
        self._lock = RLock()
        self._local = local()
        self._identity = None
        self._sequence = 0
        self._rows = deque(maxlen=MAX_ATTEMPTS)

    def bind(self, identity):
        with self._lock:
            if identity != self._identity:
                self._rows.clear()
                self._identity = identity

    def begin(self, owner, password, timeout, auth_method, private_key_path):
        method = 'private_key' if auth_method == 'private_key' else 'password'
        value = str(password or '') if method == 'password' else ''
        with self._lock:
            self._sequence += 1
            row = dict(id=self._sequence, timestamp=datetime.now(timezone.utc).isoformat(),
                       status='started', stage='connect_or_handshake', reason='pending',
                       auth_method=method, port=owner._port, timeout_seconds=timeout,
                       username_kind='root' if owner._user == 'root' else 'other' if owner._user else 'empty',
                       password_present=bool(value), password_boundary_whitespace=bool(value and value != value.strip()),
                       private_key_selected=bool(private_key_path) if method == 'private_key' else False,
                       host_key_verified=False, server_family='unknown', allowed_auth_known=False)
            self._rows.append(row)
            self._local.row = row

    def update(self, **fields):
        with self._lock:
            row = getattr(self._local, 'row', None)
            if row is not None:
                row.update(safe_row(fields))

    def host_key(self, name, verified=False):
        self.update(stage='host_key', host_key_type=name if name in ENUMS['host_key_type'] else 'unknown',
                    host_key_verified=verified)
        if verified:
            self.update(stage='authentication')

    def transport(self, transport):
        if transport is None:
            return
        banner = getattr(transport, 'remote_version', '')
        if not isinstance(banner, str):
            banner = ''
        match = re.match(r'^SSH-[0-9.]+-(dropbear|OpenSSH|paramiko)[_-]([0-9]+(?:\.[0-9]+){0,3})(?:[^0-9.]|$)', banner[:256], re.I)
        self.update(server_identified=bool(banner), server_family=match[1].lower() if match else 'other' if banner else 'unknown')
        if match:
            self.update(server_version=match[2])
        active = transport.is_active()
        if type(active) is bool:
            self.update(transport_active=active)

    def finish(self, elapsed, error=None, reused=False):
        fields = dict(elapsed_seconds=elapsed, status='completed', stage='complete',
                      reason='existing_connection' if reused else 'authenticated')
        if error is not None:
            import socket
            from .ssh_client import HostKeyChangedError, HostKeyConfirmationRequired
            exc = error.__cause__ if isinstance(error, RuntimeError) and error.__cause__ is not None else error
            if isinstance(exc, ImportError):
                self.update(status='failed', stage='configuration', reason='dependency_unavailable',
                            exception_type='ImportError', elapsed_seconds=elapsed)
                self._local.__dict__.clear()
                return
            import paramiko
            name = type(exc).__name__
            fields['stage'] = getattr(self._local, 'row', {}).get('stage', 'connect_or_handshake')
            fields.update(status='failed', exception_type=name if name in EXCEPTIONS else 'Exception', reason='unexpected_error')
            if isinstance(exc, HostKeyConfirmationRequired):
                fields.update(status='confirmation_required', stage='host_key', reason='host_key_confirmation_required')
            elif isinstance(exc, HostKeyChangedError):
                fields.update(status='blocked', stage='host_key', reason='host_key_changed')
            elif isinstance(exc, paramiko.BadAuthenticationType):
                fields.update(stage='authentication', reason='auth_method_not_allowed', allowed_auth_known=True,
                              allowed_auth_methods=list(exc.allowed_types))
            elif isinstance(exc, paramiko.PasswordRequiredException):
                fields.update(stage='configuration', reason='key_passphrase_required')
            elif isinstance(exc, paramiko.AuthenticationException):
                reason = 'authentication_timeout' if str(exc) == 'Authentication timeout.' else (
                    'authentication_rejected' if str(exc) == 'Authentication failed.' else 'authentication_interrupted')
                fields.update(stage='authentication', reason=reason, status='timeout' if reason == 'authentication_timeout' else 'failed')
            elif isinstance(exc, socket.gaierror):
                fields.update(stage='connect_or_handshake', reason='name_resolution_failed')
            elif isinstance(exc, TimeoutError):
                fields.update(status='timeout', reason='connection_timeout')
            elif isinstance(exc, FileNotFoundError) and getattr(self._local, 'row', {}).get('auth_method') == 'private_key':
                fields.update(stage='configuration', reason='key_file_unavailable')
            elif isinstance(exc, paramiko.ssh_exception.NoValidConnectionsError):
                errors = list(exc.errors.values())
                fields.update(stage='connect_or_handshake', reason='connection_refused'
                              if errors and all(isinstance(e, ConnectionRefusedError) for e in errors) else 'connection_error')
            elif isinstance(exc, OSError):
                fields.update(reason='windows_access_denied' if getattr(exc, 'winerror', None) == 10013 else
                              'connection_refused' if isinstance(exc, ConnectionRefusedError) else 'connection_error')
            elif isinstance(exc, ValueError):
                fields.update(stage='configuration', reason='configuration_error')
            elif isinstance(exc, paramiko.SSHException):
                fields.update(reason='handshake_error')
            for key in ('errno', 'winerror'):
                value = getattr(exc, key, None)
                if type(value) is int:
                    fields[key] = value
        self.update(**fields)
        self._local.__dict__.clear()

    def snapshot(self):
        with self._lock:
            return sanitized_login_attempts(deepcopy(list(self._rows)))


def record_login(operation):
    @wraps(operation)
    def wrapped(self, password='', expected_fingerprint='', timeout=15, auth_method='password', private_key_path=''):
        journal = self.login_diagnostics
        reused = self.connected
        journal.begin(self, password, timeout, auth_method, private_key_path)
        started = time.monotonic()
        try:
            result = operation(self, password, expected_fingerprint, timeout, auth_method, private_key_path)
        except Exception as exc:
            journal.finish(time.monotonic() - started, exc)
            raise
        else:
            journal.transport(self._client.get_transport() if self._client is not None else None)
            journal.finish(time.monotonic() - started, reused=reused)
            return result
    return wrapped
