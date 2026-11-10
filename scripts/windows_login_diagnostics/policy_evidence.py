"""Export only known policy layouts; erase authentication bytes before serialization."""
import ipaddress
import base64
from modules.platform_services.legacy_ipsec_binary import Reader, FILTER_V1, FILTER_V2, NFA

MAX_ITEMS = 4096
AUTH1 = bytes([1]) * 16
AUTH2 = bytes([1]) * 15 + b'\x02'
IPV6 = bytes([1]) * 15 + b'\x03'


def _skip_variable(reader, output, *, erase):
    size = reader.uint()
    start = reader.offset
    reader.take(size)
    if erase:
        output[start:reader.offset] = bytes(size)
    return size


def _count(reader, limit=MAX_ITEMS):
    count = reader.uint()
    if count > limit:
        raise ValueError('policy_item_limit')
    return count


def _filter_entries(reader, output, count, fixed):
    for _ in range(count):
        _skip_variable(reader, output, erase=False)  # DNS selectors matter.
        _skip_variable(reader, output, erase=False)
        _skip_variable(reader, output, erase=True)  # Free-text descriptions do not.
        reader.take(fixed)


def filter_evidence(data):
    reader = Reader(data)
    output = bytearray(data)
    layout = []
    parsed_legacy = 0
    kind = reader.take(16)
    if kind == FILTER_V1:
        length, count = reader.uint(), _count(reader)
        _filter_entries(reader, output, count, 52)
        parsed_legacy = count
        layout.append(dict(version=1, length=length, count=count))
        if reader.data[reader.offset:] in (b'', b'\x00'):
            reader.take(len(reader.data) - reader.offset)
            return bytes(output), layout
        kind = reader.take(16)
    if kind != FILTER_V2:
        raise ValueError('unknown_filter_layout')
    length, legacy_count, count = reader.uint(), _count(reader), _count(reader)
    if legacy_count not in (0, parsed_legacy):
        raise ValueError('unsupported_filter_count_override')
    _filter_entries(reader, output, count, 124)
    layout.append(dict(version=2, length=length, legacy_count=legacy_count, count=count))
    if reader.data[reader.offset:] == b'\x00':
        reader.take(1)
    reader.end()
    return bytes(output), layout


def rule_evidence(data):
    reader = Reader(data)
    output = bytearray(data)
    if reader.take(16) != NFA:
        raise ValueError('unknown_rule_layout')
    length, count = reader.uint(), _count(reader, 64)
    methods = []
    for _ in range(count):
        kind = reader.uint()
        size = _skip_variable(reader, output, erase=True)
        methods.append(dict(type=kind, bytes=size))
    interface = reader.uint()
    _skip_variable(reader, output, erase=False)
    tunnel = str(ipaddress.IPv4Address(reader.take(4)))
    is_tunnel, active = reader.uint(), reader.uint()
    _skip_variable(reader, output, erase=False)
    base_end = reader.offset
    extensions = []
    if reader.data[reader.offset:].startswith(AUTH1):
        reader.take(16)
        alternate_count = _count(reader, 64)
        if alternate_count != count:
            raise ValueError('alternate_auth_count_mismatch')
        for _ in range(alternate_count):
            reader.uint()
            _skip_variable(reader, output, erase=True)
        extensions.append('alternate_authentication')
    if reader.data[reader.offset:].startswith(AUTH2):
        reader.take(16)
        reader.uint()
        reader.take(count * 4)
        extensions.append('authentication_flags')
    if reader.data[reader.offset:].startswith(IPV6):
        reader.take(32)
        extensions.append('ipv6_tunnel')
    if reader.data[reader.offset:] == b'\x00':
        reader.take(1)
    reader.end()
    return bytes(output), dict(length=length, base_bytes=base_end, methods=methods,
        interface=interface, tunnel_address=tunnel, is_tunnel=is_tunnel,
        active=active, extensions=extensions)


def binary_evidence(key, value):
    result = dict(bytes=len(value), status='omitted', reason='not_needed_for_login')
    if len(value) > 4 * 1024 * 1024:
        return dict(bytes=len(value), status='omitted', reason='size_limit')
    name = key.lower()
    if not name.startswith(('ipsecfilter{', 'ipsecnfa{')):
        return result
    try:
        if name.startswith('ipsecfilter{'):
            clean, layout = filter_evidence(value)
            redacted = ['filter_description']
        else:
            clean, layout = rule_evidence(value)
            redacted = ['authentication_values', 'alternate_authentication_values']
        return dict(bytes=len(value), status='sanitized', base64=base64.b64encode(clean).decode('ascii'),
                    layout=layout, redacted_fields=redacted)
    except (ValueError, OverflowError) as error:
        # Unknown or truncated data may contain credentials. Never export any prefix.
        return dict(bytes=len(value), status='omitted', reason='unknown_or_truncated_layout',
                    layout_error=str(error))


ALLOWED = frozenset(('activepolicy', 'classname', 'name', 'ipsecname', 'ipsecid',
    'ipsecdatatype', 'ipsecnfareference', 'ipsecfilterreference',
    'ipsecnegotiationpolicyreference', 'ipsecisakmpreference', 'ipsecownersreference',
    'ipsecnegotiationpolicytype', 'ipsecnegotiationpolicyaction', 'whenchanged'))


def sanitize_tree(nodes):
    clean = {}
    for key, node in nodes.items():
        row = {}
        for field, (kind, value) in node.items():
            if field == 'ipsecdata' and kind == 3 and isinstance(value, dict):
                encoded = value.get('base64')
                if not isinstance(encoded, str) or len(encoded) > 6 * 1024 * 1024:
                    row[field] = dict(status='omitted', reason='invalid_binary_wrapper')
                else:
                    try:
                        row[field] = binary_evidence(key, base64.b64decode(encoded, validate=True))
                    except ValueError:
                        row[field] = dict(status='omitted', reason='invalid_base64')
            elif field in ALLOWED:
                row[field] = dict(type=kind, value=value)
        clean[key] = row
    return clean
