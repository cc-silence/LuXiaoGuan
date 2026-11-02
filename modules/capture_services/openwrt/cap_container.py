"""Streaming PCAPNG to classic nanosecond PCAP, preserving every packet byte.

Format references: IETF opsawg pcapng-04 and pcap-03 (fixed drafts).
Metadata remains in the retained original. Ambiguous packet streams fail closed.
"""
import hashlib
import os
from pathlib import Path
import struct
import tempfile
import time
from modules.platform_services.local_file_publish import publish_new_file

_MAX_BLOCK = 16 * 1024 * 1024
# libpcap 1.10.5 pcap-int.h MAXIMUM_SNAPLEN for 802.11/radiotap.
_MAX_PACKET = 262144
_MAGICS = {b"\xd4\xc3\xb2\xa1": ("<", 1000000),
           b"\xa1\xb2\xc3\xd4": (">", 1000000),
           b"\x4d\x3c\xb2\xa1": ("<", 1000000000),
           b"\xa1\xb2\x3c\x4d": (">", 1000000000)}


def _exact(handle, length):
    value = handle.read(length)
    if len(value) != length:
        raise ValueError("Truncated capture; original preserved.")
    return value


def _options(body, endian, check=None):
    cursor = 0
    checkpoint = 0
    while cursor < len(body):
        if check is not None and cursor >= checkpoint:
            check()
            checkpoint = cursor + 4096
        if cursor + 4 > len(body):
            raise ValueError("Truncated capture options.")
        code, length = struct.unpack_from(endian + "HH", body, cursor)
        cursor += 4
        end = cursor + ((length + 3) & ~3)
        if end > len(body):
            raise ValueError("Invalid capture option length.")
        if code == 0:
            if length or any(body[cursor:]):
                raise ValueError("Invalid capture option terminator.")
            return
        yield code, body[cursor:cursor + length]
        cursor = end


def _link_word(link, fcs_bits):
    if fcs_bits is None:
        return link
    if fcs_bits % 16 or not 0 <= fcs_bits <= 240:
        raise ValueError("FCS length cannot be preserved in classic CAP.")
    return link | (1 << 26) | ((fcs_bits // 16) << 28)


def _packet_link(link, fcs_bits, options, endian, check):
    flags_seen = False
    for code, value in _options(options, endian, check):
        if code != 2:
            continue
        if flags_seen or len(value) != 4:
            raise ValueError("Invalid or duplicate packet flags.")
        flags_seen = True
        flags = struct.unpack(endian + "I", value)[0]
        # Error/offload semantics cannot be represented by classic CAP.
        if flags & (0xffff0000 | 0xf000 | (1 << 9) | (1 << 11)):
            raise ValueError("Packet error/offload flags cannot be preserved in CAP.")
        fcs_octets = (flags >> 5) & 15
        if fcs_octets:
            fcs_bits = fcs_octets * 8
    return _link_word(link, fcs_bits)


def _ng_records(handle, check):
    endian = None
    interfaces = []
    section = -1
    used_interface = None
    while True:
        check()
        header = handle.read(8)
        if not header:
            return
        if len(header) != 8:
            raise ValueError("Truncated PCAPNG block header.")
        if header[:4] == b"\x0a\x0d\x0d\x0a":
            magic = _exact(handle, 4)
            endian = {b"\x4d\x3c\x2b\x1a": "<", b"\x1a\x2b\x3c\x4d": ">"}.get(magic)
            if endian is None:
                raise ValueError("Invalid PCAPNG byte order.")
            size = struct.unpack(endian + "I", header[4:])[0]
            prefix = magic
        else:
            if endian is None:
                raise ValueError("PCAPNG section header missing.")
            size = struct.unpack(endian + "I", header[4:])[0]
            prefix = b""
        if size < 12 + len(prefix) or size % 4 or size > _MAX_BLOCK:
            raise ValueError("Invalid or oversized PCAPNG block.")
        tail = _exact(handle, size - 8 - len(prefix))
        if struct.unpack(endian + "I", tail[-4:])[0] != size:
            raise ValueError("PCAPNG block lengths disagree.")
        body = prefix + tail[:-4]
        kind = struct.unpack(endian + "I", header[:4])[0]
        if kind == 0x0a0d0d0a:
            if len(body) < 16 or struct.unpack_from(endian + "HH", body, 4) != (1, 0):
                raise ValueError("Unsupported PCAPNG section version.")
            for _ in _options(body[16:], endian, check):
                pass
            interfaces = []
            section += 1
        elif kind == 1:
            if len(body) < 8 or len(interfaces) >= 256:
                raise ValueError("Invalid interface description.")
            link, reserved, snaplen = struct.unpack_from(endian + "HHI", body)
            if reserved or link not in (105, 127):
                raise ValueError("Unsupported capture link type; original preserved.")
            resolution, shift, fcs_bits = 1000000, 0, None
            seen = set()
            for code, value in _options(body[8:], endian, check):
                if code in (9, 13, 14):
                    if code in seen:
                        raise ValueError("Duplicate timestamp option.")
                    seen.add(code)
                if code == 9:
                    if len(value) != 1:
                        raise ValueError("Invalid timestamp resolution.")
                    resolution = (2 ** (value[0] & 127)) if value[0] & 128 else 10 ** value[0]
                elif code == 13:
                    if len(value) != 1:
                        raise ValueError("Invalid interface FCS length.")
                    fcs_bits = value[0]
                elif code == 14:
                    if len(value) != 8:
                        raise ValueError("Invalid timestamp offset.")
                    shift = struct.unpack(endian + "q", value)[0]
            interfaces.append((link, snaplen, resolution, shift, fcs_bits))
        elif kind in (2, 6):
            if len(body) < 20:
                raise ValueError("Truncated packet block.")
            if kind == 6:
                iface, high, low, captured, original = struct.unpack_from(endian + "IIIII", body)
            else:
                iface, _, high, low, captured, original = struct.unpack_from(endian + "HHIIII", body)
            if iface >= len(interfaces):
                raise ValueError("Packet references an unknown interface.")
            identity = (section, iface)
            if used_interface is not None and identity != used_interface:
                raise ValueError("Mixed capture interfaces cannot be safely exported to CAP.")
            used_interface = identity
            link, snaplen, resolution, shift, fcs_bits = interfaces[iface]
            padded = (captured + 3) & ~3
            if captured > _MAX_PACKET or captured > original or (snaplen and captured > snaplen) or 20 + padded > len(body):
                raise ValueError("Invalid captured packet length.")
            link = _packet_link(link, fcs_bits, body[20 + padded:], endian, check)
            nanos, remainder = divmod(((high << 32) | low) * 1000000000, resolution)
            if remainder:
                raise ValueError("Timestamp precision cannot be preserved in CAP.")
            nanos += shift * 1000000000
            yield link, nanos, original, body[20:20 + captured]
        elif kind == 3:
            raise ValueError("Untimestamped simple packet blocks are not converted automatically.")


def _pcap_records(handle, check):
    header = _exact(handle, 24)
    if header[:4] not in _MAGICS:
        raise ValueError("Unknown capture format.")
    endian, scale = _MAGICS[header[:4]]
    major, minor, _, _, snaplen, link = struct.unpack(endian + "HHIIII", header[4:])
    if (major, minor) != (2, 4) or (link & 0xffff) not in (105, 127) or not snaplen:
        raise ValueError("Unsupported classic capture header.")
    if link & ~0xf400ffff or (link & 0xf0000000 and not link & (1 << 26)):
        raise ValueError("Invalid classic CAP link extension bits.")
    while True:
        check()
        header = handle.read(16)
        if not header:
            return
        if len(header) != 16:
            raise ValueError("Truncated classic packet header.")
        seconds, fraction, captured, original = struct.unpack(endian + "IIII", header)
        if fraction >= scale or captured > min(snaplen, _MAX_PACKET) or captured > original:
            raise ValueError("Invalid classic packet length or timestamp.")
        yield link, seconds * 1000000000 + fraction * (1000000000 // scale), original, _exact(handle, captured)


def _records(handle, check):
    magic = _exact(handle, 4)
    handle.seek(0)
    yield from (_ng_records(handle, check) if magic == b"\x0a\x0d\x0d\x0a" else _pcap_records(handle, check))


def _digest_record(digest, nanos, original, data):
    digest.update(struct.pack("<QII", nanos, len(data), original))
    digest.update(data)


def _file_signature(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def convert_to_cap(source, destination, *, cancel_event=None, timeout=30, max_bytes=512 * 1024 * 1024):
    """Validate, stream, independently verify, then publish without overwriting.

    Existing output and original input are never removed or replaced.
    """
    source, destination = Path(source), Path(destination)
    if source.resolve() == destination.resolve() or destination.exists():
        raise FileExistsError("CAP destination must be a new file.")
    deadline = time.monotonic() + timeout
    def check():
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("CAP conversion cancelled; original preserved.")
        if time.monotonic() >= deadline:
            raise TimeoutError("CAP conversion timed out; original preserved.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with source.open("rb") as handle:
            before = os.fstat(handle.fileno())
            if before.st_size > max_bytes:
                raise ValueError("Capture exceeds automatic conversion size limit.")
            with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".cap-", suffix=".part", delete=False) as output:
                temporary = Path(output.name)
                digest, count, linktype = hashlib.sha256(), 0, None
                for link, nanos, original, data in _records(handle, check):
                    seconds, fraction = divmod(nanos, 1000000000)
                    if not 0 <= seconds <= 0xffffffff:
                        raise ValueError("Timestamp outside classic CAP range.")
                    if linktype is None:
                        linktype = link
                        output.write(struct.pack("<IHHIIII", 0xa1b23c4d, 2, 4, 0, 0, _MAX_PACKET, link))
                    if link != linktype:
                        raise ValueError("Mixed capture link types.")
                    output.write(struct.pack("<IIII", seconds, fraction, len(data), original))
                    output.write(data)
                    _digest_record(digest, nanos, original, data)
                    count += 1
                if not count:
                    raise ValueError("Capture contains no packets.")
                output.flush()
                os.fsync(output.fileno())
            after = os.fstat(handle.fileno())
            if (_file_signature(before) != _file_signature(after)
                    or _file_signature(before) != _file_signature(source.stat())):
                raise ValueError("Capture changed during conversion.")
        verified, verified_count = hashlib.sha256(), 0
        with temporary.open("rb") as handle:
            for link, nanos, original, data in _pcap_records(handle, check):
                if link != linktype:
                    raise ValueError("CAP verification link type mismatch.")
                _digest_record(verified, nanos, original, data)
                verified_count += 1
        if verified_count != count or verified.digest() != digest.digest():
            raise ValueError("CAP packet verification failed.")
        check()
        if _file_signature(before) != _file_signature(source.stat()):
            raise ValueError("Capture changed during output verification.")
        publish_new_file(temporary, destination)
        return destination
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def validate_cap(source, *, timeout=30, cancel_event=None, max_bytes=512 * 1024 * 1024):
    """Validate native CAP without rewriting it or creating a redundant copy."""
    source = Path(source)
    deadline = time.monotonic() + timeout
    def check():
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("CAP validation cancelled.")
        if time.monotonic() >= deadline:
            raise TimeoutError("CAP validation timed out.")
    count = 0
    with source.open("rb") as handle:
        before = os.fstat(handle.fileno())
        if before.st_size > max_bytes:
            raise ValueError("Capture exceeds automatic validation size limit.")
        for _record in _pcap_records(handle, check):
            count += 1
        check()
        if (_file_signature(before) != _file_signature(os.fstat(handle.fileno()))
                or _file_signature(before) != _file_signature(source.stat())):
            raise ValueError("Capture changed during validation.")
    if not count:
        raise ValueError("Capture contains no packets.")
    return source
