"""Offline SNAPSHOT candidate selection using signed metadata and live installed versions."""
import re

from .candidate_policy import is_kwrt, verify_candidate_packages


class SnapshotBundleMismatch(ValueError):
    """No compatible local candidate; no packages have been written."""


def is_snapshot(device):
    return "SNAPSHOT" in str(getattr(device, "version", "")).upper()


def candidate_profiles(repository, device):
    if (getattr(device, "distribution", "") != "OpenWrt" and not is_kwrt(device)
            or getattr(device, "package_manager", "") != "opkg"):
        return ()
    match = re.fullmatch(r"(\d+\.\d+)(?:-SNAPSHOT|\.\d+)", str(getattr(device, "version", "")), re.I)
    if match is None:
        return ()
    if repository.catalog is None and not repository._load():
        return ()
    rows = []
    for profile in repository.catalog["profiles"]:
        release = re.fullmatch(re.escape(match[1]) + r"\.(\d+)", profile["release"])
        if (release and (is_snapshot(device) or profile["release"] == getattr(device, "version", ""))
                and profile["target"] == getattr(device, "target", "")
                and profile["architecture"] == getattr(device, "package_architecture", "")
                and profile["package_manager"] == "opkg"):
            rows.append((int(release[1]), profile))
    return tuple(p for _, p in sorted(rows, key=lambda row: row[0], reverse=True))


def select_profile(repository, device, wanted, installed, version_check, cancel, tell):
    if not getattr(installed, "versions", {}).get("libc"):
        raise ValueError("\u65e0\u6cd5\u786e\u8ba4\u5f53\u524d\u56fa\u4ef6\u7684 libc \u57fa\u7840\u5e93\u7248\u672c\uff0c\u672a\u5b89\u88c5\u3002")
    candidates = candidate_profiles(repository, device)
    last = "\u672c\u5730\u79bb\u7ebf\u5305\u6ca1\u6709\u540c\u7cfb\u5217\u3001\u540c\u5e73\u53f0\u548c\u67b6\u6784\u7684\u5019\u9009\u5305\u3002"
    for profile in candidates:
        if cancel is not None and cancel.is_set():
            raise InterruptedError("Offline compatibility check cancelled")
        try:
            selected = repository.ipk_selection(profile, wanted, installed, version_check)
            verify_candidate_packages(selected, installed, kwrt=is_kwrt(device))
        except ValueError as exc:
            last = str(exc)
            tell("本地候选 " + profile["release"] + " 依赖检查未通过：" + last, "info")
            continue
        tell(str(getattr(device, "version", "")) + " 候选包依赖检查通过，来源为官方 OpenWrt " + profile["release"]
             + "；将由路由器预演核对，只补独立缺项，不替换已有组件。", "info")
        return profile
    raise SnapshotBundleMismatch("本地候选兼容检查未通过：" + last)
