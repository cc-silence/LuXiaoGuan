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
    Option("macfilter", "MAC 地址过滤", "enum", choices=(("关闭过滤", "disable"), ("仅允许列表内", "allow"), ("仅允许列表外", "deny"))),
    Option("maclist", "MAC 列表", "maclist", help="每项填写一个设备 MAC 地址，使用加减按钮增删。选择“仅允许列表内”前确认管理设备在列表中。"),
    Option("wds", "WDS 桥接", help="允许支持 WDS 的下级设备以透明桥接方式接入；需要 mac80211 驱动。"),
    Option("multicast_to_unicast", "组播转单播", help="按本机无线脚本支持的原始选项处理，不替换成含义不同的参数。"),
    Option("multicast_to_unicast_all", "组播转单播", help="将 ARP、IPv4 和 IPv6 的组播帧转换为发给各无线终端的单播帧；可能影响终端对组播的处理。"),
    Option("disassoc_low_ack", "在低 Ack 应答时断开连接", help="勾选时，可因大量发送失败或收不到确认回复而断开无线终端；取消勾选可减少这类主动断开，但不能修复干扰或实际失联，效果取决于驱动支持。"),
    Option("short_preamble", "短前导码"),
    Option("skip_inactivity_poll", "禁用不活动轮询", help="勾选表示跳过空闲后的存活探测，达到空闲期限时更容易断开终端；取消勾选表示先探测终端是否仍在线。此项不是禁止空闲断线。"),
    Option("max_inactivity", "非活动站点限制", "int", 1, 86400, help="单位：秒。终端无活动多久后开始空闲检查；通常默认 300 秒。86400 为 24 小时，不是永不断开。是否先探测取决于“禁用不活动轮询”。"),
    Option("ieee80211r", "802.11r 快速切换", feature="ap_11r", help="让设备在同一漫游网络的接入点之间更快切换；需要终端与其他接入点共同支持。"),
    Option("mobility_domain", "移动域", "hex4", feature="ap_11r", help="同一漫游网络使用相同的 4 位十六进制值；需与其他接入点配合。"),
    Option("ft_over_ds", "FT 协议", "enum", feature="ap_11r", choices=(("FT over the Air", "0"), ("FT over DS", "1")), help="FT over the Air 通过无线链路执行快速切换；FT over DS 通过分布系统执行。需与漫游网络的接入点配合。"),
    Option("ft_psk_generate_local", "本地生成 PMK", feature="ap_11r", help="PMK 是用于认证的主密钥。使用预共享密码时，可在本机生成漫游所需密钥；需与漫游网络配置配合。"),
    Option("ieee80211k", "802.11k RRM", feature="ap_eap", help="RRM 是无线资源测量，为支持的终端提供邻居与信标测量信息，辅助选择接入点。"),
    Option("rrm_neighbor_report", "邻居报告", feature="ap_eap", help="向支持 802.11k 的终端提供附近接入点信息，辅助漫游。"),
    Option("rrm_beacon_report", "信标报告", feature="ap_eap", help="启用 802.11k 信标测量报告，辅助了解附近接入点的无线状况。"),
    Option("bss_transition", "BSS 切换", feature="ap_eap", help="BSS 指无线基本服务集。启用 802.11v 切换管理，向支持的终端提供接入点切换建议；最终是否切换由终端决定。"),
    Option("distance", "距离优化", "int", 0, 114750, scope="radio", help="单位：米。到最远网络成员的距离；仅在超过一公里时设置，普通室内使用请沿用原配置。"),
    Option("rts", "RTS/CTS 阈值", "int", 0, 2347, scope="radio"),
    Option("frag", "分片阈值", "int", 256, 2346, scope="radio"),
)
IFACE_KEYS = tuple(o.key for o in AP_ADVANCED if o.scope == "iface")
RADIO_KEYS = ("beacon_int",) + tuple(o.key for o in AP_ADVANCED if o.scope == "radio")

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
