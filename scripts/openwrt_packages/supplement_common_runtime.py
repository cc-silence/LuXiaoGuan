"""Supplement verified common profiles from their exact target indexes."""
import argparse
import concurrent.futures
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

from modules.capture_services.openwrt.common_bundle_manifest import sha256_file
from modules.capture_services.openwrt.common_bundle_repository import validate_catalog
from .collect_common_bundle import Collector
from .common_bundle_support import hash_table, json_bytes, parse_feed
from .extend_common_bundle import extend_profile


def supplement(profile, target_rows):
    result = extend_profile(profile, target_rows)
    previous = {row["name"]: row for row in profile["packages"]}
    current = {row["name"]: row for row in result["packages"]}
    if any(current.get(name) != row for name, row in previous.items()):
        raise ValueError("Supplement changed an existing package")
    if set(current) - set(previous) - {"libpthread", "libgcc1"}:
        raise ValueError("Unexpected supplementary runtime package")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-archive", type=Path, required=True)
    parser.add_argument("--baseline-sha256", required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--source-cache", type=Path, action="append", default=[])
    parser.add_argument("--package-cache", type=Path)
    parser.add_argument("--deliver", action="store_true")
    args = parser.parse_args()
    if sha256_file(args.baseline_archive) != args.baseline_sha256:
        raise ValueError("Baseline fingerprint mismatch")
    root = Path(__file__).resolve().parents[2]
    destination = args.destination.resolve()
    if args.deliver and destination.is_relative_to(root / "build"):
        from scripts.build_cleanup import allowed_target
        if not allowed_target(root, destination / "bundle"):
            raise ValueError("Use a build/openwrt-* destination for managed delivery cleanup")
    task = Collector(destination, 8, "common")

    def source(url):
        for folder in args.source_cache:
            path = folder / hashlib.sha256(url.encode()).hexdigest()
            if path.is_file():
                return path.read_bytes()
        return task.http.official(url)

    with zipfile.ZipFile(args.baseline_archive) as archive:
        catalog = json.loads(archive.read("catalog.json"))
        objects = validate_catalog(catalog, "common")
        if set(archive.namelist()) != {"catalog.json"} | {"objects/" + sha for sha in objects}:
            raise ValueError("Baseline inventory mismatch")
        for sha, row in objects.items():
            raw = archive.read(row["blob"])
            if len(raw) != row["size"] or hashlib.sha256(raw).hexdigest() != sha:
                raise ValueError("Baseline object mismatch")
            task.put(raw, row["filename"])
        task.status("baseline-verified", profiles=len(catalog["profiles"]), objects=len(objects))

        def extend(profile):
            idx = next(row for row in profile["indexes"] if row["feed"] == "target")
            raw = archive.read(idx["blob"])
            if hashlib.sha256(raw).hexdigest() != idx["sha256"]:
                raise ValueError("Target index fingerprint mismatch")
            apk = profile["package_manager"] == "apk"
            base = idx["url"].rsplit("/", 1)[0]
            sums = None
            if apk:
                table = hash_table(source(base.rsplit("/packages", 1)[0] + "/sha256sums"))
                sums = {"target/" + key.removeprefix("packages/"): value for key, value in table.items()
                        if key.startswith("packages/")}
            rows = parse_feed(raw, "target", base, apk, sums)
            return supplement(profile, rows)

        profiles = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(extend, profile) for profile in catalog["profiles"]]
            for i, future in enumerate(futures, 1):
                profiles.append(future.result())
                if i % 100 == 0 or i == len(futures):
                    task.status("supplementing-runtime", completed=i, total=len(futures))
    if len(profiles) != len(catalog["profiles"]):
        raise ValueError("Existing platform coverage changed")
    for profile in profiles:
        for row in profile["packages"]:
            if args.package_cache is not None:
                path = args.package_cache / row["sha256"]
                if path.is_file():
                    raw = path.read_bytes()
                    if len(raw) != row["size"] or hashlib.sha256(raw).hexdigest() != row["sha256"]:
                        raise ValueError("Cached runtime package mismatch")
                    task.put(raw, row["filename"])
    inventory = dict(schema_version=1, purpose=task.spec.catalog_purpose,
                     roots=catalog["roots"], releases=catalog["releases"], targets=catalog["targets"],
                     profiles=profiles + catalog.get("unavailable_profiles", [])
                     + catalog.get("unavailable_dependencies", []), errors=[])
    (task.root / "inventory.json").write_bytes(json_bytes(inventory))
    report = task.finish(inventory, task.fetch_packages(inventory))
    if not report["complete"]:
        raise ValueError("Supplemented bundle is incomplete")
    validate_catalog(json.loads((task.root / "bundle/catalog.json").read_bytes()), "common")
    if args.deliver:
        destination = Path(__file__).resolve().parents[2] / "dist/openwrt_common"
        destination.mkdir(parents=True, exist_ok=True)
        original = Path(report["zip_path"])
        delivery = destination / original.name
        if delivery.exists() and sha256_file(delivery) != report["zip_sha256"]:
            raise ValueError("Delivery filename already exists with different content")
        if not delivery.exists():
            shutil.copyfile(original, delivery)
        if sha256_file(delivery) != report["zip_sha256"]:
            raise ValueError("Delivered ZIP fingerprint mismatch")
        report["zip_path"] = str(delivery)
        delivery.with_suffix(".report.json").write_bytes(json_bytes(report))
        from .support_inventory import write_inventory
        write_inventory("common", delivery)
        from scripts.build_cleanup import cleanup_offline
        cleanup_offline(Path(__file__).resolve().parents[2], task.root, delivery)
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
