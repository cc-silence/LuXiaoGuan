"""Shared hcx upgrade policy; firmware-native tools remain usable when restricted."""
import re


def supports_modern_hcx(device):
    if device is None:
        return False
    kernel = re.match(r"^(\d+)\.(\d+)", str(getattr(device, "kernel_version", "") or "").strip())
    if kernel and tuple(map(int, kernel.groups())) < (5, 15):
        return False
    version = re.match(r"^(\d+)\.(\d+)", str(getattr(device, "version", "") or "").strip())
    if version:
        return tuple(map(int, version.groups())) >= (23, 5)
    return bool(kernel and tuple(map(int, kernel.groups())) >= (5, 15))


def installation_satisfied(item):
    """An installed official old hcx is distinct from automatic-capture readiness."""
    from .models import DependencyState
    return (item.state in (DependencyState.READY, DependencyState.DISABLED)
            or item.key == "pmkid" and item.state == DependencyState.OUTDATED
            and not item.missing_packages)
