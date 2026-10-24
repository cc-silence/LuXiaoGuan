"""Collect verified official OpenWrt common-tool packages, deduplicate, ZIP."""
import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import sys
import threading
import time
import zipfile
from datetime import datetime

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.openwrt_packages.bundle_ledger import ProfileLedger
from scripts.openwrt_packages.firmware_metadata import firmware_metadata
from modules.capture_services.openwrt.bundle_specs import bundle_spec
from modules.capture_services.openwrt.common_bundle_manifest import sha256_file
from scripts.openwrt_packages.common_bundle_support import (
    HttpCache, OFFICIAL, TARGETS, ROOTS, closure, hash_table, parse_feed, json_bytes, MissingPackagesError,
)


class Collector:
    """\u6309\u7248\u672c\u548c\u76ee\u6807\u6536\u96c6\u4f9d\u8d56\uff0c\u5bf9\u8c61\u6309 SHA256 \u53bb\u91cd\u3002"""
    def __init__(self, destination, workers=8, kind="common"):
        self.spec = bundle_spec(kind)
        self.roots = tuple(ROOTS) if kind == "common" else tuple(sorted(self.spec.roots))
        self.root = Path(destination).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.stop = threading.Event()
        self.http = HttpCache(self.root / "http-cache", self.stop)
        self.workers = workers
        self.arch_locks = [threading.Lock() for _ in range(64)]
        self.objects = {}
        self.object_lock = threading.Lock()
        self.ledger = ProfileLedger(self.root, self.roots, self.spec.kind)
        self.refresh_selected = False
        self.reused_profiles = []
        self.started = time.time()
        self.deadline = self.started + 45 * 60

    def status(self, stage, **fields):
        data = dict(stage=stage, pid=os.getpid(), started=self.started,
                    deadline=self.deadline, updated=time.time(), **fields)
        # Append-only diagnostics avoid Windows rename failures while a reader holds the file.
        with (self.root / "status.jsonl").open("ab") as stream:
            stream.write(json_bytes(data))
        print(json.dumps(data, ensure_ascii=True), flush=True)

    def put(self, raw, filename):
        sha = hashlib.sha256(raw).hexdigest()
        relative = "objects/" + sha
        path = self.root / "bundle" / relative
        with self.object_lock:
            if sha not in self.objects:
                path.parent.mkdir(parents=True, exist_ok=True)
                if not path.is_file() or path.stat().st_size != len(raw) or hashlib.sha256(path.read_bytes()).hexdigest() != sha:
                    temp = path.with_suffix(".part")
                    temp.write_bytes(raw)
                    temp.replace(path)
                self.objects[sha] = dict(blob=relative, sha256=sha, size=len(raw))
        return dict(self.objects[sha], filename=filename)

    def releases(self):
        raw = self.http.get(OFFICIAL + "/")
        names = set(re.findall(rb'href="((?:19\.07|21\.02|22\.03|23\.05|24\.10|25\.12)\.\d+)/"', raw))
        releases = sorted((v.decode() for v in names), key=lambda v: tuple(map(int, v.split("."))))
        if not releases or not all(any(v.startswith(s + ".") for v in releases) for s in ("19.07", "21.02", "22.03", "23.05", "24.10", "25.12")):
            raise ValueError("Incomplete official release directory")
        return releases

    def architecture(self, release, arch):
        lock = self.arch_locks[int(hashlib.sha256((release + arch).encode()).hexdigest()[:4], 16) % 64]
        with lock:
            path = self.root / "metadata" / release / (arch + ".json")
            if path.is_file() and not self.refresh_selected:
                data = json.loads(path.read_text(encoding="utf-8"))
                # Re-register index objects when resuming after process exit.
                for item in data["indexes"]:
                    self.put((self.root / "bundle" / item["blob"]).read_bytes(), item["filename"])
                return data
            apk = tuple(map(int, release.split(".")[:2])) >= (25, 12)
            base = OFFICIAL + "/" + release + "/packages/" + arch
            hashes = hash_table(self.http.official(base + "/sha256sums")) if apk else None
            indexes, rows = [], []
            for feed in ("base", "packages", "luci"):
                filename = "packages.adb" if apk else "Packages.gz"
                feed_base = base + "/" + feed
                raw = self.http.official(feed_base + "/" + filename)
                index = self.put(raw, filename)
                indexes.append(dict(index, feed=feed, url=feed_base + "/" + filename))
                parsed = parse_feed(raw, feed, feed_base, apk, hashes)
                if apk:
                    reference = json.loads(self.http.official(feed_base + "/index.json"))
                    actual = {row["name"]: row["version"] for row in parsed}
                    for row in parsed:
                        for alias in row.get("provides", []):
                            actual.setdefault(alias["name"], row["version"])
                    expected = reference.get("packages", {})
                    if not expected or any(actual.get(name) != version for name, version in expected.items()):
                        raise ValueError("APK binary and JSON indexes disagree: " + feed_base)
                rows.extend(parsed)
            data = dict(rows=rows, indexes=indexes)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(json_bytes(data))
            return data

    def profile(self, release, target):
        if not self.refresh_selected:
            saved = self.ledger.load(release, target)
            if saved is not None:
                self.reused_profiles.append((release, target))
                return saved
        if time.time() > self.deadline:
            self.stop.set()
        target_family = target
        if release.startswith("23.05.") and target == "qualcommax/ipq807x":
            target = "ipq807x/generic"
        url = OFFICIAL + "/" + release + "/targets/" + target
        try:
            device, evidence = firmware_metadata(self.http, release, target)
        except FileNotFoundError:
            return dict(release=release, target=target, status="not-provided-by-upstream")
        arch = device.get("arch_packages")
        if not arch or not re.fullmatch(r"[A-Za-z0-9_.-]+", arch):
            raise ValueError("Missing architecture in official profile")
        architecture = self.architecture(release, arch)
        apk = tuple(map(int, release.split(".")[:2])) >= (25, 12)
        filename = "packages.adb" if apk else "Packages.gz"
        target_url = url + "/packages"
        target_raw = self.http.official(target_url + "/" + filename)
        target_index = dict(self.put(target_raw, filename), feed="target", url=target_url + "/" + filename)
        if apk:
            hashes = hash_table(self.http.official(url + "/sha256sums"))
            target_hashes = {("target/" + k.removeprefix("packages/")): value
                             for k, value in hashes.items() if k.startswith("packages/")}
        else:
            target_hashes = None
        target_rows = parse_feed(target_raw, "target", target_url, apk, target_hashes)
        try:
            selected, provided = closure(target_rows + architecture["rows"], self.roots)
        except MissingPackagesError as exc:
            return dict(release=release, target=target, target_family=target_family,
                        architecture=arch, status="dependencies-unavailable",
                        missing_packages=list(exc.packages), firmware_evidence=evidence)
        for row in selected:
            if row["architecture"] not in (arch, "all", "noarch"):
                raise ValueError("Incompatible package architecture: " + row["filename"])
        return dict(release=release, target=target, target_family=target_family, architecture=arch,
                    package_manager="apk" if apk else "opkg", status="index-verified",
                    roots=list(self.roots), packages=selected, firmware_required=provided,
                    firmware_evidence=evidence,
                    indexes=[target_index] + architecture["indexes"] if apk or self.spec.kind == "common" else [])

    def scan(self, releases, targets):
        profiles, errors = [], []
        tasks = [(v, t) for v in releases for t in targets]
        selected = set(tasks)
        previous_path = self.root / "inventory.json"
        if previous_path.is_file():
            previous = json.loads(previous_path.read_text(encoding="utf-8"))
            if previous.get("errors"):
                raise ValueError("Resolve the previous inventory errors before extending it")
            profiles = [p for p in previous["profiles"] if
                        (p["release"], p.get("target_family", p["target"])) not in selected]
        releases = sorted(set(releases) | {p["release"] for p in profiles}, key=lambda v:tuple(map(int,v.split("."))))
        targets = sorted(set(targets) | {p.get("target_family",p["target"]) for p in profiles})
        self.status("indexing", total=len(tasks), completed=0)
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {pool.submit(self.profile, *item): item for item in tasks}
            for index, future in enumerate(concurrent.futures.as_completed(futures), 1):
                release, target = futures[future]
                try:
                    profiles.append(future.result())
                except Exception as exc:
                    errors.append(dict(release=release, target=target, reason=str(exc)))
                    print("PROFILE_ERROR " + release + " " + target + " " + str(exc), flush=True)
                if index % 10 == 0 or index == len(tasks):
                    self.status("indexing", completed=index, total=len(tasks), errors=len(errors),
                                valid=sum(p["status"] == "index-verified" for p in profiles))
        inventory = dict(schema_version=1, purpose=self.spec.catalog_purpose, releases=releases,
                         targets=targets, roots=list(self.roots), profiles=sorted(profiles, key=lambda p:(p["release"],p["target"])),
                         errors=errors, created=time.time())
        (self.root / "inventory.json").write_bytes(json_bytes(inventory))
        return inventory

    def fetch_packages(self, inventory):
        objects = {}
        for profile in inventory["profiles"]:
            for row in profile.get("packages", []):
                obj = objects.setdefault(row["sha256"], dict(size=row["size"], sources=[]))
                if obj["size"] != row["size"]:
                    raise ValueError("Inconsistent shared package size")
                if row["url"] not in obj["sources"]:
                    obj["sources"].append(row["url"])
        for profile in inventory["profiles"]:
            for row in profile.get("indexes", []):
                path = self.root / "bundle" / row["blob"]
                if not path.is_file() or path.stat().st_size != row["size"] or hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]:
                    raw = self.http.official(row["url"])
                    if hashlib.sha256(raw).hexdigest() != row["sha256"]:
                        raise ValueError("Archived index unavailable; restore the approved ZIP or explicitly refresh this profile")
                    self.put(raw,row["filename"])
                else:
                    self.objects.setdefault(row["sha256"], dict(blob=row["blob"],size=row["size"],sha256=row["sha256"]))
        self.status("downloading", total=len(objects), completed=0, errors=len(inventory["errors"]))
        def fetch(item):
            sha, row = item
            if time.time() > self.deadline:
                self.stop.set()
            if self.stop.is_set():
                raise InterruptedError("Collection cancelled or deadline reached")
            path = self.root / "bundle" / "objects" / sha
            if path.is_file() and path.stat().st_size == row["size"] and hashlib.sha256(path.read_bytes()).hexdigest() == sha:
                return self.put(path.read_bytes(), Path(row["sources"][0]).name)
            errors = []
            candidates = list(dict.fromkeys(url for original in row["sources"] for url in (
                original, original.replace("https://downloads.openwrt.org/", "https://archive.openwrt.org/"),
                original.replace("https://downloads.openwrt.org/", "https://downloads.cdn.openwrt.org/"))))
            for url in candidates:
                try:
                    raw = self.http.get(url, limit=row["size"] + 1)
                    if len(raw) != row["size"] or hashlib.sha256(raw).hexdigest() != sha:
                        raise ValueError("Official package SHA256 or size mismatch")
                    return self.put(raw, Path(url).name)
                except Exception as exc:
                    errors.append(str(exc))
            raise RuntimeError("; ".join(errors))
        failures = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {pool.submit(fetch, item): item[0] for item in objects.items()}
            for index, future in enumerate(concurrent.futures.as_completed(futures), 1):
                try:
                    future.result()
                except Exception as exc:
                    failures.append(dict(sha256=futures[future], reason=str(exc)))
                    print("OBJECT_ERROR " + futures[future] + " " + str(exc), flush=True)
                if index % 25 == 0 or index == len(objects):
                    self.status("downloading", total=len(objects), completed=index, failed=len(failures))
        return failures

    def finish(self, inventory, failures):
        complete = (not inventory["errors"] and not failures
                    and any(p["status"] == "index-verified" for p in inventory["profiles"])
                    and all(p["status"] in ("index-verified", "not-provided-by-upstream", "dependencies-unavailable")
                            for p in inventory["profiles"]))
        profiles = []
        logical_packages = 0
        logical_indexes = 0
        for source in inventory["profiles"]:
            if source["status"] != "index-verified":
                continue
            profile = dict(source)
            for row in profile["packages"]:
                row["blob"] = "objects/" + row["sha256"]
                logical_packages += row["size"]
            logical_indexes += sum(row["size"] for row in profile["indexes"])
            profiles.append(profile)
        # Only ship objects referenced by installable profiles. Feed indexes
        # cached while resolving IPK capture packages are build inputs only.
        referenced = {}
        for profile in profiles:
            for row in profile["packages"] + profile["indexes"]:
                previous = referenced.setdefault(row["sha256"], row)
                if previous["size"] != row["size"]:
                    raise ValueError("Conflicting referenced object sizes")
        self.objects = {sha: dict(blob=row["blob"], size=row["size"], sha256=sha)
                        for sha, row in referenced.items()}
        catalog = dict(schema_version=1, purpose=self.spec.catalog_purpose, complete=complete,
                       roots=list(self.roots), releases=inventory["releases"], targets=inventory["targets"],
                       profiles=profiles, unsupported=[p for p in inventory["profiles"] if p["status"] not in ("index-verified", "not-provided-by-upstream", "dependencies-unavailable")],
                       unavailable_profiles=[p for p in inventory["profiles"] if p["status"] == "not-provided-by-upstream"],
                       unavailable_dependencies=[p for p in inventory["profiles"] if p["status"] == "dependencies-unavailable"],
                       index_errors=inventory["errors"], download_errors=failures,
                       note="Firmware base libraries are device-provided; runtime must verify versions and installation plan.")
        (self.root / "bundle" / "catalog.json").write_bytes(json_bytes(catalog))
        self.status("verifying", objects=len(self.objects))
        for sha, obj in self.objects.items():
            path = self.root / "bundle" / obj["blob"]
            if path.stat().st_size != obj["size"] or hashlib.sha256(path.read_bytes()).hexdigest() != sha:
                raise ValueError("Final object verification failed: " + sha)
        filename = self.spec.filename_prefix + datetime.now().strftime("%Y%m%d_%H%M%S") + ("" if complete else "_INCOMPLETE") + ".zip"
        archive = self.root / filename
        temporary = archive.with_suffix(".zip.part")
        self.status("compressing", objects=len(self.objects))
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as output:
            output.write(self.root / "bundle" / "catalog.json", "catalog.json")
            for sha, row in sorted(self.objects.items()):
                output.write(self.root / "bundle" / row["blob"], row["blob"])
        with zipfile.ZipFile(temporary) as check:
            bad = check.testzip()
            if bad:
                raise ValueError("ZIP CRC failure: " + bad)
            if len(check.namelist()) != len(self.objects) + 1:
                raise ValueError("ZIP inventory mismatch")
        temporary.replace(archive)
        result = dict(complete=complete, profiles=len(profiles), releases=len(inventory["releases"]),
                      targets=len(inventory["targets"]), architectures=sorted({p["architecture"] for p in profiles}),
                      package_references=sum(len(p["packages"]) for p in profiles),
                      unique_packages=len({r["sha256"] for p in profiles for r in p["packages"]}),
                      unique_objects=len(self.objects), logical_package_bytes=logical_packages,
                      logical_index_bytes=logical_indexes, deduplicated_bytes=sum(r["size"] for r in self.objects.values()),
                      zip_bytes=archive.stat().st_size, zip_path=str(archive),
                      zip_sha256=sha256_file(archive),
                      index_errors=len(inventory["errors"]), download_errors=len(failures),
                      elapsed_seconds=round(time.time() - self.started, 2))
        if complete:
            self.ledger.seed_inventory(inventory)
        result["reused_profiles"] = len(self.reused_profiles)
        (self.root / "report.json").write_bytes(json_bytes(result))
        self.status("complete" if complete else "incomplete", **result)
        return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", required=True)
    parser.add_argument("--kind", choices=("common", "capture"), default="common")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--http-cache", type=Path, help="Reuse a build-only official response cache")
    parser.add_argument("--deliver", action="store_true", help="Copy the verified ZIP and report into dist/openwrt_<kind>")
    parser.add_argument("--release", action="append")
    parser.add_argument("--target", action="append")
    parser.add_argument("--scan-only", action="store_true")
    parser.add_argument("--resume-inventory", action="store_true")
    parser.add_argument("--refresh-selected", action="store_true", help="Explicitly refresh only the selected releases/targets")
    parser.add_argument("--seed-archive", type=Path)
    parser.add_argument("--archive-sha256")
    args = parser.parse_args()
    task = Collector(args.destination, args.workers, args.kind)
    if args.http_cache:
        task.http = HttpCache(args.http_cache, task.stop)
    signal.signal(signal.SIGINT, lambda *_: task.stop.set())
    if args.seed_archive:
        task.ledger.seed_archive(args.seed_archive,args.archive_sha256 or "")
    task.refresh_selected = args.refresh_selected
    task.http.refresh = args.refresh_selected
    if args.resume_inventory:
        inventory = json.loads((task.root / "inventory.json").read_text(encoding="utf-8"))
        for profile in inventory["profiles"]:
            for item in profile.get("indexes", []):
                task.put((task.root / "bundle" / item["blob"]).read_bytes(), item["filename"])
    else:
        inventory = task.scan(args.release or task.releases(), args.target or TARGETS)
    if args.scan_only:
        task.status("scan-complete", errors=len(inventory["errors"]))
        return
    failures = task.fetch_packages(inventory)
    result = task.finish(inventory, failures)
    if not result["complete"]:
        sys.exit(2)
    if args.deliver:
        import shutil
        from modules.capture_services.openwrt.common_bundle_manifest import sha256_file
        destination = ROOT / "dist" / task.spec.directory
        destination.mkdir(parents=True, exist_ok=True)
        archive = Path(result["zip_path"])
        delivery = destination / archive.name
        if delivery.exists() and sha256_file(delivery) != result["zip_sha256"]:
            raise ValueError("Delivery filename already belongs to different verified content; retain both archives")
        if not delivery.exists():
            shutil.copyfile(archive, delivery)
        if sha256_file(delivery) != result["zip_sha256"]:
            raise ValueError("Delivery copy fingerprint mismatch")
        result["zip_path"] = str(delivery)
        (destination / (archive.stem + ".report.json")).write_bytes(json_bytes(result))
        from scripts.openwrt_packages.support_inventory import write_inventory
        write_inventory(args.kind, delivery)
        from scripts.build_cleanup import cleanup_offline
        cleanup_offline(Path(__file__).resolve().parents[2], task.root, delivery)



if __name__ == "__main__":
    main()
