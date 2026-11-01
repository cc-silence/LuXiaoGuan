"""Conservative policy for official packages tried on an unverified firmware."""
_CORE = frozenset({"libc", "libgcc1", "libpthread", "librt", "kernel", "busybox", "opkg", "base-files",
                   "procd", "netifd", "uci", "ubus", "firewall", "firewall4", "dnsmasq", "dropbear"})


def is_kwrt(device):
    return str(getattr(device, "distribution", "")).strip().casefold() == "kwrt"


def requires_candidate(device):
    from .firmware_profile import release_kind
    try:
        return (str(getattr(device, "distribution", "")).strip().casefold() != "openwrt"
                or release_kind(getattr(device, "version", "")) != "stable")
    except ValueError:
        return True


def verify_candidate_packages(packages, installed, *, kwrt=False):
    """Reject system changes before downloading/uploading or invoking opkg."""
    if not getattr(installed, "versions", {}).get("libc"):
        raise ValueError("无法确认当前固件的 libc 基础库版本，未安装。")
    packages = tuple(packages)
    verify_candidate_names(package.name for package in packages)
    for package in packages:
        if package.name in installed:
            raise ValueError("候选包会替换已安装的软件，已停止：" + package.name)


def verify_candidate_names(names):
    for name in names:
        if name in _CORE or name.startswith(("kmod-", "wpad", "hostapd", "wpa-supplicant")):
            raise ValueError("候选包需要改动系统基础组件，已停止：" + name)


class CandidatePlanRejected(ValueError):
    """A local trial failed before commit; another source may be considered."""
    def __init__(self, error):
        super().__init__(str(error))
        self.package_diagnostic = getattr(error, "package_diagnostic", "")
