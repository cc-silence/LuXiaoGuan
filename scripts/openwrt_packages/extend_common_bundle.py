"""Extend verified common profiles with new roots without re-resolving old packages."""
import argparse
import copy
import hashlib
import itertools
import json
from pathlib import Path
import shutil
import zipfile

from modules.capture_services.openwrt.bundle_specs import COMMON_ROOTS
from modules.capture_services.openwrt.common_bundle_manifest import sha256_file
from modules.capture_services.openwrt.common_bundle_repository import validate_catalog
from .collect_common_bundle import Collector
from .common_bundle_support import HttpCache, closure, json_bytes


def extend_profile(profile, rows):
    previous = {row["name"]: row for row in profile["packages"]}
    added, provided = closure(list(previous.values()) + rows, sorted(COMMON_ROOTS))
    result = copy.deepcopy(profile)
    selected = dict(previous)
    for row in added:
        if row["name"] not in selected:
            selected[row["name"]] = row
    result["packages"] = list(selected.values())
    result["roots"] = sorted(COMMON_ROOTS)
    result["firmware_required"] = sorted(set(profile["firmware_required"]) | set(provided))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-archive", type=Path, required=True)
    parser.add_argument("--baseline-sha256", required=True)
    parser.add_argument("--metadata-root", type=Path, action="append", required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--http-cache", type=Path, required=True)
    parser.add_argument("--deliver", action="store_true")
    args = parser.parse_args()
    if sha256_file(args.baseline_archive) != args.baseline_sha256:
        raise ValueError("Baseline ZIP differs from its approved fingerprint")
    task = Collector(args.destination, 8, "common")
    task.http = HttpCache(args.http_cache, task.stop)
    with zipfile.ZipFile(args.baseline_archive) as archive:
        catalog = json.loads(archive.read("catalog.json"))
        if (catalog.get("complete") is not True or catalog.get("purpose") != "common-router-tools"
                or not set(catalog["roots"]).issubset(COMMON_ROOTS)):
            raise ValueError("Cannot extend this baseline catalog")
        # Read one content-addressed object at a time; never load the ZIP into RAM.
        for name in archive.namelist():
            if name == "catalog.json":
                continue
            if not name.startswith("objects/") or len(name) != len("objects/") + 64:
                raise ValueError("Unexpected baseline archive entry")
            raw = archive.read(name)
            if hashlib.sha256(raw).hexdigest() != name.split("/")[1]:
                raise ValueError("Baseline object is damaged")
            task.put(raw, "baseline-object")
    profiles = []
    ordered = sorted(catalog["profiles"], key=lambda p: (p["release"], p["architecture"], p["target"]))
    for (release, arch), group in itertools.groupby(ordered, key=lambda p: (p["release"], p["architecture"])):
        candidates = [root / release / (arch + ".json") for root in args.metadata_root]
        path = next((path for path in candidates if path.is_file()), None)
        if path is None:
            raise ValueError("Saved official metadata missing for " + release + "/" + arch)
        metadata = json.loads(path.read_text(encoding="utf-8"))
        for profile in group:
            profiles.append(extend_profile(profile, metadata["rows"]))
        task.status("extending-roots", release=release, architecture=arch, completed=len(profiles))
    profiles.extend(catalog.get("unavailable_profiles", []))
    inventory = dict(schema_version=1, purpose=task.spec.catalog_purpose,
        roots=sorted(COMMON_ROOTS), releases=catalog["releases"], targets=catalog["targets"],
        profiles=sorted(profiles, key=lambda p: (p["release"], p["target"])), errors=[])
    (task.root / "inventory.json").write_bytes(json_bytes(inventory))
    failures = task.fetch_packages(inventory)
    report = task.finish(inventory, failures)
    if not report["complete"]:
        raise ValueError("Extended common bundle is incomplete")
    validate_catalog(json.loads((task.root / "bundle/catalog.json").read_bytes()), "common")
    if args.deliver:
        destination = Path(__file__).resolve().parents[2] / "dist/openwrt_common"
        destination.mkdir(parents=True, exist_ok=True)
        original = Path(report["zip_path"])
        delivery = destination / original.name
        if delivery.exists() and sha256_file(delivery) != report["zip_sha256"]:
            raise ValueError("Delivery filename already contains different content")
        if not delivery.exists():
            shutil.copyfile(original, delivery)
        if sha256_file(delivery) != report["zip_sha256"]:
            raise ValueError("Delivery ZIP copy differs")
        report["zip_path"] = str(delivery)
        (destination / (original.stem + ".report.json")).write_bytes(json_bytes(report))
        from scripts.openwrt_packages.support_inventory import write_inventory
        write_inventory("common", delivery)
        from scripts.build_cleanup import cleanup_offline
        cleanup_offline(Path(__file__).resolve().parents[2], task.root, delivery)


if __name__ == "__main__":
    main()
