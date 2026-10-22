"""Shared installable helpers for AP broadcasting and station relay setup."""
from .signal_commands import SIGNAL_PACKAGES

WIRELESS_HELPER_PACKAGES = (*SIGNAL_PACKAGES, "iw", "wpa-cli", "rpcd", "rpcd-mod-iwinfo")
# Device-owned wireless stacks and kernel drivers must be matched by firmware.
FIRMWARE_REQUIREMENTS = ("uci", "ubus", "netifd", "hostapd", "wpa_supplicant",
                         "wireless drivers", "rpcd UCI rollback API")
