"""Shared identities for two independent offline dependency packages."""
from dataclasses import dataclass
from .wireless_dependencies import WIRELESS_HELPER_PACKAGES

COMMON_ROOTS = frozenset((
    "openssh-sftp-server", "tmux", "iw", "iwinfo", "luci-app-commands",
    "luci-i18n-base-zh-cn", "luci-i18n-commands-zh-cn",
    *WIRELESS_HELPER_PACKAGES,
))
CAPTURE_ROOTS = frozenset(("aircrack-ng", "hcxdumptool", "reaver", "iw", "ip-tiny"))

@dataclass(frozen=True)
class BundleSpec:
    """\u96c6\u4e2d\u5b9a\u4e49\u4e0b\u8f7d\u3001\u5b58\u50a8\u548c\u7b7e\u540d\u8fb9\u754c\uff0c\u9632\u6b62\u4e24\u79cd\u5305\u6df7\u7528\u3002"""
    kind: str
    title: str
    roots: frozenset
    @property
    def namespace(self): return "openwrt-" + self.kind
    @property
    def purpose(self): return "openwrt-" + self.kind + "-offline"
    @property
    def catalog_purpose(self): return self.kind + "-router-tools"
    @property
    def filename_prefix(self): return "RouterManager_OpenWrt_" + self.kind.title() + "_Offline_"
    @property
    def directory(self): return "openwrt_" + self.kind
    @property
    def service_attr(self): return self.kind + "_bundle_download"

SPECS = {
    "common": BundleSpec("common", "\u5e38\u7528\u4f9d\u8d56", COMMON_ROOTS),
    "capture": BundleSpec("capture", "\u6293\u5305\u4f9d\u8d56", CAPTURE_ROOTS),
}
def bundle_spec(kind="common"):
    """\u4ec5\u5141\u8bb8\u660e\u786e\u7684\u4e24\u79cd\u8d44\u6e90\u7c7b\u578b\u3002"""
    try: return SPECS[kind]
    except (KeyError, TypeError): raise ValueError("Unknown offline bundle kind") from None
