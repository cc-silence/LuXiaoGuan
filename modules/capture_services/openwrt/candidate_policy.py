"""Conservative policy for official packages tried on an unverified firmware."""
_CORE = frozenset({"libc", "libgcc1", "libpthread", "librt", "kernel", "busybox", "opkg", "base-files",
                   "procd", "netifd", "uci", "ubus", "firewall", "firewall4", "dnsmasq", "dropbear"})
_TRIAL_TOOLS = frozenset({"wpa-cli", "iw", "iwinfo", "aircrack-ng", "hcxdumptool", "reaver",
                         "tmux", "openssh-sftp-server"})


def is_kwrt(device):
    return str(getattr(device, "distribution", "")).strip().casefold() == "kwrt"


def requires_candidate(device):
    return is_kwrt(device) or "SNAPSHOT" in str(getattr(device, "version", "")).upper()


def verify_candidate_packages(packages, installed, *, kwrt=False):
    """Reject system changes before downloading/uploading or invoking opkg."""
    if not getattr(installed, "versions", {}).get("libc"):
        raise ValueError("无法确认当前固件的 libc 基础库版本，未安装。")
    for package in packages:
        name = package.name
        if name in installed:
            raise ValueError("候选包会改动已有软件，未安装：" + name)
        if (name in _CORE or name.startswith(("kmod-", "wpad", "hostapd", "wpa-supplicant"))
                or kwrt and name not in _TRIAL_TOOLS):
            raise ValueError("兼容尝试需要补装系统组件或非独立工具，已跳过：" + name)


class CandidatePlanRejected(ValueError):
    """A local trial failed before commit; another source may be considered."""
    def __init__(self, error):
        super().__init__(str(error))
        self.package_diagnostic = getattr(error, "package_diagnostic", "")
