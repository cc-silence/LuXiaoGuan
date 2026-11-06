"""Bounded passive WPS metadata reader for the existing airodump scan."""
import struct
import time
from dataclasses import replace

from modules.capture_services.capture_logging import LOGGER
from .remote_resources import CaptureResourceError, MAX_WORKSPACE_KB

_MAX_PACKET = 65535
_MAX_POINTS = 4096
_CHUNK = 65536
_MAX_FILE = MAX_WORKSPACE_KB * 1024
# The legacy Version stays 0x10 in WPS 2.x; Version2 uses a vendor extension.
_BYTE_FIELDS = {0x104a: (0x10,), 0x1044: (1, 2), 0x1057: (0, 1)}
_MAGICS = {b"\xd4\xc3\xb2\xa1": "<", b"\xa1\xb2\xc3\xd4": ">",
           b"\x4d\x3c\xb2\xa1": "<", b"\xa1\xb2\x3c\x4d": ">"}


def _metadata_transport_errors():
    from .command_runner import _transport_errors
    try:
        from paramiko.sftp import SFTPError
    except ImportError:
        return _transport_errors()
    return _transport_errors() + (SFTPError,)


def wps_status_text(point):
    if point.wps_supported is not True:
        return "\u4e0d\u652f\u6301"
    return "\u5df2\u9501\u5b9a" if point.wps_locked else "\u652f\u6301"


def parse_wps_frame(frame, linktype):
    """Accept complete AP beacons/probe responses; never infer from an SSID."""
    if linktype == 127:
        if len(frame) < 8 or frame[0] != 0:
            return None
        length = int.from_bytes(frame[2:4], "little")
        if not 8 <= length <= len(frame):
            return None
        # Radiotap FCS flags must be decoded before treating trailing bytes as IEs.
        present = int.from_bytes(frame[4:8], "little")
        cursor = 8
        extension = present
        while extension & (1 << 31):
            if cursor + 4 > length:
                return None
            extension = int.from_bytes(frame[cursor:cursor+4], "little")
            cursor += 4
        if present & 1:  # TSFT, aligned to 8 from the radiotap header start.
            cursor = (cursor + 7) & ~7
            cursor += 8
        if present & 2:
            if cursor >= length:
                return None
            flags = frame[cursor]
            if flags & 0x40:  # BADFCS
                return None
            if flags & 0x10:
                if len(frame) < length + 4:
                    return None
                frame = frame[:-4]
        frame = frame[length:]
    elif linktype != 105:
        return None
    if len(frame) < 36:
        return None
    fc = int.from_bytes(frame[:2], "little")
    if fc & 0xc70f or ((fc >> 4) & 15) not in (5, 8) or frame[22] & 15:
        return None
    address = frame[16:22]
    if address != frame[10:16] or address == bytes(6) or address[0] & 1:
        return None
    cursor, payload = 36, bytearray()
    while cursor < len(frame):
        if cursor + 2 > len(frame):
            return None
        tag, length = frame[cursor:cursor+2]
        end = cursor + 2 + length
        if end > len(frame):
            return None
        value = frame[cursor+2:end]
        if tag == 221 and value[:4] == b"\x00\x50\xf2\x04":
            payload.extend(value[4:])
        cursor = end
    if not payload:
        return None
    cursor, locked = 0, None
    required_fields = set()
    while cursor < len(payload):
        if cursor + 4 > len(payload):
            return None
        tag, length = struct.unpack_from(">HH", payload, cursor)
        end = cursor + 4 + length
        if end > len(payload):
            return None
        value = payload[cursor+4:end]
        allowed = _BYTE_FIELDS.get(tag)
        if allowed is not None and (length != 1 or value[0] not in allowed):
            return None
        if tag in (0x104a, 0x1044):
            required_fields.add(tag)
        if tag == 0x1057:
            locked = bool(value[0])
        cursor = end
    if required_fields != {0x104a, 0x1044}:
        return None
    if locked is None:
        # hostap omits AP Setup Locked when unlocked, in both beacon and probe IEs.
        locked = False
    bssid = ":".join(f"{part:02X}" for part in address)
    return bssid, locked, (fc >> 4) & 15


class WpsScanReader:
    """Per-task metadata and bounded partial record; no persistent packet copy."""
    def __init__(self):
        self.offset = 0
        self.pending = bytearray()
        self.endian = None
        self.linktype = None
        self.snaplen = 0
        self.fcs_bytes = 0
        self.points = {}
        self.invalid = False
        self.failure_reported = False

    def feed(self, data):
        if self.invalid:
            return
        self.pending.extend(data)
        if self.endian is None:
            if len(self.pending) < 24:
                return
            self.endian = _MAGICS.get(bytes(self.pending[:4]))
            if self.endian is None:
                self.invalid = True
                self.pending.clear()
                return
            major, minor = struct.unpack_from(self.endian + "HH", self.pending, 4)
            self.snaplen, network = struct.unpack_from(self.endian + "II", self.pending, 16)
            self.linktype = network & 0xffff
            if self.linktype == 105 and network & (1 << 26):
                self.fcs_bytes = ((network >> 28) & 15) * 2
            if (major, minor) != (2, 4) or self.linktype not in (105, 127) or not self.snaplen:
                self.invalid = True
                self.pending.clear()
                return
            del self.pending[:24]
        cursor = 0
        while cursor + 16 <= len(self.pending):
            _, _, size, original = struct.unpack_from(self.endian + "IIII", self.pending, cursor)
            if not 0 < size <= min(_MAX_PACKET, self.snaplen) or original < size:
                self.invalid = True
                self.pending.clear()
                return
            end = cursor + 16 + size
            if end > len(self.pending):
                break
            packet_end = end - self.fcs_bytes
            result = (parse_wps_frame(self.pending[cursor+16:packet_end], self.linktype)
                      if size == original and packet_end > cursor + 16 else None)
            if result is not None:
                bssid, locked, subtype = result
                if bssid in self.points or len(self.points) < _MAX_POINTS:
                    self.points[bssid] = locked
            cursor = end
        del self.pending[:cursor]

    def sync(self, ssh_client, remote_path, cancel_event=None, *, final=False):
        """Read only new bytes, with finite I/O, memory and workspace budgets."""
        def check():
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("Wi-Fi scan cancelled")
            if time.monotonic() >= deadline:
                raise TimeoutError("WPS metadata transfer timed out")
        def set_io_budget(sftp):
            check()
            sftp.get_channel().settimeout(min(5, max(0.1, deadline-time.monotonic())))

        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("Wi-Fi scan cancelled")
        if self.invalid:
            return
        transport_errors = _metadata_transport_errors()
        broken_transport_errors = (TimeoutError, EOFError, ConnectionError) + tuple(
            error for error in transport_errors if not issubclass(error, OSError))
        deadline = time.monotonic() + (10 if final else 5)
        try:
            check()
            with ssh_client.transfer(cancel_event, timeout=max(0.1, deadline-time.monotonic())):
                check()
                sftp = ssh_client.open_sftp(timeout=min(5, max(0.1, deadline-time.monotonic())), cancel_event=cancel_event)
                try:
                    set_io_budget(sftp)
                    size = sftp.stat(str(remote_path)).st_size
                    check()
                    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
                        raise ValueError("Scan PCAP size is unavailable")
                    if size > _MAX_FILE:
                        raise CaptureResourceError("\u626b\u63cf\u4e34\u65f6\u6587\u4ef6\u8fbe\u5230 32 MiB \u5b89\u5168\u4e0a\u9650\uff0c\u5df2\u505c\u6b62\u626b\u63cf\u3002")
                    if size < self.offset:
                        raise ValueError("Scan PCAP unexpectedly truncated")
                    budget = min(size - self.offset, _MAX_FILE if final else 4 * 1024 * 1024)
                    if budget <= 0:
                        return
                    set_io_budget(sftp)
                    with sftp.open(str(remote_path), "rb") as stream:
                        stream.seek(self.offset)
                        while budget > 0:
                            set_io_budget(sftp)
                            data = stream.read(min(_CHUNK, budget))
                            if not data:
                                break
                            self.feed(data)
                            self.offset += len(data)
                            budget -= len(data)
                            if self.invalid:
                                break
                    check()
                except broken_transport_errors:
                    # Still inside the transfer lock: never discard another task's channel.
                    ssh_client.drop_sftp()
                    raise
        except FileNotFoundError:
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("Wi-Fi scan cancelled")
            return  # Producer has not flushed the first snapshot yet.
        except (InterruptedError, CaptureResourceError):
            raise
        except (OSError, EOFError, ValueError, RuntimeError) + transport_errors as exc:
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("Wi-Fi scan cancelled") from exc
            if not self.failure_reported:
                LOGGER.warning("WPS scan metadata unavailable: %s", type(exc).__name__)
                self.failure_reported = True

    def apply(self, points):
        return tuple(replace(point, wps_supported=True, wps_locked=self.points[point.bssid.upper()])
                     if point.bssid.upper() in self.points else point for point in points)

    def log_summary(self):
        LOGGER.info("WPS scan summary bytes=%d supported=%d locked=%d invalid=%s incomplete_tail=%d transfer_failed=%s",
                    self.offset, len(self.points), sum(self.points.values()), self.invalid,
                    len(self.pending), self.failure_reported)
