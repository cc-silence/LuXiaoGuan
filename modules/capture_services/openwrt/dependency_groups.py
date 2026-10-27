"""Shared dependency boundaries for confirmations, task scopes and UI rows."""
from .common_tool_dependencies import TOOLS, TOOL_KEYS

CAPTURE_PACKAGES = {
    "scan": ("aircrack-ng",), "handshake": ("aircrack-ng",),
    "pmkid": ("hcxdumptool", "aircrack-ng"),
    "wps": ("reaver", "aircrack-ng"), "password": (),
}
CAPTURE_KEYS = frozenset(CAPTURE_PACKAGES)
COMMON_ITEMS = ("sftp", "terminal", *(tool.key for tool in TOOLS))
COMMON_KEYS = frozenset(COMMON_ITEMS)


def missing_packages(dependencies, keys):
    """Use the same explicit group/selection for preview and execution."""
    return tuple(dict.fromkeys(package for item in dependencies if item.key in keys
                 and item.state.value == "missing" for package in item.missing_packages))
