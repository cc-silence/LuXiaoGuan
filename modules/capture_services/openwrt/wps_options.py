"""Shared WPS source-address preparation and validation; no device mutations."""
from secrets import token_bytes

from .command_builder import normalize_bssid


def spoof_mac_for(options):
    if not bool(options.get("spoof_mac_enabled", False)):
        return ""
    try:
        address = normalize_bssid(options.get("spoof_mac", ""))
        octets = bytes.fromhex(address.replace(":", ""))
        if octets[0] & 1 or not any(octets):
            raise ValueError("Invalid source address")
    except (ValueError, TypeError):
        raise ValueError('\u4f2a\u88c5 MAC \u683c\u5f0f\u65e0\u6548\uff0c\u8bf7\u586b\u5199\u6709\u6548\u7684\u5355\u64ad\u7f51\u5361\u5730\u5740\uff1b\u6e05\u7a7a\u53ef\u81ea\u52a8\u751f\u6210\uff0c\u4e5f\u53ef\u53d6\u6d88\u201c\u542f\u7528\u4f2a\u88c5 MAC\u201d\u3002') from None
    return address


def random_source_mac():
    """Generate a locally administered unicast address with OS randomness."""
    octets = bytearray(token_bytes(6))
    octets[0] = (octets[0] & 0xFC) | 0x02
    return ":".join(f"{value:02X}" for value in octets)


def prepare_wps_options(options, *, mode=None):
    """Resolve an empty enabled source once; preserve caller-owned settings."""
    prepared = dict(options or {})
    if mode == "second":
        from .wps_defaults import validate_quick_options
        validate_quick_options(prepared)
    if prepared.get("rotate_mac_each_pin", False):
        if not prepared.get("spoof_mac_enabled", False):
            raise ValueError("\u9010\u7801\u968f\u673a\u9700\u8981\u542f\u7528\u4f2a\u88c5 MAC\u3002")
        if mode not in (None, "normal", "second"):
            raise ValueError("\u9010\u7801\u968f\u673a\u9002\u7528\u4e8e\u5e38\u89c4 Pin \u548c\u9ed8\u8ba4 PIN \u901f\u6d4b\uff0c\u79bb\u7ebf\u8ba1\u7b97\u4e0d\u9002\u7528\u3002")
        if {"-g", "-q"}.intersection(str(prepared.get("additional_args", "")).split()):
            raise ValueError("\u9010\u7801\u968f\u673a\u7531\u7a0b\u5e8f\u7ba1\u7406\u5355\u6b21\u5c1d\u8bd5\u8fb9\u754c\uff0c\u8bf7\u79fb\u9664\u9644\u52a0\u53c2\u6570 -g / -q\u3002")
        if not prepared.get("_rotation_source_prepared", False):
            prepared["spoof_mac"] = random_source_mac()
            prepared["_rotation_source_prepared"] = True
    if prepared.get("spoof_mac_enabled", False):
        if not str(prepared.get("spoof_mac", "") or "").strip():
            prepared["spoof_mac"] = random_source_mac()
        prepared["spoof_mac"] = spoof_mac_for(prepared)
    return prepared
