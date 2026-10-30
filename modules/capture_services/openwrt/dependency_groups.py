"""Shared dependency boundaries for confirmations, task scopes and UI rows."""
from .common_tool_dependencies import TOOLS, TOOL_KEYS, REQUIRED_TOOL_KEYS

CAPTURE_PACKAGES = {
    "scan": ("aircrack-ng", "iw", "ip-tiny"),
    "handshake": ("aircrack-ng", "iw", "ip-tiny"),
    "pmkid": ("hcxdumptool", "aircrack-ng", "iw", "ip-tiny"),
    "wps": ("reaver", "aircrack-ng", "iw", "ip-tiny"),
    "password": (),
}
CAPTURE_KEYS = frozenset(CAPTURE_PACKAGES)
CAPTURE_REMOVABLE_PACKAGES = {
    key: tuple(package for package in packages if package not in {"iw", "ip-tiny", "ip-full"})
    for key, packages in CAPTURE_PACKAGES.items()
}
COMMON_ITEMS = ("sftp", "terminal", *(tool.key for tool in TOOLS))
COMMON_KEYS = frozenset(COMMON_ITEMS)
REQUIRED_COMMON_ITEMS = ("sftp", *(tool.key for tool in TOOLS if tool.key in REQUIRED_TOOL_KEYS))
REQUIRED_COMMON_KEYS = frozenset(REQUIRED_COMMON_ITEMS)


def missing_packages(dependencies, keys):
    """Use the same explicit group/selection for preview and execution."""
    return tuple(dict.fromkeys(package for item in dependencies if item.key in keys
                 and item.state.value == "missing" for package in item.missing_packages))
