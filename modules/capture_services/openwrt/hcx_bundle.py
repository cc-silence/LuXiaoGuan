"""Designated hcx payloads stored inside the authenticated capture repository."""
import re
from contextlib import contextmanager
from tempfile import TemporaryDirectory

from .bundle_storage_lock import bundle_lock

ARCHITECTURES = ("aarch64_cortex-a53", "mipsel_24kc")
RELEASES = {"22.03": "22.03.7", "23.05": "23.05.6",
            "24.10": "24.10.8", "25.12": "25.12.6"}
HCX_LIBRARIES = frozenset(("libpcap1", "libopenssl3"))


def validate_variants(variants):
    """Keep every original payload, without widening the hcx install exception."""
    if not isinstance(variants, list) or len(variants) != 8:
        raise ValueError("Capture bundle must contain all eight designated hcx variants")
    keys, objects = set(), {}
    for variant in variants:
        branch, arch = variant["branch"], variant["architecture"]
        key = branch, arch
        if (key in keys or branch not in RELEASES or arch not in ARCHITECTURES
                or variant["release"] != RELEASES[branch]
                or variant["package_manager"] != ("apk" if branch == "25.12" else "opkg")):
            raise ValueError("Invalid designated hcx variant identity")
        keys.add(key)
        library = "libopenssl1.1" if branch == "22.03" else "libopenssl3"
        rows = variant["packages"]
        if len(rows) != 3 or {r["name"] for r in rows} != {"hcxdumptool", "libpcap1", library}:
            raise ValueError("Designated hcx runtime dependencies are incomplete")
        for row in rows:
            suffix = "apk" if branch == "25.12" else "ipk"
            if (row["architecture"] != arch
                    or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.+~\-]*\." + suffix, row["filename"])
                    or not re.fullmatch(r"[a-f0-9]{64}", row["sha256"])
                    or row["blob"] != "objects/" + row["sha256"]
                    or type(row["size"]) is not int or not 0 < row["size"] <= 32 * 1024 * 1024):
                raise ValueError("Invalid designated hcx package object")
            if row["name"] == "hcxdumptool" and row["version"] != (
                    "6.2.4-1" if branch in {"22.03", "23.05"} else "6.3.4-r1"):
                raise ValueError("Unexpected designated hcx version")
            previous = objects.setdefault(row["sha256"], row)
            if previous["size"] != row["size"]:
                raise ValueError("Conflicting designated hcx object size")
    return objects


@contextmanager
def primary_package(device, cancel):
    """Hold the repository lock until upload and verification have completed."""
    from .common_bundle_repository import CommonBundleRepository
    from .signal_manager import SignalError
    repo = CommonBundleRepository(kind="capture")
    with bundle_lock(repo.root):
        if cancel is not None and cancel.is_set():
            raise SignalError("cancelled", "Installation cancelled")
        if not repo._load() or "hcx_variants" not in repo.catalog:
            raise SignalError("dependencies", "\u8bf7\u5148\u4e0b\u8f7d\u6216\u66f4\u65b0\u7edf\u4e00\u6293\u5305\u4f9d\u8d56\u79bb\u7ebf\u5305\uff08\u9700\u5305\u542b\u81ea\u52a8\u6293\u5305\u4e13\u7528 hcx 6.3.4\uff09\uff0c\u518d\u5b89\u88c5\u6216\u5347\u7ea7\u3002")
        branch = "25.12" if device.package_manager == "apk" else "24.10"
        variant = next(v for v in repo.catalog["hcx_variants"]
                       if v["branch"] == branch and v["architecture"] == device.package_architecture)
        row = next(r for r in variant["packages"] if r["name"] == "hcxdumptool")
        with TemporaryDirectory(prefix="routermanager-hcx-") as directory:
            yield repo.materialize(row, directory, cancel), row
