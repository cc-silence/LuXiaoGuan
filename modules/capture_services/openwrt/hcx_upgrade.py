"""Explicit hcx upgrade; normal missing-dependency installs stay unchanged."""
from .models import DependencyState


def upgrade_hcx(manager, device, cache_dir, cancel, tell):
    features = {"pmkid": True}
    tell("\u6b63\u5728\u68c0\u67e5\u6293\u5305\u5de5\u5177\u5347\u7ea7\u6761\u4ef6\u2026", "info")
    checked = manager.check(features, device.sftp_available, cancel)
    item = next((row for row in checked if row.key == "pmkid"), None)
    if item is None or item.state not in {DependencyState.UPGRADABLE, DependencyState.READY}:
        raise RuntimeError(getattr(item, "detail", "") or "Cannot verify hcx upgrade prerequisites")
    if item.state == DependencyState.READY:
        return checked
    tell("\u6b63\u5728\u5347\u7ea7 hcxdumptool 6.3.4\u2026", "info")
    if not manager._try_install_modern_hcxdumptool(
            device, device.sftp_available, cancel, tell, cache_dir):
        raise RuntimeError("\u5f53\u524d\u8bbe\u5907\u6ca1\u6709\u53ef\u7528\u7684\u73b0\u4ee3\u7248\u6293\u5305\u5de5\u5177\u5b89\u88c5\u5305\uff0c\u672a\u6267\u884c\u5347\u7ea7\u3002")
    checked = manager.check(features, device.sftp_available, cancel)
    item = next((row for row in checked if row.key == "pmkid"), None)
    if item is None or item.state != DependencyState.READY:
        raise RuntimeError(getattr(item, "detail", "") or "Hcx upgrade verification failed")
    tell("hcxdumptool 6.3.4 \u5347\u7ea7\u5b8c\u6210\uff0c\u5df2\u901a\u8fc7\u590d\u6d4b\u3002", "success")
    return checked
