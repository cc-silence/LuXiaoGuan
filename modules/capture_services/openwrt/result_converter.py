"""抓包结果验证和格式转换边界。"""

import re
import hashlib
import shutil
import struct
from dataclasses import dataclass, field
from pathlib import Path


# Windows 文件名保留字，不区分大小写。
_WINDOWS_RESERVED_NAMES = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{i}" for i in range(1, 10)]
    + [f"LPT{i}" for i in range(1, 10)]
)

_MAX_STEM_LENGTH = 80
CONVERTIBLE_VERDICTS = frozenset({"complete", "m1m2", "m2m3", "m1m4", "m3m4", "pmkid"})


def safe_result_stem(ssid, bssid):
    """把信号名转换为可安全做 Windows 文件名的词干。

    参数：ssid 是扫描到的信号名（可能为空或隐藏占位）；bssid 是其 MAC。
    返回值：已处理非法字符、保留名、首尾空格圆点和长度的词干；信号名不可用时
    回退到无冒号 BSSID。绝不返回空串。
    """

    text = str(ssid or "").strip()
    if not text or text == "__hidden__":
        text = str(bssid or "").strip().replace(":", "-")
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text)
    text = text.strip().rstrip(". ")
    if not text:
        text = str(bssid or "").strip().replace(":", "-") or "unknown"
    if text.upper() in _WINDOWS_RESERVED_NAMES:
        text = "_" + text
    return text[:_MAX_STEM_LENGTH] or "unknown"


def unique_result_path(output_dir, stem, suffix):
    """在输出目录中返回不重名的最终路径，重名时追加 __2、__3。

    参数：output_dir 是最终产物根目录；stem 是 safe_result_stem 的结果；
    suffix 含点（如 .cap）。不创建文件，只做存在性检查。
    """

    output_dir = Path(output_dir)
    candidate = output_dir / f"{stem}{suffix}"
    counter = 2
    while candidate.exists():
        candidate = output_dir / f"{stem}__{counter}{suffix}"
        counter += 1
    return candidate


def finalize_result_file(temp_path, output_dir, stem, suffix):
    """把会话临时产物搬运为输出根目录下的最终命名文件。

    参数：temp_path 是已下载到本地会话临时目录的产物；output_dir 是用户输出根
    目录，不存在则创建。返回值：最终 Path。副作用：移动文件；输出目录不可写时
    抛异常，临时文件保留以便排查。
    """

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    final_path = unique_result_path(output_dir, stem, suffix)
    shutil.move(str(temp_path), str(final_path))
    return final_path


def converted_output_dir(root):
    """转换产物目录（22000/HCCAPX），不存在则创建并返回。"""

    path = Path(root) / "转换产物"
    path.mkdir(parents=True, exist_ok=True)
    return path


def final_results_dir(root):
    """最终结果目录（密码/PIN 结果 TXT 与会话恢复文件），不存在则创建并返回。"""

    path = Path(root) / "最终结果"
    path.mkdir(parents=True, exist_ok=True)
    return path


# pcap 全局头魔数决定字节序；纳秒变体一并兼容。
_PCAP_MAGICS = {
    b"\xd4\xc3\xb2\xa1": "<",
    b"\xa1\xb2\xc3\xd4": ">",
    b"\x4d\x3c\xb2\xa1": "<",
    b"\xa1\xb2\x3c\x4d": ">",
}

# airodump-ng 写 radiotap（127）或裸 802.11（105）；其他链路类型无法判断。
_PCAP_DLT_RADIO = 127
_PCAP_DLT_80211 = 105

_EAPOL_LLC = b"\xaa\xaa\x03\x00\x00\x00\x88\x8e"

# EAPOL-Key 描述符类型：2 为 RSN，254 为 WPA；不同于 Key Info 的版本位。
_KEY_DESCRIPTOR_IDS = (2, 254)

# Key Information 位（大端）：类型、ACK、MIC、Secure 决定 M1-M4。
_KEY_TYPE_PAIRWISE = 0x0008
_KEY_ACK = 0x0080
_KEY_MIC = 0x0100
_KEY_SECURE = 0x0200

# M1 附带的 PMKID 密钥数据标识：DD 14 00 0F AC 04 + 16 字节 PMKID。
_PMKID_KDE_PREFIX = b"\xdd\x14\x00\x0f\xac\x04"


@dataclass(frozen=True)
class HandshakeAnalysis:
    """一次 CAP 的 EAPOL 结构分析结论。

    status 取值：complete（M1-M4 齐全）、m1m2/m2m3（可跑包的最小集）、pmkid（含可跑包
    PMKID）、partial（零散 EAPOL）、none（确认没有）、unknown（无法判断）。
    detail 是给用户看的一句话；ap/sta 是匹配到的双方 MAC（无匹配为空）。
    结构匹配不验证 MIC（没有密码验不了），最终以跑包为准。
    """

    status: str
    detail: str
    ap: str = ""
    sta: str = ""
    content_complete: bool = True


@dataclass
class _PairFrames:
    """同一对 AP/STA 收集到的各类 EAPOL-Key 帧详情。

    每个 kind 存帧详情字典列表：nonce、replay、mic（16 字节）、eapol（完整
    802.1X 报文）、keyver（密钥描述符版本）；pmkid 为该对是否见过带 PMKID 的
    M1；other 为其他 EAPOL 计数。
    """

    m1: list = field(default_factory=list)
    m2: list = field(default_factory=list)
    m3: list = field(default_factory=list)
    m4: list = field(default_factory=list)
    pmkid: bool = False
    pmkid_hex: str = ""
    other: int = 0


def _format_mac(raw):
    """将 6 字节 MAC 转为大写冒号形式，长度不足返回空串。"""

    if len(raw) != 6:
        return ""
    return ":".join(f"{value:02X}" for value in raw)


def _parse_eapol_key_frame(frame, has_radiotap):
    """解析单个 802.11 帧中的 EAPOL-Key。

    返回值：帧详情字典（含 ap/sta/kind/nonce/replay/mic/eapol/has_pmkid/pmkid_hex）；
    其他类型 EAPOL 返回 {"kind": "other"} 仅记存在性；无关帧返回 None。
    WDS/IBSS 帧不归因；全零 nonce、组密钥、未知描述符一律不计入握手。
    eapol 是从 802.1X 头开始的完整报文（供 22000 用）。
    """

    offset = 0
    if has_radiotap:
        if len(frame) < 4:
            return None
        tap_len = frame[2] | (frame[3] << 8)
        if tap_len <= 0 or tap_len > len(frame):
            return None
        offset = tap_len
    if len(frame) < offset + 24:
        return None
    frame_control = frame[offset] | (frame[offset + 1] << 8)
    if (frame_control >> 2) & 0x03 != 0x02:
        return None
    to_ds = bool(frame_control & 0x0100)
    from_ds = bool(frame_control & 0x0200)
    if to_ds == from_ds:
        return None
    addr1 = frame[offset + 4:offset + 10]
    addr2 = frame[offset + 10:offset + 16]
    if len(addr1) < 6 or len(addr2) < 6:
        return None
    if from_ds:
        ap, sta = bytes(addr2), bytes(addr1)
    else:
        ap, sta = bytes(addr1), bytes(addr2)
    offset += 24
    if (frame_control >> 4) & 0x0F >= 8:
        offset += 2
    if frame[offset:offset + 8] != _EAPOL_LLC:
        return None
    offset += 8
    if len(frame) < offset + 4:
        return {"kind": "other"}
    if frame[offset + 1] != 3:
        return {"kind": "other"}
    eapol_len = (frame[offset + 2] << 8) | frame[offset + 3]
    eapol = bytes(frame[offset:offset + 4 + eapol_len])
    if len(eapol) < 4 + eapol_len:
        return {"kind": "other"}
    key_off = offset + 4
    if eapol_len < 95:
        return {"kind": "other"}
    descriptor = frame[key_off]
    info = (frame[key_off + 1] << 8) | frame[key_off + 2]
    replay = int.from_bytes(frame[key_off + 5:key_off + 13], "big")
    nonce = bytes(frame[key_off + 13:key_off + 45])
    mic = bytes(frame[key_off + 77:key_off + 93])
    # 密钥描述符版本（Key Info 低 3 位）：1=WPA、2=WPA2；HCCAPX 需要原样记录。
    keyver = info & 0x0007
    if descriptor not in _KEY_DESCRIPTOR_IDS or keyver not in (1, 2, 3):
        return {"kind": "other"}
    if (info & _KEY_TYPE_PAIRWISE) == 0:
        return {"kind": "other"}
    ack = bool(info & _KEY_ACK)
    mic_flag = bool(info & _KEY_MIC)
    secure = bool(info & _KEY_SECURE)
    if ack and not mic_flag and not secure:
        kind = "m1"
    elif mic_flag and not ack and not secure:
        kind = "m2"
    elif ack and mic_flag:
        kind = "m3"
    elif mic_flag and not ack and secure:
        kind = "m4"
    else:
        return {"kind": "other"}
    if (kind in {"m1", "m3"}) != from_ds:
        return {"kind": "other"}
    keydata_len = int.from_bytes(eapol[97:99], "big")
    if 99 + keydata_len > len(eapol):
        return {"kind": "other"}
    # Hashcat 单独存 MIC，参与校验的 EAPOL 对应字段必须清零。
    eapol = eapol[:81] + bytes(16) + eapol[97:]
    # M4 的 nonce 字段按协议就是全零，不能按“零 nonce 无效”丢弃；M1/M2/M3
    # 出现全零 nonce 说明不是正常握手帧，仍不计入。
    if nonce == bytes(32) and kind != "m4":
        return {"kind": "other"}
    if mic_flag and mic == bytes(16):
        return {"kind": "other"}
    has_pmkid = False
    pmkid_hex = ""
    if kind == "m1":
        keydata_len = (frame[key_off + 93] << 8) | frame[key_off + 94]
        keydata = frame[key_off + 95:key_off + 95 + keydata_len]
        at = 0
        while at + 2 <= len(keydata):
            end = at + 2 + keydata[at + 1]
            if end > len(keydata):
                break
            if (keydata[at:at + 6] == _PMKID_KDE_PREFIX
                    and any(keydata[at + 6:end])):
                has_pmkid = True
                pmkid_hex = bytes(keydata[at + 6:end]).hex()
                break
            at = end
    return {
        "kind": kind,
        "ap": _format_mac(ap),
        "sta": _format_mac(sta),
        "nonce": nonce,
        "replay": replay,
        "mic": mic,
        "eapol": eapol,
        "keyver": keyver,
        "has_pmkid": has_pmkid,
        "pmkid_hex": pmkid_hex,
    }


def convertible_pairs(pairs):
    """枚举所有可跑包组合，返回 {(ap, sta): verdict}。

    参数 pairs 是 _analyze_detailed 返回的 per-(ap, sta) 帧详情。verdict 取值
    complete/m1m2/m2m3/pmkid；无组合返回空字典。供 PMKID 等多 AP 文件逐个转换。
    """

    found = {}
    for key, entry in (pairs or {}).items():
        selected = _select_best({key: entry})
        if selected is not None:
            found[key] = selected[0]
    return found


def handshake_content_fingerprint(analysis, pairs):
    """返回可完整判断的目标内容指纹；未知返回空串，不将流量字节数当进展。

    忽略包时间戳、无关流量及相同帧重传，只比较解析出的 EAPOL 密钥内容。
    调用方仅保存摘要；空的有效集合也有指纹，可在完整超时后比较两次无结果。
    """
    if analysis.status == "unknown" or not analysis.content_complete:
        return ""
    frames = set()
    for (ap, sta), entry in pairs.items():
        for kind in ("m1", "m2", "m3", "m4"):
            for frame in getattr(entry, kind):
                frames.add((ap, sta, kind, frame["nonce"], frame["replay"],
                            frame["mic"], frame["eapol"], frame["keyver"],
                            frame.get("pmkid_hex", "")))
    return hashlib.sha256(repr(sorted(frames)).encode("ascii")).hexdigest()


def analyze_handshake_cap(cap_path, bssid="", max_bytes=64 * 1024 * 1024):
    """分析 CAP 本地文件中的 EAPOL 握手结构。

    参数：cap_path 是已下载的原始包；bssid 为空时评估全文件最优的一对，
    否则只评估该 AP 参与的帧；max_bytes 是扫描上限，超限只看前部。
    返回值：(HandshakeAnalysis, pairs)，pairs 是 per-(ap, sta) 的帧详情，
    供 22000 生成复用，避免二次走查。纯标准库解析，不依赖外部工具；
    不验证 MIC，最终以跑包为准。
    """

    analysis, pairs = _analyze_detailed(cap_path, bssid, max_bytes)
    return analysis, pairs


def extract_beacon_ssids(cap_path, max_bytes=64 * 1024 * 1024, max_frames=1000000, bssid=""):
    """提取 Beacon、Probe Response、关联及重关联请求中的 UTF-8 SSID。

    可限定目标 BSSID；保留截断前已识别的名称。目标尚未识别时，损坏、
    不支持或超限抛出未知原因，不能用其他网络的名称掩盖目标未知。
    """

    with open(cap_path, "rb") as handle:
        blob = handle.read(max_bytes + 1)
    limited = len(blob) > max_bytes
    blob = blob[:max_bytes]
    found = {}
    target = str(bssid or "").upper()
    conflicts = set()
    unknown = ""
    try:
        if blob[0:4] == b"\x0a\x0d\x0d\x0a":
            frames = _iter_pcapng_frames(blob)
        else:
            frames = _iter_classic_frames(blob)
        for _index, (body, has_radiotap) in enumerate(frames):
            if _index >= max_frames:
                limited = True
                break
            if not body:
                unknown = "链路类型不支持，SSID 内容未知。"
            parsed = _parse_beacon(body, has_radiotap)
            if parsed is not None:
                bssid, ssid = parsed
                if target and bssid != target:
                    continue
                if bssid in conflicts:
                    continue
                if bssid in found and found[bssid] != ssid:
                    conflicts.add(bssid)
                    del found[bssid]
                    unknown = "目标管理帧的 SSID 冲突，请核对真实名称。"
                else:
                    found[bssid] = ssid
    except (struct.error, ValueError):
        if not found:
            raise
    if limited and not found:
        raise ValueError("只扫描了文件前部，SSID 内容未知。")
    if unknown and not found:
        raise ValueError(unknown)
    return found


def _iter_classic_frames(blob):
    """走查 classic pcap 记录，逐个产出 (帧字节, 是否 radiotap)。"""

    endian = _PCAP_MAGICS.get(blob[0:4])
    if endian is None or len(blob) < 24:
        raise ValueError("原始包头无效，SSID 内容未知。")
    try:
        dlt = struct.unpack(endian + "I", blob[20:24])[0] & 0xffff
    except struct.error:
        return
    tap = dlt == _PCAP_DLT_RADIO
    if dlt not in (_PCAP_DLT_RADIO, _PCAP_DLT_80211):
        raise ValueError("链路类型不支持，SSID 内容未知。")
    offset = 24
    while offset + 16 <= len(blob):
        try:
            _ts_sec, _ts_frac, incl_len, _orig_len = struct.unpack(
                endian + "IIII", blob[offset:offset + 16])
        except struct.error:
            return
        body_start = offset + 16
        body_end = body_start + incl_len
        if incl_len <= 0 or incl_len > 100000 or body_end > len(blob) or _orig_len < incl_len:
            raise ValueError("包记录截断或损坏，SSID 内容未知。")
        yield bytes(blob[body_start:body_end]), tap
        offset = body_end
    if offset != len(blob):
        raise ValueError("包尾截断，SSID 内容未知。")


def _parse_beacon(frame, has_radiotap):
    """解析四类携带名称的管理帧，按地址结构核对 BSSID；无效返回 None。"""

    offset = 0
    if has_radiotap:
        if len(frame) < 8 or frame[0] != 0:
            return None
        tap_len = frame[2] | (frame[3] << 8)
        if tap_len < 8 or tap_len > len(frame):
            return None
        offset = tap_len
    if len(frame) < offset + 24:
        return None
    frame_control = frame[offset] | (frame[offset + 1] << 8)
    subtype = (frame_control >> 4) & 0x0F
    fixed = {8: 12, 5: 12, 0: 4, 2: 10}.get(subtype)
    if (frame_control & 0xc70f) or fixed is None or frame[offset + 22] & 0x0f:
        return None
    bssid = bytes(frame[offset + 16:offset + 22])
    if len(bssid) < 6 or bssid == bytes(6) or bssid[0] & 1:
        return None
    address = frame[offset + (10 if subtype in (8, 5) else 4):offset + (16 if subtype in (8, 5) else 10)]
    if address != bssid:
        return None
    cursor = offset + 24 + fixed
    while cursor + 2 <= len(frame):
        tag, length = frame[cursor], frame[cursor + 1]
        if cursor + 2 + length > len(frame):
            return None
        if tag == 0:
            if length == 0 or length > 32:
                return None
            try:
                ssid = bytes(frame[cursor + 2:cursor + 2 + length]).decode("utf-8")
            except UnicodeDecodeError:
                return None
            if not ssid or not any(frame[cursor + 2:cursor + 2 + length]):
                return None
            return _format_mac(bssid), ssid
        cursor += 2 + length
    return None


class _PcapngFormatError(ValueError):
    """pcapng 块结构损坏，调用方转 unknown 结论。"""


class _PcapngTailError(_PcapngFormatError):
    """尾部未写完，不否定前部完整记录。"""


def _frame_by_linktype(data, linktype):
    """按链路类型返回 (帧字节, 是否 radiotap)；不支持的类型返回空帧占位。"""

    if linktype == _PCAP_DLT_RADIO:
        return bytes(data), True
    if linktype == _PCAP_DLT_80211:
        return bytes(data), False
    return b"", False


def _iter_pcapng_frames(blob, with_timestamp=False):
    """解析 pcapng 块，逐个产出 (帧字节, 是否 radiotap)。

    IDB 记链路类型（接口按出现顺序编号）；EPB/PB/SPB 取包。块长度非法或包体
    超出时静默截断（调用方按记录数判断）；字节序魔数不对抛 _PcapngFormatError。
    """

    if len(blob) < 12:
        raise _PcapngFormatError("原始包为空或已损坏。")
    order = struct.unpack("<I", blob[8:12])[0]
    if order == 0x1A2B3C4D:
        endian = "<"
    elif order == 0x4D3C2B1A:
        endian = ">"
    else:
        raise _PcapngFormatError("原始包不是可识别的 pcapng 格式。")
    ifaces = {}
    snaplens = {}
    clocks = {}
    offset = 0
    size = len(blob)
    while offset + 12 <= size:
        if blob[offset:offset + 4] == b"\x0a\x0d\x0d\x0a":
            magic = blob[offset + 8:offset + 12]
            if magic not in (b"\x4d\x3c\x2b\x1a", b"\x1a\x2b\x3c\x4d"):
                raise _PcapngFormatError("pcapng 字节序标记损坏。")
            endian = "<" if magic == b"\x4d\x3c\x2b\x1a" else ">"
            ifaces = {}
            snaplens = {}
            clocks = {}
        try:
            block_type, block_len = struct.unpack(endian + "II", blob[offset:offset + 8])
        except struct.error:
            raise _PcapngFormatError("原始包中部损坏，无法继续判断。")
        if block_len < 12 or block_len % 4:
            raise _PcapngFormatError("pcapng 块长度无效。")
        if offset + block_len > size:
            raise _PcapngTailError("pcapng 尾部未写完。")
        if struct.unpack(endian + "I", blob[offset + block_len - 4:offset + block_len])[0] != block_len:
            raise _PcapngFormatError("pcapng 块尾长度不一致。")
        body = blob[offset + 8:offset + block_len - 4]
        if block_type == 0x00000001 and len(body) >= 8:
            linktype, _reserved, snaplen = struct.unpack(endian + "HHI", body[:8])
            iface_id = len(ifaces)
            snaplens[iface_id] = snaplen
            ifaces[iface_id] = linktype
            resolution, shift = 1e-6, 0
            cursor = 8
            while cursor + 4 <= len(body):
                code, length = struct.unpack(endian + "HH", body[cursor:cursor + 4])
                cursor += 4
                value = body[cursor:cursor + length]
                if len(value) != length:
                    raise _PcapngFormatError("Invalid interface option")
                if code == 0:
                    break
                if code == 9 and length == 1:
                    resolution = (2.0 ** -(value[0] & 127) if value[0] & 128
                                  else 10.0 ** -value[0])
                if code == 14 and length == 8:
                    shift = struct.unpack(endian + "q", value)[0]
                cursor += (length + 3) & ~3
            clocks[iface_id] = resolution, shift
        elif block_type == 0x00000006 and len(body) >= 20:
            iface, _ts_hi, _ts_lo, caplen, _origlen = struct.unpack(endian + "IIIII", body[:20])
            data = body[20:20 + caplen]
            if len(data) < caplen or caplen > _origlen:
                raise _PcapngFormatError("pcapng 包长度无效。")
            if iface not in ifaces:
                raise _PcapngFormatError("pcapng 缺少包所引用的接口。")
            frame = _frame_by_linktype(data, ifaces[iface])
            resolution, shift = clocks[iface]
            timestamp = ((_ts_hi << 32) | _ts_lo) * resolution + shift
            yield (*frame, timestamp) if with_timestamp else frame
        elif block_type == 0x00000002 and len(body) >= 20:
            iface, _drops, _ts_hi, _ts_lo, caplen, _origlen = struct.unpack(endian + "HHIIII", body[:20])
            data = body[20:20 + caplen]
            if len(data) < caplen or caplen > _origlen:
                raise _PcapngFormatError("pcapng 包长度无效。")
            if iface not in ifaces:
                raise _PcapngFormatError("pcapng 缺少包所引用的接口。")
            frame = _frame_by_linktype(data, ifaces[iface])
            resolution, shift = clocks[iface]
            timestamp = ((_ts_hi << 32) | _ts_lo) * resolution + shift
            yield (*frame, timestamp) if with_timestamp else frame
        elif block_type == 0x00000003 and len(body) >= 4:
            (origlen,) = struct.unpack(endian + "I", body[:4])
            caplen = min(origlen, snaplens.get(0, 0) or origlen)
            data = body[4:4 + caplen]
            if len(data) < caplen:
                raise _PcapngFormatError("pcapng 包长度无效。")
            if 0 not in ifaces:
                raise _PcapngFormatError("pcapng 缺少包所引用的接口。")
            frame = _frame_by_linktype(data, ifaces[0])
            yield (*frame, None) if with_timestamp else frame
        offset += block_len
    if offset != size:
        raise _PcapngTailError("pcapng 尾部未写完。")


def _analyze_pcapng_detailed(blob, target, truncated):
    """走查 pcapng 帧并排名，返回 (HandshakeAnalysis, pairs)。

    解析与排名复用 classic 路径同一套函数，结论口径一致；空文件判损坏。
    """

    frames = []
    parse_error = ""
    tail_only = False
    try:
        for frame in _iter_pcapng_frames(blob, with_timestamp=True):
            frames.append(frame)
    except _PcapngFormatError as exc:
        # 增长中的快照可能只有尾块不完整，前部完整组合仍是有效证据。
        parse_error = str(exc)
        tail_only = isinstance(exc, _PcapngTailError)
    pairs = {}
    eapol_total = 0
    records = 0
    for body, radiotap, timestamp in frames:
        parsed = _parse_eapol_key_frame(body, radiotap)
        records += 1
        if parsed is None:
            continue
        if parsed.get("kind") == "other":
            eapol_total += 1
            continue
        ap, sta = parsed["ap"], parsed["sta"]
        if target and ap != target:
            continue
        eapol_total += 1
        entry = pairs.setdefault((ap, sta), _PairFrames())
        getattr(entry, parsed["kind"]).append(
            {
                "nonce": parsed["nonce"],
                "replay": parsed["replay"],
                "mic": parsed["mic"],
                "eapol": parsed["eapol"],
                "keyver": parsed["keyver"],
                "pmkid_hex": parsed["pmkid_hex"],
                "timestamp": timestamp,
            }
        )
        if parsed["has_pmkid"]:
            entry.pmkid = True
            entry.pmkid_hex = entry.pmkid_hex or parsed["pmkid_hex"]
    if truncated and eapol_total == 0:
        return HandshakeAnalysis("unknown", "只扫描了文件前部且未发现 EAPOL，无法判断。"), {}
    best = _rank_pairs(pairs)
    if best is not None:
        return best, pairs
    if parse_error:
        if tail_only and records and not truncated:
            return HandshakeAnalysis("partial" if eapol_total else "none",
                                     "本轮未抓到可用包。", content_complete=False), pairs
        return HandshakeAnalysis("unknown", parse_error), pairs
    if truncated:
        return HandshakeAnalysis(
            "unknown",
            f"只分析了文件前部（超 64MB）：前部共 {records} 条记录、{eapol_total} 帧 EAPOL，"
            "未凑出可跑包组合，无法判断完整结果或无进展。"), pairs
    if any(not body for body, _radiotap, _timestamp in frames):
        return HandshakeAnalysis("unknown", "原始包包含不支持的链路类型，无法完整判断。"), pairs
    if eapol_total > 0:
        return HandshakeAnalysis(
            "partial", "本轮未抓到可用包。"
        ), pairs
    if records == 0:
        return HandshakeAnalysis("unknown", "原始包损坏，无法判断。"), {}
    return HandshakeAnalysis("none", "本轮未抓到可用包。"), pairs


def _analyze_detailed(cap_path, bssid="", max_bytes=64 * 1024 * 1024):
    """走查并排名，返回 (HandshakeAnalysis, pairs)。"""

    target = str(bssid or "").strip().upper()
    try:
        with open(cap_path, "rb") as handle:
            blob = handle.read(max_bytes + 1)
    except OSError:
        return HandshakeAnalysis("unknown", "原始包无法读取。"), {}
    truncated = len(blob) > max_bytes
    blob = blob[:max_bytes]
    if blob[0:4] == b"\x0a\x0d\x0d\x0a":
        return _analyze_pcapng_detailed(blob, target, truncated)
    if len(blob) < 24:
        return HandshakeAnalysis("unknown", "原始包为空或已损坏。"), {}
    endian = _PCAP_MAGICS.get(blob[0:4])
    if endian is None:
        return HandshakeAnalysis("unknown", "原始包不是可识别的 pcap 格式。"), {}
    try:
        dlt = struct.unpack(endian + "I", blob[20:24])[0] & 0xffff
    except struct.error:
        return HandshakeAnalysis("unknown", "原始包文件头损坏。"), {}
    if dlt not in (_PCAP_DLT_RADIO, _PCAP_DLT_80211):
        return HandshakeAnalysis("unknown", "原始包链路类型不支持解析。"), {}
    pairs = {}
    eapol_total = 0
    records = 0
    offset = 24
    incomplete_tail = False
    while offset + 16 <= len(blob):
        try:
            _ts_sec, _ts_frac, incl_len, _orig_len = struct.unpack(
                endian + "IIII", blob[offset:offset + 16]
            )
        except struct.error:
            return HandshakeAnalysis("unknown", "原始包中部损坏，无法继续判断。"), {}
        body_start = offset + 16
        body_end = body_start + incl_len
        if incl_len <= 0 or incl_len > 100000 or _orig_len < incl_len:
            return HandshakeAnalysis("unknown", "原始包中部损坏，无法继续判断。"), {}
        if body_end > len(blob):
            incomplete_tail = True
            break
        parsed = _parse_eapol_key_frame(
            blob[body_start:body_end], dlt == _PCAP_DLT_RADIO
        )
        records += 1
        if parsed is None:
            offset = body_end
            continue
        if parsed.get("kind") == "other":
            eapol_total += 1
            offset = body_end
            continue
        ap, sta = parsed["ap"], parsed["sta"]
        if target and ap != target:
            offset = body_end
            continue
        eapol_total += 1
        entry = pairs.setdefault((ap, sta), _PairFrames())
        getattr(entry, parsed["kind"]).append(
            {
                "nonce": parsed["nonce"],
                "replay": parsed["replay"],
                "mic": parsed["mic"],
                "eapol": parsed["eapol"],
                "keyver": parsed["keyver"],
                "pmkid_hex": parsed["pmkid_hex"],
                "timestamp": _ts_sec + _ts_frac / (1e9 if blob[:4] in (b"\x4d\x3c\xb2\xa1", b"\xa1\xb2\x3c\x4d") else 1e6),
            }
        )
        if parsed["has_pmkid"]:
            entry.pmkid = True
            entry.pmkid_hex = entry.pmkid_hex or parsed["pmkid_hex"]
        offset = body_end
    if truncated and eapol_total == 0:
        return HandshakeAnalysis("unknown", "只扫描了文件前部且未发现 EAPOL，无法判断。"), {}
    best = _rank_pairs(pairs)
    if best is not None:
        return best, pairs
    if truncated:
        return HandshakeAnalysis("unknown", "包文件超出分析上限，本次无法确认结果。"), pairs
    if records and (incomplete_tail or offset != len(blob)):
        return HandshakeAnalysis(
            "partial" if eapol_total else "none",
            "本轮未抓到可用包。", content_complete=False), pairs
    if eapol_total > 0:
        return HandshakeAnalysis(
            "partial", "本轮未抓到可用包。"
        ), pairs
    if records == 0 and incomplete_tail:
        return HandshakeAnalysis("unknown", "原始包损坏，无法判断。"), {}
    return HandshakeAnalysis("none", "本轮未抓到可用包。"), pairs


def _same_exchange(first, second):
    """核对版本和时间关联，防止将不同会话的帧拼成消息对。"""
    if first["keyver"] != second["keyver"]:
        return False
    a, b = first.get("timestamp"), second.get("timestamp")
    return a is None or b is None or abs(a - b) <= 10.0


def _find_m4_pair(frames, kind):
    """仅接受携带非零客户端 nonce 的 M4。"""
    for first in getattr(frames, kind):
        for fourth in frames.m4:
            expected = first["replay"] + (1 if kind == "m1" else 0)
            if (fourth["nonce"] != bytes(32) and fourth["replay"] == expected
                    and _same_exchange(first, fourth)):
                return first, fourth
    return None


def _find_m1m2(frames):
    """在同对帧中找 RC 相等的 M1×M2 组合；找不到返回 None。

    M2 回显 M1 的重放计数，这是挑战对可用的基本条件（更宽松的缺口留给跑包时的
    nonce 纠错，本工具只写严格有效的组合）。
    """

    for first in frames.m1:
        for second in frames.m2:
            if (first["replay"] == second["replay"]
                    and _same_exchange(first, second)):
                return first, second
    return None


def _find_complete(frames):
    """找 M1-M4 完整组合：M1×M2 配对成功，且存在 M3×M4 使 RC3==RC2+1、双方 nonce
    各自一致；找不到返回 None。"""

    pair = _find_m1m2(frames)
    if pair is None:
        return None
    first, second = pair
    for third in frames.m3:
        for fourth in frames.m4:
            if (
                third["replay"] == second["replay"] + 1
                and fourth["replay"] == third["replay"]
                and _same_exchange(second, third)
                and _same_exchange(third, fourth)
                and third["nonce"] == first["nonce"]
                and fourth["nonce"] in (bytes(32), second["nonce"])
            ):
                return first, second, third, fourth
    return None


def _find_m2m3(frames):
    """寻找同版本且 RC3=RC2+1 的 M2/M3；返回 (M3, M2)，供 ANonce/MIC 写出。"""
    for second in frames.m2:
        for third in frames.m3:
            if (third["replay"] == second["replay"] + 1
                    and _same_exchange(third, second)):
                return third, second
    return None


def _select_best(pairs):
    """选出最优可跑包组合，返回 (verdict, ap, sta, frames)。

    verdict 取 complete/m1m2/m2m3/pmkid；frames 是组合用到的帧详情：
    complete 为 (m1, m2, m3, m4)，m1m2 为 (m1, m2)，pmkid 为 (m1_with_pmkid,)。
    选不出返回 None。
    """

    best_pmkid = None
    for (ap, sta), entry in pairs.items():
        combo = _find_complete(entry)
        if combo is not None:
            first, second, third, fourth = combo
            return ("complete", ap, sta, (first, second, third, fourth))
        pair = _find_m1m2(entry)
        if pair is not None:
            return ("m1m2", ap, sta, pair)
        pair = _find_m2m3(entry)
        if pair is not None:
            return ("m2m3", ap, sta, pair)
        for kind, verdict in (("m1", "m1m4"), ("m3", "m3m4")):
            pair = _find_m4_pair(entry, kind)
            if pair is not None:
                return verdict, ap, sta, pair
        if entry.pmkid and best_pmkid is None:
            pmkid_frame = next(
                (frame for frame in entry.m1 if frame.get("pmkid_hex")), None
            )
            if pmkid_frame is not None:
                best_pmkid = ("pmkid", ap, sta, (pmkid_frame,))
    return best_pmkid


def _rank_pairs(pairs):
    """从多对 AP/STA 中选出最优结论，complete > m1m2 > pmkid。"""

    selected = _select_best(pairs)
    if selected is None:
        return None
    verdict, ap, sta, _frames = selected
    short = f"{ap} 与 {sta}"
    if verdict == "complete":
        return HandshakeAnalysis(
            "complete",
            f"抓到完整四次握手（M1-M4，{short}），可以跑包，最终以跑包为准。",
            ap,
            sta,
        )
    if verdict == "m1m2":
        return HandshakeAnalysis(
            "m1m2",
            f"抓到 M1+M2（{short}），可以跑包，最终以跑包为准。",
            ap,
            sta,
        )
    if verdict == "m2m3":
        return HandshakeAnalysis(
            "m2m3",
            f"抓到 M2+M3（{short}），可转换为 22000，最终以跑包为准。",
            ap,
            sta,
        )
    if verdict in {"m1m4", "m3m4"}:
        return HandshakeAnalysis(verdict, "已抓到可用消息对，可生成 22000。", ap, sta)
    return HandshakeAnalysis(
        "pmkid",
        f"发现 PMKID（{short}），可以跑包，最终以跑包为准。",
        ap,
        sta,
    )


def _flat_mac(mac):
    """去掉冒号并小写，供 22000 行使用。"""

    return str(mac or "").replace(":", "").lower()


def _eapol_22000_line(ssid, ap, sta, first, second, pair):
    """构造 WPA*02 行：MIC/EAPOL 取自 M2，ANonce 取自 M1 或 M3。"""

    return "*".join(
        [
            "WPA",
            "02",
            bytes(second["mic"]).hex(),
            _flat_mac(ap),
            _flat_mac(sta),
            bytes(str(ssid or "").encode("utf-8", errors="replace")).hex(),
            bytes(first["nonce"]).hex(),
            bytes(second["eapol"]).hex(),
            pair,
        ]
    )


def _pmkid_22000_line(ssid, ap, sta, pmkid_hex):
    """构造 WPA*01 行：PMKID 来自 M1 密钥数据。"""

    return "*".join(
        [
            "WPA",
            "01",
            pmkid_hex.lower(),
            _flat_mac(ap),
            _flat_mac(sta),
            bytes(str(ssid or "").encode("utf-8", errors="replace")).hex(),
            "",
            "",
            "",
        ]
    )


def iter_22000_lines(pairs, ssid, check):
    """从已解析帧枚举严格消息对和全部 PMKID；调用方负责有界收集与精确去重。

    不重读原始包；每个组合检查取消/期限，同一 AP/STA 的不同握手均保留。
    """
    for (ap, sta), entry in pairs.items():
        for first in entry.m1:
            check()
            if first.get("pmkid_hex"):
                yield _pmkid_22000_line(ssid, ap, sta, first["pmkid_hex"])
        for second in entry.m2:
            for first in entry.m1:
                check()
                if first["replay"] == second["replay"] and _same_exchange(first, second):
                    yield _eapol_22000_line(ssid, ap, sta, first, second, "00")
            for third in entry.m3:
                check()
                if third["replay"] == second["replay"] + 1 and _same_exchange(third, second):
                    yield _eapol_22000_line(ssid, ap, sta, third, second, "02")
        for kind, mp in (("m1", "01"), ("m3", "05")):
            for first in getattr(entry, kind):
                for fourth in entry.m4:
                    check()
                    expected = first["replay"] + (1 if kind == "m1" else 0)
                    if (fourth["nonce"] != bytes(32) and fourth["replay"] == expected
                            and _same_exchange(first, fourth)):
                        yield _eapol_22000_line(ssid, ap, sta, first, fourth, mp)


def write_22000(cap_path, ssid, bssid, out_path, max_bytes=64 * 1024 * 1024):
    """把 CAP 中的可跑包组合写成 Hashcat 22000 文件。

    参数：ssid 是目标信号名（隐藏或空则拒绝，跑包离不开 SSID）；bssid 限定目标
    AP；out_path 是输出路径。返回值：输出 Path。行格式遵循 hashcat 官方规范
    （WPA*02/*01 九字段）；M1-M4 齐全时额外写 M2+M3 授权行。无可跑包组合时抛
    RuntimeError，调用方保留原始包。
    """

    ssid_names = ssid if isinstance(ssid, dict) else None
    ssid_text = str(ssid or "") if ssid_names is None else ""
    if ssid_names is None and (not ssid_text or ssid_text == "__hidden__"):
        raise RuntimeError("SSID 未知，无法生成 22000（跑包需要 SSID）。")
    if len(ssid_text.encode("utf-8")) > 32:
        raise RuntimeError("SSID 超过 32 字节，请核对真实信号名。")
    analysis, pairs = _analyze_detailed(cap_path, bssid, max_bytes)
    if analysis.status == "unknown":
        raise RuntimeError(f"原始包无法解析，不能生成 22000：{analysis.detail}")
    lines = []
    for (ap, sta), entry in pairs.items():
        if str(ap).upper() != str(bssid or "").strip().upper() and bssid:
            continue
        if ssid_names is not None:
            ssid_text = ssid_names.get(str(ap).upper(), "")
            if not ssid_text or ssid_text == "__hidden__" or len(ssid_text.encode("utf-8")) > 32:
                continue
        selected = _select_best({(ap, sta): entry})
        if selected is None:
            continue
        verdict, _ap, _sta, frames = selected
        if verdict == "pmkid":
            lines.append(_pmkid_22000_line(ssid_text, ap, sta, frames[0]["pmkid_hex"]))
        else:
            message_pair = {"complete": "00", "m1m2": "00", "m2m3": "02", "m1m4": "01", "m3m4": "05"}[verdict]
            lines.append(_eapol_22000_line(ssid_text, ap, sta, frames[0], frames[1], message_pair))
            if verdict == "complete":
                lines.append(_eapol_22000_line(ssid_text, ap, sta, frames[2], frames[1], "02"))
    seen = []
    for line in lines:
        if line not in seen:
            seen.append(line)
    if not seen:
        raise RuntimeError("包中没有可跑包组合（需要 M1+M2、M2+M3 或 PMKID），不生成 22000。")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(seen) + "\n", encoding="ascii")
    return out_path


def _mac_bytes(text):
    """把冒号分隔的 MAC 字符串转成 6 字节；格式非法时抛 ValueError。"""

    return bytes.fromhex(str(text or "").replace(":", "").replace("-", ""))


def normalize_hccapx_record(record):
    """将旧 WinBox 的 HCCA 记录恢复为标准布局，返回字节串，不改源文件。

    仅修复已发布的版本 4、M1+M2 布局；结构不符抛错，其他签名交由导入校验。
    """

    if record[:4] != b"HCCA":
        return record
    if len(record) != 393:
        raise ValueError("旧 WinBox HCCAPX 记录不足 393 字节，请从原始包重新转换。")
    signature, version, pair, length, ssid, keyver, mic, ap, sta, anonce, snonce, size, eapol = struct.unpack(
        "<4sIBB32sB16s6s6s32s32sH256s", record)
    if (version != 4 or pair != 0 or not 1 <= length <= 32
            or keyver not in (1, 2, 3) or not 99 <= size <= 256
            or int.from_bytes(eapol[2:4], "big") + 4 != size
            or not any(snonce) or eapol[17:49] != snonce):
        raise ValueError("旧 WinBox HCCAPX 结构无效，请从原始包重新转换。")
    eapol = eapol[:81] + bytes(16) + eapol[97:]
    return struct.pack("<4sIBB32sB16s6s32s6s32sH256s", b"HCPX", version, pair,
                       length, ssid, keyver, mic, ap, anonce, sta, snonce, size, eapol)


def write_hccapx(cap_path, ssid, bssid, out_path, max_bytes=64 * 1024 * 1024):
    """把 CAP 中的 M1+M2 组合写成 Hashcat HCCAPX 文件（兼容格式，非主格式）。

    参数：ssid 是目标信号名（隐藏或空则拒绝，跑包离不开 SSID）；bssid 限定目标
    AP；out_path 是输出路径。返回值：输出 Path。每条记录 393 字节
    （签名 HCCAPX、版本 4、message_pair 固定 0 即 M1+M2 取 M2 的 EAPOL）。
    只有 M1+M2 配对成功的组合可写：纯 PMKID 或 EAPOL 超 256 字节的跳过；
    一对 AP/STA 都写不出时抛 RuntimeError，调用方保留原始包。
    """

    ssid_bytes = str(ssid or "").encode("utf-8")
    if not ssid_bytes or ssid_bytes == b"__hidden__":
        raise RuntimeError("SSID 未知，无法生成 HCCAPX（跑包需要 SSID）。")
    if len(ssid_bytes) > 32:
        raise RuntimeError("SSID 超过 32 字节，HCCAPX 装不下。")
    analysis, pairs = _analyze_detailed(cap_path, bssid, max_bytes)
    if analysis.status == "unknown":
        raise RuntimeError(f"原始包无法解析，不能生成 HCCAPX：{analysis.detail}")
    records = []
    skipped_eapol = 0
    for (ap, sta), entry in pairs.items():
        if str(ap).upper() != str(bssid or "").strip().upper() and bssid:
            continue
        combo = _find_complete(entry)
        if combo is not None:
            first, second = combo[0], combo[1]
        else:
            found = _find_m1m2(entry)
            if found is None:
                continue
            first, second = found
        eapol = bytes(second["eapol"])
        if len(eapol) > 256:
            skipped_eapol += 1
            continue
        keyver = int(second.get("keyver", 0) or 0)
        if keyver not in (1, 2, 3):
            keyver = 2
        records.append(
            struct.pack(
                "<4sIBB32sB16s6s32s6s32sH256s",
                b"HCPX",
                4,
                0,
                len(ssid_bytes),
                ssid_bytes.ljust(32, b"\x00"),
                keyver,
                bytes(second["mic"]),
                _mac_bytes(ap),
                bytes(first["nonce"]),
                _mac_bytes(sta),
                bytes(second["nonce"]),
                len(eapol),
                eapol.ljust(256, b"\x00"),
            )
        )
    seen = []
    for record in records:
        if record not in seen:
            seen.append(record)
    if not seen:
        reason = "M2 的 EAPOL 超长" if skipped_eapol else "需要 M1+M2（纯 PMKID 写不出 hccapx）"
        raise RuntimeError(f"包中没有可写 HCCAPX 的组合（{reason}），不生成 HCCAPX。")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(b"".join(seen))
    return out_path


class CaptureResultConverter:
    """验证 PCAPNG/CAP并生成 22000；转换失败时不得删除原始包。

    converter_path 可为空，表示当前仅保留原始包。架构阶段不假定存在不受官方支持的
    Windows hcxtools 二进制，后续可接远端转换器或经过验证的外部工具。
    """

    def __init__(self, converter_path=""):
        self.converter_path = str(converter_path or "")

    @property
    def available(self):
        """返回是否已配置转换器路径；不检查或执行文件。"""

        return bool(self.converter_path)
