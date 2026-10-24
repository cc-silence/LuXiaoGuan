"""Verify an incremental offline bundle against its previously audited baseline."""
import hashlib
import json
from pathlib import Path
import threading
import zipfile

from .common_bundle_support import HttpCache, closure
from .firmware_metadata import firmware_metadata
from .opkg_version import satisfies


class OfflineCache(HttpCache):
    def get(self, url, limit=16 * 1024 * 1024):
        key = hashlib.sha256(url.encode()).hexdigest()
        path = self.root / key
        if path.is_file():
            raw = path.read_bytes()
            if len(raw) > limit:
                raise ValueError("Cached metadata exceeds limit")
            return raw
        if path.with_suffix(".404").is_file():
            raise FileNotFoundError(url)
        raise ValueError("Official evidence was not cached: " + url)


def verify_extension(archive, baseline_archive, baseline_sha256, baseline_proof, cache_root, kind):
    from modules.capture_services.openwrt.common_bundle_manifest import sha256_file
    from modules.capture_services.openwrt.common_bundle_repository import validate_catalog
    from modules.capture_services.openwrt.dependency_resolution import InstalledPackages, resolve_packages
    from modules.capture_services.openwrt.signal_package_feed import FeedPackage
    if sha256_file(baseline_archive) != baseline_sha256:
        raise ValueError("Baseline archive fingerprint differs")
    proof = json.loads(Path(baseline_proof).read_text(encoding="utf-8"))
    with zipfile.ZipFile(baseline_archive) as zipped:
        old_raw = zipped.read("catalog.json")
    old = json.loads(old_raw)
    if (hashlib.sha256(old_raw).hexdigest() != proof["catalog_sha256"] or
            len(old["profiles"]) != proof["profiles_verified"]):
        raise ValueError("Baseline proof belongs to another archive")
    with zipfile.ZipFile(archive) as zipped:
        raw = zipped.read("catalog.json")
        catalog = json.loads(raw)
        objects = validate_catalog(catalog, kind)
        if set(zipped.namelist()) != {"catalog.json"} | {"objects/" + sha for sha in objects}:
            raise ValueError("Archive contains missing or unreferenced objects")
        for sha, row in objects.items():
            value = zipped.read("objects/" + sha)
            if len(value) != row["size"] or hashlib.sha256(value).hexdigest() != sha:
                raise ValueError("Archive object checksum differs: " + sha)
    def identity(p):
        return p["release"], p["target"], p["architecture"]
    baseline = {identity(p): p for p in old["profiles"]}
    current = {identity(p): p for p in catalog["profiles"]}
    if any(current.get(key) != p for key, p in baseline.items()):
        raise ValueError("Previously verified profile was removed or changed")
    for field in ("unavailable_profiles", "unavailable_dependencies"):
        if any(p not in catalog.get(field, []) for p in old.get(field, [])):
            raise ValueError("Previous coverage exclusions were lost")
    cache = OfflineCache(cache_root, threading.Event())
    new = [p for key, p in current.items() if key not in baseline]
    sources = []
    for p in new:
        metadata, evidence = firmware_metadata(cache, p["release"], p["target"])
        if metadata["arch_packages"] != p["architecture"] or evidence != p["firmware_evidence"]:
            raise ValueError("New firmware platform evidence differs")
        selected, provided = closure(p["packages"], p["roots"])
        if {r["name"] for r in selected} != {r["name"] for r in p["packages"]}:
            raise ValueError("New dependency graph differs")
        if provided != p["firmware_required"]:
            raise ValueError("Firmware-owned dependencies differ")
        records = {}
        for r in p["packages"]:
            records[r["name"]] = FeedPackage(r["name"], r["version"], r["filename"], r["sha256"],
                r["size"], r["url"], tuple(r["depends"]), tuple(r.get("provides", [])))
        for r in p["packages"]:
            for alias in r.get("provides", []):
                records.setdefault(alias.split(" ", 1)[0], records[r["name"]])
        actual = resolve_packages(records, p["roots"], InstalledPackages({n: "device-provided" for n in provided}), satisfies)
        if {r.name for r in actual} != {r["name"] for r in p["packages"]}:
            raise ValueError("Runtime dependency resolver disagrees")
        sources.extend(evidence)
    return dict(schema=1, catalog_sha256=hashlib.sha256(raw).hexdigest(),
        profiles_verified=len(current), objects_verified=len(objects), new_profiles_verified=len(new),
        baseline_profiles_preserved=len(baseline), baseline_sha256=baseline_sha256,
        models=proof["models"], sources=sources,
        unavailable_dependencies=catalog.get("unavailable_dependencies", []),
        limitation="Package/platform verification only; model evidence retained from baseline. No live router test.")
