"""Bounded read-only decoders for MS-GPIPSEC 2.2.1.3.1 and 2.2.1.5.1."""
import struct
from uuid import UUID

FILTER_V1 = UUID('80dc20b5-2ec8-11d1-a89e-00a0248d3021').bytes_le
FILTER_V2 = UUID('35fecd3d-ae29-4373-8a6a-c5d8fab2fb08').bytes_le
NFA = UUID('11bbac00-498d-11d1-8639-00a0248d3021').bytes_le


class Reader:
    def __init__(self, data):
        if not isinstance(data, bytes) or len(data) > 4 * 1024 * 1024:
            raise ValueError('Unsupported policy binary size or type')
        self.data = data
        self.offset = 0

    def take(self, size):
        if size < 0 or self.offset + size > len(self.data):
            raise ValueError('Truncated policy binary')
        start = self.offset
        self.offset += size
        return self.data[start:self.offset]

    def uint(self):
        return struct.unpack('<I', self.take(4))[0]

    def variable(self):
        return self.take(self.uint())

    def end(self):
        if self.offset != len(self.data):
            raise ValueError('Unsupported trailing policy binary')


def _strings(reader):
    source = reader.variable()
    destination = reader.variable()
    reader.variable()  # Description is not an endpoint selector.
    identifier = reader.take(16)
    if source.strip(b'\x00') or destination.strip(b'\x00'):
        raise ValueError('DNS selectors are not literal endpoint filters')
    return identifier


def _v1(reader):
    identifier = _strings(reader)
    mirror = reader.uint()
    source, mask, destination, dstmask, tunnel = (reader.take(4) for _ in range(5))
    protocol = reader.uint()
    srcport, dstport, is_tunnel, special, options = struct.unpack('<HHBBH', reader.take(8))
    return dict(id=identifier, mirror=mirror, source=source, mask=mask,
                destination=destination, dstmask=dstmask, tunnel=tunnel,
                protocol=protocol, srcport=srcport, dstport=dstport,
                is_tunnel=is_tunnel, special=special, options=options)


def _address(reader):
    kind, version = reader.uint(), reader.uint()
    address, secondary = reader.take(16), reader.take(16)
    return kind, version, address, secondary


def _v2(reader):
    identifier = _strings(reader)
    mirror = reader.uint()
    source, destination = _address(reader), _address(reader)
    srcport = struct.unpack('<IHH', reader.take(8))
    dstport = struct.unpack('<IHH', reader.take(8))
    return dict(id=identifier, mirror=mirror, source=source, destination=destination,
                srcport=srcport, dstport=dstport, protocol=reader.uint(), flags=reader.uint())


def filters(data):
    reader = Reader(data)
    version = reader.take(16)
    legacy = []
    if version == FILTER_V1:
        length, count = reader.uint(), reader.uint()
        if count > 1:
            raise ValueError('Endpoint filter list must have exactly one filter')
        start = reader.offset
        legacy = [_v1(reader) for _ in range(count)]
        consumed = reader.offset - start
        # Historical formats include count/trailing sentinel in their length.
        if length not in {consumed, consumed + 3, consumed + 4}:
            raise ValueError('Invalid legacy filter length')
        if reader.data[reader.offset:] in (b'', b'\x00'):
            reader.take(len(reader.data) - reader.offset)
            return legacy, []
        version = reader.take(16)
    if version != FILTER_V2:
        raise ValueError('Unsupported filter policy identifier')
    length, legacy_count, count = reader.uint(), reader.uint(), reader.uint()
    if legacy_count not in (0, len(legacy)) or count > 1:
        raise ValueError('Unexpected extended filter count')
    start = reader.offset
    modern = [_v2(reader) for _ in range(count)]
    consumed = reader.offset - start
    if length not in {consumed}:
        raise ValueError('Invalid extended filter length')
    if reader.data[reader.offset:] == b'\x00':
        reader.take(1)
    reader.end()
    if not legacy and not modern:
        raise ValueError('Empty endpoint filter list')
    return legacy, modern


def rule(data):
    reader = Reader(data)
    if reader.take(16) != NFA:
        raise ValueError('Unsupported rule policy identifier')
    length, count = reader.uint(), reader.uint()
    if count > 64:
        raise ValueError('Too many rule authentication methods')
    for _ in range(count):
        reader.uint()
        reader.variable()  # Authentication material is never retained.
    interface = reader.uint()
    interface_name = reader.variable()
    tunnel, is_tunnel, active = reader.take(4), reader.uint(), reader.uint()
    endpoint_name = reader.variable()
    if length not in {reader.offset - 21, reader.offset - 20}:
        raise ValueError('Invalid rule data length')
    # Remaining documented extensions describe authentication or tunnel addresses.
    # A transport-only rule does not interpret tunnel addresses.
    remaining = reader.data[reader.offset:]
    if remaining.startswith(bytes.fromhex('01' * 15 + '01')):
        reader.take(16)
        alternate_count = reader.uint()
        if alternate_count != count:
            raise ValueError('Invalid alternate authentication count')
        for _ in range(alternate_count):
            reader.uint()
            reader.variable()
    if reader.data[reader.offset:].startswith(bytes.fromhex('01' * 15 + '02')):
        reader.take(16)
        reader.uint()
        reader.take(count * 4)
    if reader.data[reader.offset:].startswith(bytes.fromhex('01' * 15 + '03')):
        reader.take(32)
    if reader.data[reader.offset:] == b'\x00':
        reader.take(1)
    reader.end()
    return dict(interface=interface, interface_name=interface_name, tunnel=tunnel,
                is_tunnel=is_tunnel, active=active, endpoint_name=endpoint_name)
