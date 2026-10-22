"""同信道分组与有界的目标 PCAP 分离。"""
import struct
from pathlib import Path
from .command_builder import normalize_bssid, normalize_channel
from .result_converter import _PCAP_MAGICS, _parse_eapol_key_frame


def group_channel_targets(targets):
    """按扫描频段和信道稳定分组，不猜测 radio 对应关系。"""
    if len(targets) > 256:
        raise ValueError("At most 256 same-channel targets are supported.")
    groups = {}
    for target in targets:
        channel = normalize_channel(target.channel)
        band = target.band
        if band not in ('2.4G', '5G') or (band == '2.4G') != (channel <= 14):
            raise ValueError('Target band/channel is inconsistent; scan again.')
        normalize_bssid(target.bssid)
        groups.setdefault((band, channel), []).append(target)
    return tuple(tuple(group) for group in groups.values())


def split_channel_cap(source, targets, cancel_event=None, *, with_metadata=False):
    """Project target management/EAPOL records retaining PCAP timestamps.

    Only classic PCAP from airodump is accepted. Limits and malformed final
    records fail explicitly; partial output is removed on failure/cancellation.
    """
    source = Path(source)
    paths = {normalize_bssid(t.bssid): source.with_name(
        source.stem + '-' + normalize_bssid(t.bssid).replace(':', '') + '.cap') for t in targets}
    if source.stat().st_size > 64 * 1024 * 1024:
        raise ValueError('Channel capture exceeds the 64 MiB limit.')
    handles = {}
    incomplete_tail = False
    try:
        with source.open('rb') as stream:
            header = stream.read(24)
            endian = _PCAP_MAGICS.get(header[:4])
            if len(header) != 24 or endian is None:
                raise ValueError('Unrecognized channel PCAP header.')
            major, minor, _, _, snaplen, link = struct.unpack(endian + 'HHiiii', header[4:])
            if (major, minor) != (2, 4) or link not in (105, 127) or snaplen <= 0:
                raise ValueError('Unsupported channel PCAP format.')
            for key, path in paths.items():
                handles[key] = path.open('xb')
                handles[key].write(header)
            records = 0
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise InterruptedError('Channel split cancelled.')
                record = stream.read(16)
                if not record: break
                if len(record) != 16:
                    incomplete_tail = True
                    break
                _, _, size, original = struct.unpack(endian + 'IIII', record)
                if size > snaplen or size > original or size > 1024 * 1024:
                    raise ValueError('Invalid channel record length.')
                frame = stream.read(size)
                if len(frame) != size:
                    incomplete_tail = True
                    break
                records += 1
                if records > 200000:
                    raise ValueError('Channel capture exceeds the record limit.')
                offset = int.from_bytes(frame[2:4], 'little') if link == 127 else 0
                if (link == 127 and (len(frame) < 8 or frame[0] != 0 or offset < 8)) or len(frame) < offset + 24:
                    continue
                fc = int.from_bytes(frame[offset:offset + 2], 'little')
                key = ''
                if ((fc >> 2) & 3) == 0:
                    key = ':'.join(f'{v:02X}' for v in frame[offset + 16:offset + 22])
                else:
                    parsed = _parse_eapol_key_frame(frame, link == 127)
                    if parsed and parsed.get('ap'):
                        ap = parsed['ap']
                        key = ':'.join(f'{v:02X}' for v in ap) if isinstance(ap, bytes) else str(ap)
                if key in handles:
                    handles[key].write(record + frame)
        for handle in handles.values(): handle.close()
        return (paths, incomplete_tail) if with_metadata else paths
    except BaseException:
        for handle in handles.values(): handle.close()
        for key in handles: paths[key].unlink(missing_ok=True)
        raise
