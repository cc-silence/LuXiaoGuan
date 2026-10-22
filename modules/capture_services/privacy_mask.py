"""隐私掩码纯函数；表格与播报共用，不依赖 Qt。"""

MASKED_TEXT = "••••••"


def mask_text(value, masked):
    """掩码开关打开时返回固定圆点，否则返回原文；不泄露原长度。"""
    return MASKED_TEXT if masked else str(value or "")


def mask_target(ssid, bssid, mask_ssid, mask_bssid):
    """组合显示名掩码：SSID 位用 SSID 开关，空 SSID 回退看 BSSID 开关。"""
    if ssid:
        return mask_text(ssid, mask_ssid)
    return mask_text(bssid, mask_bssid)
