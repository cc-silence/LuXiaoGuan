"""Prepare, sign and publish verified RouterManager offline dependency bundles."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from threading import Event
import time
import tempfile
import zipfile

from modules.capture_services.openwrt.bundle_specs import bundle_spec
from modules.capture_services.openwrt.common_bundle_manifest import verify_bundle_manifest, sha256_file
from modules.capture_services.openwrt.common_bundle_repository import validate_catalog
from modules.capture_services.openwrt.common_bundle_download import _receive
from modules.app_update_services.manifest import canonical, https_url
from .signing import sign
from .cloudflare import ROOT, upload
from .storage import ensure_space, publication_lock
from .retention import cleanup_retention


def gateway():
    from modules.app_update_services.channel import GATEWAY
    if not GATEWAY:
        raise ValueError("Deploy and configure the RouterManager gateway first")
    return https_url(GATEWAY)


def prepare(kind, archive, coverage):
    spec = bundle_spec(kind)
    archive = Path(archive).resolve()
    if not archive.name.startswith(spec.filename_prefix):
        raise ValueError("Use a RouterManager-owned offline archive")
    report_path = archive.with_suffix(".report.json")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    digest = sha256_file(archive)
    if (not report.get("complete") or report["zip_sha256"] != digest
            or report["zip_bytes"] != archive.stat().st_size):
        raise ValueError("Archive differs from its verified build report")
    evidence = json.loads(Path(coverage).read_text(encoding="utf-8"))
    with zipfile.ZipFile(archive) as zipped:
        raw = zipped.read("catalog.json")
        catalog = json.loads(raw)
        objects = validate_catalog(catalog, kind)
        if (evidence["catalog_sha256"] != hashlib.sha256(raw).hexdigest()
                or evidence["profiles_verified"] != len(catalog["profiles"])
                or evidence["objects_verified"] != len(objects)):
            raise ValueError("Coverage audit belongs to a different archive")
        if set(zipped.namelist()) != {"catalog.json"} | {"objects/"+sha for sha in objects}:
            raise ValueError("Archive contains missing or unreferenced objects")
    folder = ROOT / "build/publishing" / kind / digest
    folder.mkdir(parents=True, exist_ok=True)
    manifest = folder / "manifest.json"
    if manifest.exists():
        payload = verify_bundle_manifest(manifest.read_bytes(), kind=kind)
        if payload["sha256"] != digest:
            raise ValueError("Publication retry points to different content")
    else:
        now = datetime.now(timezone(timedelta(hours=8)))
        payload = dict(schema=1, product="RouterManager", purpose=spec.purpose,
            revision=now.strftime("%Y%m%d%H%M%S"), sha256=digest,
            size=archive.stat().st_size, filename=archive.name,
            catalog_sha256=hashlib.sha256(raw).hexdigest(), catalog_size=len(raw),
            unpacked_size=len(raw)+sum(row["size"] for row in objects.values()),
            profiles=len(catalog["profiles"]), releases=catalog["releases"],
            architectures=sorted({p["architecture"] for p in catalog["profiles"]}),
            published_at=now.isoformat(timespec="seconds"),
            url=gateway()+"/offline/"+spec.namespace+"/files/"+digest+"/"+archive.name)
        manifest.write_bytes(sign(payload))
        verify_bundle_manifest(manifest.read_bytes(), kind=kind)
    return archive, manifest, payload


def read_public(url, path, size):
    _receive(url, path, size, Event(), lambda *_: None, time.monotonic()+900)


@publication_lock
def publish(kind, archive, coverage):
    archive, manifest, payload = prepare(kind, archive, coverage)
    spec = bundle_spec(kind)
    key = "offline/"+spec.namespace+"/files/"+payload["sha256"]+"/"+archive.name
    immutable = "offline/"+spec.namespace+"/manifests/"+payload["sha256"]+".json"
    ensure_space({"manifest": {"key": immutable, "size": manifest.stat().st_size}},
                 archive, manifest, payload, "channel/"+spec.namespace+".json")
    upload(archive, key, "application/zip")
    # Verify actual public bytes before switching the independently signed channel.
    with tempfile.TemporaryDirectory(prefix="public-offline-", dir=manifest.parent) as temporary:
        downloaded = Path(temporary) / archive.name
        read_public(payload["url"], downloaded, payload["size"])
        if downloaded.stat().st_size != payload["size"] or sha256_file(downloaded) != payload["sha256"]:
            raise ValueError("Public offline ZIP fingerprint mismatch; channel not changed")
    immutable = "offline/"+spec.namespace+"/manifests/"+payload["sha256"]+".json"
    upload(manifest, immutable, "application/json")
    upload(manifest, "channel/"+spec.namespace+".json", "application/json")
    raw = _receive(gateway()+"/offline/"+spec.namespace+"/manifest", None, 65536,
        Event(), lambda *_: None, time.monotonic()+60)
    current = verify_bundle_manifest(raw, kind=kind)
    if raw != manifest.read_bytes() or current["sha256"] != payload["sha256"]:
        raise ValueError("Public offline channel verification failed")
    cleanup_retention()
    result = dict(kind=kind, status="published-and-client-verified", manifest_url=
        gateway()+"/offline/"+spec.namespace+"/manifest", archive_url=payload["url"],
        sha256=payload["sha256"], profiles=payload["profiles"], size=payload["size"])
    (manifest.parent/"publication.json").write_bytes(canonical(result))
    from scripts.openwrt_packages.support_inventory import write_inventory
    write_inventory(kind, archive, coverage, result)
    return result
