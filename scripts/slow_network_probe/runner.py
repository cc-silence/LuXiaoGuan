"""Compare existing and diagnostic budgets on explicitly identified routes."""
import argparse
import re
from datetime import datetime, timezone
import json
from pathlib import Path
import signal
import threading
import time

from modules.app_update_services.channel import GATEWAY
from modules.app_update_services.manifest import verify_manifest
from modules.app_update_services.trust import PUBLIC_KEY
from modules.app_update_services.catalog import parse_catalog
from modules.capture_services.openwrt.common_bundle_manifest import verify_bundle_manifest
from modules.network_policy import NetworkChannel, allowed_channels
from .transport import ROOT, probe

RELAXED = dict(name="long_wait", connect=45, idle=60, total=180)
BASELINES = {
    "app": dict(name="current", connect=12, idle=12, total=24),
    "blob": dict(name="current", connect=5, idle=5, total=300),
    "offline_check": dict(name="current", connect=10, idle=30, total=60),
    "offline_download": dict(name="current", connect=10, idle=30, total=900),
}


class Runner:
    def __init__(self, output, cancel, headers=None, sample_only=False, seed_directory=None):
        self.seed_directory = Path(seed_directory) if seed_directory else None
        self.output = Path(output)
        self.cancel = cancel
        self.headers = headers or {}
        self.sample_only = sample_only
        self.report = dict(schema=1, created_utc=datetime.now(timezone.utc).isoformat(),
                           scope="local computer and network only", production_changed=False,
                           profiles=dict(long_wait=RELAXED, baselines=BASELINES),
                           notes=[
                               "Qt is used for app metadata; Requests for offline packages and blobs.",
                               "Each selected route is isolated; no implicit proxy fallback.",
                               "Current offline checks use 60s and downloads 900s, isolated to one route.",
                               "Whole app manifest+catalog long-wait check shares a 180-second budget.",
                               "Current Qt check has a 24s manifest budget and resets to 36s for the catalog.",
                               "Each probe starts a fresh client; production may reuse a connection.",
                               "Requests cannot expose exact TCP/TLS completion; ambiguous stages remain explicit.",
                               "Only one representative signed incremental blob is tested.",
                           ], records=[])
        self.full_verified = set()
        self.download_budget = 1024**3

    def save(self):
        self.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.output.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.report, indent=2), encoding="utf-8")
        temporary.replace(self.output)

    def request(self, label, url, route, profile, **kwargs):
        if self.cancel.is_set():
            return None, None
        headers = (dict(self.headers) if kwargs.get("engine") == "qt"
                   and url.startswith(GATEWAY + "/") else {})
        if kwargs.get("engine") == "qt":
            identity = ROOT / "probe-data/identity.json"
            version = (json.loads(identity.read_text(encoding="utf-8"))["app_version"]
                       if identity.is_file() else re.search(r'APP_VERSION\s*=\s*["\']([^"\']+)',
                           (ROOT / "main.py").read_text(encoding="utf-8")).group(1))
            headers.update({"User-Agent": "RouterManager/" + version, "Accept": "application/json"})
        elif label.startswith(("common_", "capture_")):
            headers["Cache-Control"] = "no-cache"
        record, body = probe(url, route, profile, headers=headers, cancel=self.cancel, **kwargs)
        record["target"] = label
        record["budgets_seconds"] = profile.copy()
        self.report["records"].append(record)
        self.save()
        print(f'{label} {route.name} {profile["name"]}: {record["reason"]} '
              f'({record["elapsed_seconds"]}s, {record["received_bytes"]} bytes)', flush=True)
        return record, body

    def validate(self, record, body, verifier):
        if record and record["outcome"] == "success":
            try:
                result = verifier(body)
                record["signature_or_catalog_verified"] = True
                self.save()
                return result
            except Exception as exc:
                record.update(outcome="failed", reason="signed_metadata_invalid",
                              validation_error_type=type(exc).__name__)
                self.save()
        return None

    def seed(self, name, verifier):
        if self.seed_directory is None or self.cancel.is_set():
            return None
        path = self.seed_directory / (name + ".json")
        record = dict(target=name, source="bundled_signed_snapshot",
                      outcome="failed", reason="snapshot_invalid")
        try:
            limit = 8*1024*1024 if name == "app_catalog" else 65536
            if path.stat().st_size > limit:
                raise ValueError("Snapshot size limit")
            value = verifier(path.read_bytes())
            record.update(outcome="snapshot_loaded", reason="signed_snapshot_verified")
        except Exception as exc:
            record["validation_error_type"] = type(exc).__name__
            value = None
        self.report["records"].append(record)
        self.save()
        return value

    def artifact(self, label, item, route, category):
        if self.cancel.is_set():
            return
        expected = dict(size=item["size"], sha256=item["sha256"])
        long_record = None
        for profile in (BASELINES[category], RELAXED):
            record, _ = self.request(label, item["url"], route, profile,
                                     mode="sample", limit=min(65536, item["size"]), expected=expected)
            if profile is RELAXED:
                long_record = record
        if not long_record or long_record["outcome"] != "success" or self.sample_only:
            return
        identity = item["sha256"]
        if long_record.get("verified"):
            self.full_verified.add(identity)
        if identity in self.full_verified:
            return
        if item["size"] > self.download_budget:
            self.report["records"].append(dict(target=label, route=route.name,
                outcome="skipped", reason="run_download_budget"))
            self.save()
            return
        self.download_budget -= item["size"]
        full = {**RELAXED, "name": "long_wait_full", "total": 900}
        record, _ = self.request(label, item["url"], route, full, mode="full",
                                 limit=item["size"], expected=expected)
        if record and record["outcome"] == "success":
            self.full_verified.add(identity)

    def discover(self, label, url, routes, verifier, engine):
        for route in routes:
            record, body = self.request(label + "_discovery", url, route,
                                        RELAXED, engine=engine)
            value = self.validate(record, body, verifier)
            if value is not None:
                return value
        return None

    def app(self, route, discovery):
        release = None
        catalog = None
        url = GATEWAY + "/manifest"
        for profile in (BASELINES["app"], RELAXED):
            start = time.monotonic()
            record, raw = self.request("app_manifest", url, route, profile, engine="qt")
            candidate = self.validate(record, raw, lambda data: verify_manifest(data, PUBLIC_KEY))
            if candidate is not None:
                release = candidate
                remaining = (36 if profile["name"] == "current" else
                             profile["total"] - (time.monotonic() - start))
                if candidate.catalog_url and remaining > 0.1:
                    catalog_profile = {**profile, "total": remaining}
                    rec, data = self.request("app_catalog", candidate.catalog_url, route,
                        catalog_profile, engine="qt", limit=8*1024*1024)
                    checked = self.validate(rec, data, lambda body: parse_catalog(body, candidate))
                    if checked is not None:
                        catalog = checked
        if release is None:
            release = self.discover("app_manifest", url, discovery,
                                   lambda data: verify_manifest(data, PUBLIC_KEY), "qt")
        if release is None:
            release = self.seed("app_manifest", lambda data: verify_manifest(data, PUBLIC_KEY))
        if release is None:
            self.report["records"].append(dict(target="app_downloads", route=route.name,
                outcome="skipped", reason="no_verified_manifest"))
            self.save()
            return
        if catalog is None and release.catalog_url:
            # Even when checking fails, compare the verified catalog endpoint as a separate request.
            for profile in (BASELINES["app"], RELAXED):
                rec, data = self.request("app_catalog_standalone", release.catalog_url,
                    route, profile, engine="qt", limit=8*1024*1024)
                checked = self.validate(rec, data, lambda body: parse_catalog(body, release))
                if checked is not None:
                    catalog = checked
            if catalog is None:
                for discovery_route in discovery:
                    rec, data = self.request("app_catalog_discovery", release.catalog_url,
                        discovery_route, RELAXED, engine="qt", limit=8*1024*1024)
                    catalog = self.validate(rec, data, lambda body: parse_catalog(body, release))
                    if catalog is not None:
                        break
        if catalog is None:
            catalog = self.seed("app_catalog", lambda data: parse_catalog(data, release))
        if catalog is not None and catalog.get("files"):
            entry = max(catalog["files"], key=lambda item: item["blob_size"])
            base = release.catalog_url.split("/catalog/")[0]
            self.artifact("app_incremental_blob", dict(
                url=base+"/blobs/"+entry["blob_sha256"]+".z",
                size=entry["blob_size"], sha256=entry["blob_sha256"]), route, "blob")
        else:
            self.report["records"].append(dict(target="app_incremental_blob", route=route.name,
                outcome="skipped", reason="no_verified_catalog"))
            self.save()
        self.artifact("app_full_package", dict(url=release.url, size=release.size,
                      sha256=release.sha256), route, "blob")

    def offline(self, kind, route, discovery):
        url = GATEWAY + "/offline/openwrt-" + kind + "/manifest"
        manifest = None
        verifier = lambda data: verify_bundle_manifest(data, kind=kind)
        for profile in (BASELINES["offline_check"], RELAXED):
            record, body = self.request(kind+"_manifest", url, route, profile)
            checked = self.validate(record, body, verifier)
            if checked is not None:
                manifest = checked
        if manifest is None:
            manifest = self.discover(kind+"_manifest", url, discovery, verifier, "requests")
        if manifest is None:
            manifest = self.seed(kind+"_manifest", verifier)
        if manifest is not None:
            self.artifact(kind+"_archive", manifest, route, "offline_download")
        else:
            self.report["records"].append(dict(target=kind+"_archive", route=route.name,
                outcome="skipped", reason="no_verified_manifest"))
            self.save()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--route", default="direct",
                        choices=["direct", "http_proxy", "socks_proxy", "system_proxy"])
    parser.add_argument("--discover-via-proxy", action="store_true",
                        help="Use configured proxies only to discover signed targets if metadata fails.")
    parser.add_argument("--sample-only", action="store_true",
                        help="Skip complete archive downloads; never claim hash verification.")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    cancel = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: cancel.set())
    configured = [c for c in allowed_channels(None, "proxy") if c.proxy_url]
    routes = ([NetworkChannel("direct", "Direct")] if args.route == "direct" else
              [c for c in configured if c.name == args.route])
    if not routes:
        parser.error("Selected route is not configured/enabled; no implicit fallback.")
    discovery = configured[:1] if args.discover_via_proxy else []
    from modules.platform_services.app_update_access import read_code
    code = read_code()
    headers = {"X-RouterManager-Test-Code": code} if code else {}
    output = args.output or ROOT / "build/network-probes" / (
        datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + args.route + ".json")
    runner = Runner(output, cancel, headers, args.sample_only)
    runner.report.update(sample_only=args.sample_only, selected_route=args.route,
                         discovery_proxy_enabled=args.discover_via_proxy)
    runner.save()
    try:
        for route in routes:
            runner.app(route, discovery)
            for kind in ("common", "capture"):
                runner.offline(kind, route, discovery)
    finally:
        runner.report["cancelled"] = cancel.is_set()
        runner.save()
    print("Report: " + str(output))
    return 130 if cancel.is_set() else 0


if __name__ == "__main__":
    raise SystemExit(main())
