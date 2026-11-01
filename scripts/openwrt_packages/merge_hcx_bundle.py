"""Merge designated hcx payloads into a verified capture ZIP without re-resolving profiles."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

from scripts.openwrt_packages.collect_common_bundle import Collector, deliver_bundle, ROOT
from modules.capture_services.openwrt.common_bundle_repository import validate_catalog


def merge(archive, expected_sha256, destination, deliver=False):
    destination = Path(destination).resolve()
    if deliver and destination.is_relative_to(ROOT / "build"):
        from scripts.build_cleanup import allowed_target
        if not allowed_target(ROOT, destination / "bundle"):
            raise ValueError("Use a direct build/openwrt-* staging directory for delivery")
    task = Collector(destination, kind="capture")
    inventory = task.ledger.seed_archive(archive, expected_sha256)
    with zipfile.ZipFile(archive) as zipped:
        baseline = json.loads(zipped.read("catalog.json"))
    result = task.finish(inventory, [])
    if not result["complete"]:
        raise ValueError("Cannot deliver an incomplete capture bundle")
    with zipfile.ZipFile(result["zip_path"]) as zipped:
        raw = zipped.read("catalog.json")
        combined = json.loads(raw)
        objects = validate_catalog(combined, "capture")
        if combined["profiles"] != baseline["profiles"]:
            raise ValueError("Existing exact-firmware profiles unexpectedly changed")
        if set(zipped.namelist()) != {"catalog.json"} | {"objects/" + sha for sha in objects}:
            raise ValueError("Merged capture ZIP inventory differs")
    proof = dict(baseline_sha256=expected_sha256,
                 preserved_profiles=len(baseline["profiles"]), hcx_variants=len(combined["hcx_variants"]),
                 original_hcx_references=sum(len(v["packages"]) for v in combined["hcx_variants"]),
                 objects_verified=len(objects), catalog_sha256=hashlib.sha256(raw).hexdigest(),
                 limitation="Local byte/inventory verification only; no router or Debian runtime validation.")
    (task.root / "hcx-merge-verification.json").write_text(
        json.dumps(proof, indent=2) + "\n", encoding="utf-8")
    if deliver:
        result["zip_path"] = str(deliver_bundle(task, result))
    return result



if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--deliver", action="store_true")
    args = parser.parse_args()
    print(json.dumps(merge(args.archive, args.sha256, args.destination, args.deliver)), flush=True)
