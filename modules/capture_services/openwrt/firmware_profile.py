"""Firmware identity and official repository paths shared by both installers."""
import re

_STABLE = re.compile(r"(\d{2}\.\d{2})\.(\d+)")
_RC = re.compile(r"(\d{2}\.\d{2})\.(\d+)-rc(\d+)", re.I)
_BRANCH = re.compile(r"(\d{2}\.\d{2})-SNAPSHOT", re.I)


def release_kind(version):
    version = str(version or "").strip()
    if _STABLE.fullmatch(version):
        return "stable"
    if _RC.fullmatch(version):
        return "rc"
    if _BRANCH.fullmatch(version):
        return "branch"
    if version.upper() == "SNAPSHOT":
        return "snapshot"
    raise ValueError("无法识别固件版本，请核对设备版本信息：" + version)


def canonical_release(version):
    """Use the spelling of official URLs for case-insensitive firmware identities."""
    version = str(version or "").strip()
    kind = release_kind(version)
    if kind == "snapshot":
        return "SNAPSHOT"
    if kind == "branch":
        return _BRANCH.fullmatch(version)[1] + "-SNAPSHOT"
    match = (_RC if kind == "rc" else _STABLE).fullmatch(version)
    result = match[1] + "." + str(int(match[2]))
    return result + "-rc" + str(int(match[3])) if kind == "rc" else result


def release_series(version):
    return "" if release_kind(version) == "snapshot" else str(version).strip()[:5]


def repository_paths(version):
    version = canonical_release(version)
    kind = release_kind(version)
    if kind == "snapshot":
        return ("snapshots",)
    if kind == "branch":
        series = release_series(version)
        return ("releases/" + series + "-SNAPSHOT", "releases/packages-" + series)
    return ("releases/" + str(version).strip(),)


def package_directory(version, arch, feed):
    paths = repository_paths(version)
    return tuple(path + ("/" if path.startswith("releases/packages-") else "/packages/")
                 + arch + "/" + feed for path in paths)


def compatible_release(device, release):
    """Candidates never cross a known release series; unversioned snapshots use ABI checks."""
    version = str(getattr(device, "version", "")).strip()
    try:
        version = canonical_release(version)
        release = canonical_release(release)
        kind = release_kind(version)
        if release_kind(release) != "stable":
            return False
        if kind == "snapshot":
            return True
        if kind == "stable":
            return release == version
        return release_series(release) == release_series(version)
    except ValueError:
        return False
