"""Offline SNAPSHOT candidate selection using signed metadata and live installed versions."""
import re

_CORE = frozenset({"libc", "libgcc1", "libpthread", "librt", "kernel", "busybox", "opkg", "base-files"})


class SnapshotBundleMismatch(ValueError):
    """No compatible local candidate; no packages have been written."""


def is_snapshot(device):
    return "SNAPSHOT" in str(getattr(device, "version", "")).upper()


def candidate_profiles(repository, device):
    if (getattr(device, "distribution", "") != "OpenWrt"
            or getattr(device, "package_manager", "") != "opkg"):
        return ()
    match = re.fullmatch(r"(\d+\.\d+)-SNAPSHOT", str(getattr(device, "version", "")), re.I)
    if match is None:
        return ()
    if repository.catalog is None and not repository._load():
        return ()
    rows = []
    for profile in repository.catalog["profiles"]:
        release = re.fullmatch(re.escape(match[1]) + r"\.(\d+)", profile["release"])
        if (release and profile["target"] == getattr(device, "target", "")
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
            if any(p.name in _CORE or p.name.startswith("kmod-") for p in selected):
                raise ValueError("\u9700\u8981\u66ff\u6362\u6216\u8865\u88c5\u56fa\u4ef6\u57fa\u7840\u7ec4\u4ef6\uff0c\u672a\u5b89\u88c5\u3002")
        except ValueError as exc:
            last = str(exc)
            tell("\u672c\u5730\u5019\u9009 " + profile["release"] + " \u4f9d\u8d56\u68c0\u67e5\u672a\u901a\u8fc7\uff1a" + last, "info")
            continue
        tell("SNAPSHOT \u672c\u5730\u4f9d\u8d56\u68c0\u67e5\u901a\u8fc7\uff0c\u5019\u9009\u5305\u6765\u81ea " + profile["release"]
             + "\uff1b\u4e0b\u4e00\u6b65\u7531\u8def\u7531\u5668\u9884\u6f14\u6838\u5bf9\uff0c\u4e0d\u4ee3\u8868\u5df2\u901a\u8fc7\u771f\u673a\u529f\u80fd\u9a8c\u8bc1\u3002", "info")
        return profile
    raise SnapshotBundleMismatch("SNAPSHOT \u672c\u5730\u517c\u5bb9\u68c0\u67e5\u672a\u901a\u8fc7\uff1a" + last)
