"""Designated hcx payloads stored inside the authenticated capture repository."""
import re
from contextlib import contextmanager
from tempfile import TemporaryDirectory

from .bundle_storage_lock import bundle_lock

# Original supplemental objects keep their schema; install selection has no architecture whitelist.
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
def primary_package(device, cancel, tell=None):
    """Use the signed local body first, otherwise fetch the same pinned body online."""
    from .common_bundle_repository import CommonBundleRepository
    from .signal_manager import SignalError
    repo = CommonBundleRepository(kind="capture")
    with bundle_lock(repo.root):
        if cancel is not None and cancel.is_set():
            raise SignalError("cancelled", "Installation cancelled")
        if repo._load():
            from .hcx_sources import local_primary_row
            row = local_primary_row(repo.catalog, device)
            if row is not None:
                with TemporaryDirectory(prefix="routermanager-hcx-") as directory:
                    yield repo.materialize(row, directory, cancel), row
                return
    from .hcx_online import online_primary_package
    with online_primary_package(device, cancel, tell or (lambda *_: None)) as package:
        yield package
