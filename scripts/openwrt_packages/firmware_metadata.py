"""Read current and legacy official firmware architecture evidence."""
import gzip
import hashlib
import json
import re

from .common_bundle_support import OFFICIAL, hash_table, parse_feed


def firmware_metadata(http, release, target):
    """Read platform evidence; legacy metadata does not certify device models."""
    base = OFFICIAL + "/" + release + "/targets/" + target
    try:
        raw = http.official(base + "/profiles.json")
    except FileNotFoundError:
        return legacy_metadata(http, base, release, target)
    data = json.loads(raw)
    if data.get("target") != target:
        raise ValueError("Official firmware target differs: " + base)
    return data, [dict(url=base + "/profiles.json", sha256=hashlib.sha256(raw).hexdigest())]


def legacy_metadata(http, base, release, target):
    hashes = hash_table(http.official(base + "/sha256sums"))
    evidence = []

    def checked(name):
        raw = http.official(base + "/" + name)
        digest = hashlib.sha256(raw).hexdigest()
        if hashes.get(name) != digest:
            raise ValueError("Legacy firmware metadata checksum differs: " + name)
        evidence.append(dict(url=base + "/" + name, sha256=digest))
        return raw

    config = checked("config.buildinfo").decode("utf-8")
    family, subtarget = target.split("/")
    if not re.search(r"^CONFIG_TARGET_" + re.escape(family) + r"=y$", config, re.M):
        raise ValueError("Legacy firmware target differs: " + base)
    prefix = "openwrt-" + release + "-" + family + "-" + subtarget + "-"
    has_image = any(name.startswith(prefix) for name in hashes)
    # Old single-subtarget builds omit "generic" from image/SDK names.
    default_sdk = "openwrt-sdk-" + release + "-" + family + "_gcc-"
    default_target = (subtarget == "generic" and
                      any(name.startswith(default_sdk) for name in hashes) and
                      not re.search(r"^CONFIG_TARGET_" + re.escape(family) +
                                    r"_(?!DEVICE)[A-Za-z0-9_]+=y$", config, re.M))
    if not has_image and not default_target:
        raise ValueError("Legacy firmware image target differs: " + base)
    # Original image checksums may predate a refreshed Packages.gz. The
    # checksummed build manifest still proves the original platform identity.
    manifest = checked("packages/Packages.manifest")
    rows = parse_feed(gzip.compress(manifest), "target", base + "/packages", False)
    base_files = [row for row in rows if row["name"] == "base-files"]
    if len(base_files) != 1:
        raise ValueError("Legacy firmware has no unique base-files architecture")
    arch = base_files[0]["architecture"]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", arch) or arch in ("all", "noarch"):
        raise ValueError("Legacy firmware architecture is not device-specific")
    return dict(target=target, arch_packages=arch, legacy_metadata=True), evidence
