"""Task-scoped network evidence containing only finite labels and numbers."""
from contextlib import contextmanager
from contextvars import ContextVar
import errno
import ipaddress
import socket

_sink = ContextVar('network_diagnostic_sink', default=None)
STAGES = frozenset({'static_inspect', 'static_command', 'static_prepare', 'static_activate', 'static_commit', 'static_restore', 'lease_before', 'lease_after_release', 'lease_after_renew', 'adapter_before', 'adapter_after', 'address_wait', 'lease_release',
    'lease_renew', 'lease_process', 'network_plan', 'network_context'})
REASONS = frozenset({'address_ready', 'address_pending', 'adapter_missing', 'adapter_changed',
    'address_conflict', 'route_pending', 'adapter_disconnected', 'dhcp_disabled', 'inspection_failed', 'authorization_failed', 'permission_denied',
    'lease_failed', 'lease_timeout', 'lease_unsupported', 'reboot_required', 'request_expired',
    'system_accepted', 'system_error', 'connection_refused', 'connection_reset',
    'connection_aborted', 'network_unreachable', 'host_unreachable', 'socket_timeout',
    'dns_failed', 'authentication_failed', 'host_key_changed', 'host_key_unconfirmed',
    'ssh_channel_closed', 'ssh_error', 'connection_unknown'})
NUMBERS = frozenset({'ip_enabled', 'system_exit_code', 'native_error', 'elevated', 'timeout_seconds',
    'elapsed_seconds', 'adapter_index', 'adapter_found', 'adapter_connected', 'dhcp_enabled',
    'renewable', 'route_selected', 'address_count', 'usable_address_count', 'gateway_count',
    'dns_count', 'target_on_link', 'target_subnet_match', 'gateway_matches_target', 'address_conflicts_target',
    'snapshot_cached', 'snapshot_attempts', 'adapter_count', 'target_count', 'restoring', 'automatic',
    'automatic_allowed', 'route_candidates', 'errno', 'winerror'})

@contextmanager
def observing(sink):
    token = _sink.set(sink)
    try:
        yield
    finally:
        _sink.reset(token)

def emit(stage, reason='', **fields):
    sink = _sink.get()
    if sink is None or stage not in STAGES:
        return
    row = dict(stage=stage, reason=reason if reason in REASONS else '')
    row.update({k: v for k, v in fields.items() if k in NUMBERS
                and (v is None or type(v) in (int, float, bool))})
    try:
        sink(row)
    except Exception:
        pass  # Diagnostic storage must never interrupt recovery.

def adapter_state(adapter, expected=()):
    """Keep network relationships without persisting addresses or identities."""
    if not isinstance(adapter, dict):
        return dict(adapter_found=False)
    def interfaces(values):
        result = []
        if isinstance(values,str):values=[values]
        for value in list(values or ())[:128]:
            try:
                result.append(ipaddress.IPv4Interface(value))
            except (TypeError, ValueError):
                pass
        return result
    addresses = interfaces(adapter.get('addresses'))
    targets = interfaces(expected)
    usable = [a for a in addresses if not (a.ip.is_link_local or a.ip.is_loopback
              or a.ip.is_unspecified or a.ip.is_multicast)]
    row = dict(adapter_found=True, adapter_index=adapter.get('index'),
        adapter_connected=adapter.get('connected'), dhcp_enabled=adapter.get('dhcp'),
        renewable=adapter.get('renewable'), route_selected=adapter.get('route'),
        address_count=len(addresses), usable_address_count=len(usable),
        gateway_count=len(adapter.get('gateways') or ()), dns_count=len(adapter.get('dns') or ()))
    if targets:
        row.update(target_on_link=any(t.ip in a.network and a.ip!=t.ip for a in usable for t in targets),
            target_subnet_match=any(a.ip in t.network and a.ip != t.ip
                   for a in usable for t in targets),
            gateway_matches_target=any(str(t.ip) in (adapter.get('gateways') or ()) for t in targets),
            address_conflicts_target=any(a.ip == t.ip for a in addresses for t in targets))
    return row

def failure_fields(error):
    """Classify chained errors without retaining messages or endpoints."""
    result = {}; reason = 'connection_unknown'; current = error; seen = set()
    for _ in range(8):
        if current is None or id(current) in seen:
            break
        seen.add(id(current))
        for key in ('errno', 'winerror'):
            value = getattr(current, key, None)
            if type(value) is int:
                result[key] = value
        name = type(current).__name__
        if name in {'HostKeyChangedError', 'BadHostKeyException'}:
            reason = 'host_key_changed'; break
        if name == 'HostKeyConfirmationRequired':
            reason = 'host_key_unconfirmed'; break
        if name in {'AuthenticationException', 'BadAuthenticationType', 'PasswordRequiredException'}:
            reason = 'authentication_failed'; break
        codes = {getattr(current,k) for k in ('errno','winerror') if type(getattr(current,k,None)) is int}
        if isinstance(current, socket.gaierror): reason = 'dns_failed'
        elif isinstance(current, TimeoutError): reason = 'socket_timeout'
        elif isinstance(current, ConnectionRefusedError) or codes & {errno.ECONNREFUSED, 10061}: reason = 'connection_refused'
        elif isinstance(current, ConnectionResetError) or codes & {errno.ECONNRESET, 10054}: reason = 'connection_reset'
        elif isinstance(current, ConnectionAbortedError) or codes & {errno.ECONNABORTED, 10053}: reason = 'connection_aborted'
        elif codes & {errno.ENETUNREACH, 10051}: reason = 'network_unreachable'
        elif codes & {errno.EHOSTUNREACH, 10065}: reason = 'host_unreachable'
        elif name == 'SSHException': reason = 'ssh_channel_closed' if str(current) == 'Channel closed.' else 'ssh_error'
        nested=getattr(current,'errors',None)
        next_error=next((v for v in nested.values() if isinstance(v,BaseException)),None) if isinstance(nested,dict) else None
        current = current.__cause__ or current.__context__ or next_error
    result['connection_reason'] = reason
    return result
