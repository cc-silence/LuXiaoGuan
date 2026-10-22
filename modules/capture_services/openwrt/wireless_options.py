"""Shared typed whitelist for optional OpenWrt wireless settings.

Unset fields preserve device values. This module contains no UI or transport code.
"""
from dataclasses import dataclass
import re

@dataclass(frozen=True)
class Option:
    key: str
    label: str
    kind: str = "bool"
    low: int = 0
    high: int = 1
    feature: str = ""
    scope: str = "iface"
    help: str = ""
    choices: tuple = ()

AP_ADVANCED = (
    Option("macfilter", "设备访问规则", "enum", choices=(("关闭过滤", "disable"), ("仅允许列表", "allow"), ("拒绝列表", "deny"))),
    Option("maclist", "设备地址列表", "maclist", help="用空格分隔设备 MAC 地址。选择“仅允许列表”前确认管理设备在列表中。"),
    Option("wds", "WDS 桥接", help="允许支持 WDS 的下级设备以透明桥接方式接入；需要 mac80211 驱动。"),
    Option("multicast_to_unicast", "多播转单播"),
    Option("disassoc_low_ack", "断开弱信号设备"),
    Option("short_preamble", "短前导码"),
    Option("skip_inactivity_poll", "跳过空闲探测"),
    Option("max_inactivity", "空闲超时（秒）", "int", 1, 86400),
    Option("ieee80211r", "802.11r 快速漫游", feature="ap_11r"),
    Option("mobility_domain", "漫游域", "hex4", feature="ap_11r", help="同一漫游网络使用相同的 4 位十六进制值；需与其他接入点配合。"),
    Option("ft_over_ds", "通过分布系统漫游", feature="ap_11r"),
    Option("ft_psk_generate_local", "本地生成漫游密钥", feature="ap_11r"),
    Option("ieee80211k", "802.11k 漫游辅助", feature="ap_eap"),
    Option("rrm_neighbor_report", "邻居接入点报告", feature="ap_eap"),
    Option("rrm_beacon_report", "信标测量报告", feature="ap_eap"),
    Option("bss_transition", "802.11v 切换建议", feature="ap_eap"),
    Option("distance", "传输距离（米）", "int", 0, 114750, scope="radio"),
    Option("rts", "RTS 阈值", "int", 0, 2347, scope="radio"),
    Option("frag", "分片阈值", "int", 256, 2346, scope="radio"),
)
IFACE_KEYS = tuple(o.key for o in AP_ADVANCED if o.scope == "iface")
RADIO_KEYS = tuple(o.key for o in AP_ADVANCED if o.scope == "radio")

def validate_options(options):
    for spec in AP_ADVANCED:
        if spec.key not in options: continue
        value = options[spec.key]
        valid = isinstance(value, str)
        if spec.kind == "bool": valid = valid and value in ("0", "1")
        elif spec.kind == "int": valid = valid and value.isascii() and value.isdigit() and spec.low <= int(value) <= spec.high
        elif spec.kind == "enum": valid = valid and value in {v for _,v in spec.choices}
        elif spec.kind == "maclist": valid = valid and 0 < len(value.split()) <= 512 and all(re.fullmatch(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", mac) for mac in value.split())
        elif spec.kind == "hex4": valid = valid and bool(re.fullmatch(r"[0-9A-Fa-f]{4}", value))
        if not valid: raise ValueError("无效的无线参数：" + spec.label)
