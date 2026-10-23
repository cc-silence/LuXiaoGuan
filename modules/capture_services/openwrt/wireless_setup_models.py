"""无线设置的数据边界；密码不参与对象展示、日志和持久化。"""
from dataclasses import dataclass, field
import ipaddress
import re
from .wireless_security import encryption_base
from .wireless_identity import validate_identity
from .wireless_options import IFACE_KEYS, RADIO_KEYS, validate_options


@dataclass(frozen=True)
class WirelessNetwork:
    """一次原生扫描发现的接入点，绑定实际扫描网卡。"""
    radio: str
    bssid: str
    ssid: str
    channel: int | None
    band: str
    power: int | None
    encryption: str
    reason: str = ""


@dataclass(frozen=True)
class WirelessEnvironment:
    """当前连接的检测结果；就绪仅代表管理环境，不代表真实连接已验证。"""
    missing: tuple[str, ...] = ()
    issues: tuple[str, ...] = ()
    data: dict = field(default_factory=dict)
    fingerprint: str = ""

    @property
    def ready(self):
        return not self.missing and not self.issues and bool(self.data)


@dataclass(frozen=True)
class WirelessRequest:
    """冻结一次保存请求；字段白名单必须在界面和执行器两处一致校验。"""
    mode: str
    radio: str
    ssid: str
    password: str = field(default="", repr=False)
    section: str = ""
    label: str = ""
    options: dict = field(default_factory=dict)
    verification: str = field(default="", repr=False)
    revision: str = ""


@dataclass
class StoredWirelessPassword:
    password: str = field(default="", repr=False)

    def clear(self):
        self.password = ""


@dataclass(frozen=True)
class WirelessTaskResult:
    """普通结果不含密码；按需读取的密码结果投递后立即清空。"""
    action: str
    value: object = None
    error: str = ""
    code: str = ""


@dataclass(frozen=True)
class WirelessState:
    """页面可回放的环境、扫描和任务状态，不保存输入密码。"""
    environment: WirelessEnvironment | None = None
    networks: tuple[WirelessNetwork, ...] = ()
    busy: bool = False
    action: str = ""
    detail: str = "登录后自动检测无线设置环境。"
    pending_changes: bool = False
    scan_radio: str = ""


_NAMES = re.compile(r"[A-Za-z0-9_]+")
_MAC = re.compile(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}")
AP_OPTIONS = frozenset(IFACE_KEYS + RADIO_KEYS + ("encryption", "hidden", "isolate", "maxassoc", "channel", "htmode", "country", "txpower", "network", "wmm", "ieee80211w", "dtim_period", "beacon_int", "wds"))
STA_OPTIONS = frozenset(("encryption", "bssid", "proto", "ipaddr", "netmask", "gateway", "dns", "metric", "zone", "channel", "bridge_mode", "network", "relay_ip", "identity_action", "macaddr", "hostname"))


def validate_request(request):
    """拒绝额外字段、无效编码和危险配置值；不执行任何远端动作。"""
    if request.mode not in ("ap", "sta") or not _NAMES.fullmatch(request.radio):
        raise ValueError("无线网卡或模式无效，请重新读取配置。")
    if request.section and not _NAMES.fullmatch(request.section):
        raise ValueError("不能稳定定位这条配置，请刷新后重试。")
    if not request.ssid or len(request.ssid.encode("utf-8")) > 32 or any(ord(c) < 32 for c in request.ssid):
        raise ValueError("Wi-Fi 名称需要为 1 到 32 个 UTF-8 字节，不能含换行或控制字符。")
    if len(request.label) > 128 or any(ord(c) < 32 for c in request.label):
        raise ValueError("备注最多 128 个字符，不能含控制字符。")
    if request.revision and not re.fullmatch(r"[a-fA-F0-9]{192}", request.revision):
        raise ValueError("配置版本标记无效，请重新打开设置。")
    allowed = AP_OPTIONS if request.mode == "ap" else STA_OPTIONS
    if not isinstance(request.options, dict) or set(request.options) - allowed:
        raise ValueError("存在不支持的设置项，未提交配置。")
    encryption = request.options.get("encryption", "psk2")
    base = encryption_base(encryption)
    password = request.password
    if password or not request.section:
        length = len(password.encode("utf-8"))
        if base not in ("none", "owe") and not (8 <= length <= 63 or base in ("psk", "psk2", "psk-mixed") and re.fullmatch(r"[0-9A-Fa-f]{64}", password)):
            raise ValueError("请输入 8 到 63 字节的密码；WPA2 也支持 64 位十六进制密钥。")
    if any(ord(c) < 32 or ord(c) == 127 for c in password):
        raise ValueError("密码不能含换行或控制字符。")
    options = request.options
    validate_options(options)
    if request.mode == "sta":
        validate_identity(options)
    for key in ("hidden", "isolate", "wmm", "wds"):
        if key in options and options[key] not in ("0", "1"):
            raise ValueError("无线开关值无效。")
    if "ieee80211w" in options and options["ieee80211w"] not in ("0", "1", "2"):
        raise ValueError("802.11w 管理帧保护值无效。")
    if base in ("sae", "owe") and options.get("ieee80211w") in ("0", "1"):
        raise ValueError("WPA3-SAE 需要强制 802.11w 管理帧保护。")
    for key, low, high in (("dtim_period", 1, 255), ("beacon_int", 15, 65535), ("maxassoc", 1, 512), ("metric", 0, 65535), ("txpower", 0, 40)):
        if key in options and (not isinstance(options[key], str) or not options[key].isascii() or not options[key].isdigit() or not low <= int(options[key]) <= high):
            raise ValueError("数字设置超出支持范围：" + key)
    if "channel" in options and options["channel"] != "auto" and not (isinstance(options["channel"], str) and options["channel"].isascii() and options["channel"].isdigit()):
        raise ValueError("无线信道无效。")
    if "htmode" in options and not re.fullmatch(r"(?:NOHT|(?:HT|VHT|HE|EHT)(?:20|40|80|160|320|80\+80))", str(options["htmode"])):
        raise ValueError("无线带宽模式无效。")
    if "country" in options and not re.fullmatch(r"(?:[A-Z]{2}|00)", str(options["country"])):
        raise ValueError("国家代码无效。")
    for key in ("network", "zone"):
        if key in options and not _NAMES.fullmatch(str(options[key])):
            raise ValueError("网络或防火墙区域名称无效。")
    if options.get("bssid") and not _MAC.fullmatch(str(options["bssid"])):
        raise ValueError("BSSID 无效。")
    if options.get("bridge_mode", "routed") not in ("routed", "wds", "relayd"):
        raise ValueError("Unsupported wireless bridge mode")
    if options.get("bridge_mode") in ("wds", "relayd") and not options.get("network"):
        raise ValueError("Select an existing bridge network")
    if options.get("relay_ip"):
        ipaddress.IPv4Address(options["relay_ip"])
    if "proto" in options and options["proto"] not in ("dhcp", "static"):
        raise ValueError("地址获取方式无效。")
    if options.get("proto") == "static":
        ipaddress.IPv4Address(options.get("ipaddr", ""))
        ipaddress.IPv4Network("0.0.0.0/" + options.get("netmask", ""))
    if options.get("gateway"):
        ipaddress.IPv4Address(options["gateway"])
    for key in ("dns",):
        if options.get(key):
            for address in str(options[key]).split():
                ipaddress.ip_address(address)
    return request


def parse_scan(rows, radio, *, channels=(), on_evidence=None):
    """Normalize old/new iwinfo formats without inferring a band from radio numbers."""
    from .wireless_scan_results import parse_scan_results
    return parse_scan_results(rows, radio, channels=channels, on_evidence=on_evidence)
