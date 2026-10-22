"""Bounded profile parsing and explicit, stable-per-save station identities."""
from dataclasses import dataclass
import re
import secrets

_MAC=re.compile(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}")
_HOST=re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")

@dataclass(frozen=True)
class IdentityProfile:
    label: str
    hostname: str
    prefix: str

def valid_mac(value):
    return isinstance(value,str) and bool(_MAC.fullmatch(value)) and not int(value[:2],16)&1 and value.upper()!="00:00:00:00:00:00"

def parse_profiles(text):
    if len(text.encode("utf-8"))>262144: raise ValueError("Identity list too large")
    profiles=[];rejected=[];seen=set()
    for number,line in enumerate(text.splitlines(),1):
        line=line.strip()
        if not line or line.startswith("#"):continue
        if len(profiles)>=2048:raise ValueError("Too many identity profiles")
        label,sep,prefix=line.rpartition("@")
        if not sep or not label or len(label)>128 or any(ord(c)<32 for c in label) or not re.fullmatch(r"(?:[0-9A-Fa-f]{2}:){2}[0-9A-Fa-f]{2}",prefix) or int(prefix[:2],16)&1:
            rejected.append(number);continue
        hostname=re.sub(r"[^A-Za-z0-9-]+","-",label).strip("-")[:63].rstrip("-")
        if not hostname:rejected.append(number);continue
        key=(label,prefix.upper())
        if key not in seen:profiles.append(IdentityProfile(label,hostname,prefix.upper()));seen.add(key)
    return tuple(profiles),tuple(rejected)

def generate_mac(prefix=None):
    if prefix is None:
        raw=bytearray(secrets.token_bytes(6));raw[0]=(raw[0]|2)&254
        return ":".join(f"{x:02X}" for x in raw)
    if not re.fullmatch(r"(?:[0-9A-Fa-f]{2}:){2}[0-9A-Fa-f]{2}",prefix) or int(prefix[:2],16)&1:raise ValueError("Invalid unicast prefix")
    return prefix.upper()+":"+":".join(f"{x:02X}" for x in secrets.token_bytes(3))

def validate_identity(options):
    action=options.get("identity_action","keep")
    if action not in ("keep","set","restore","default"):raise ValueError("无效的设备身份选项。")
    if action=="set" and not valid_mac(options.get("macaddr")):raise ValueError("请填写有效的单播 MAC 地址。")
    if action!="set" and ("macaddr" in options or "hostname" in options):raise ValueError("身份参数与操作不一致。")
    if "hostname" in options and (not isinstance(options["hostname"], str) or not _HOST.fullmatch(options["hostname"])):raise ValueError("设备名称需要 1 到 63 位英文字母、数字或连字符，首尾不能是连字符。")
    if options.get("bridge_mode")=="wds" and "hostname" in options:raise ValueError("WDS 共用局域网，不修改共享网络的设备名称。")
