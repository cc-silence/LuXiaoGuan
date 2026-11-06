"""Prepare, sign and publish verified RouterManager offline dependency bundles."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import zipfile

from modules.capture_services.openwrt.bundle_metadata import write_metadata
from modules.capture_services.openwrt.bundle_specs import bundle_spec
from modules.capture_services.openwrt.common_bundle_manifest import verify_bundle_manifest, sha256_file
from modules.capture_services.openwrt.common_bundle_repository import validate_catalog
from modules.app_update_services.manifest import canonical, https_url
from .signing import sign
from .cloudflare import ROOT


def gateway():
    from modules.app_update_services.channel import GATEWAY
    if not GATEWAY:
        raise ValueError("Deploy and configure the RouterManager gateway first")
    return https_url(GATEWAY)


def prepare(kind, archive, coverage, *, min_app_version=None):
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
        from modules.app_update_services.manifest import version_key
        declared_minimum = catalog.get("min_app_version")
        if min_app_version is not None:
            version_key(min_app_version)
            if declared_minimum is not None and version_key(min_app_version) < version_key(declared_minimum):
                raise ValueError("Minimum application version cannot weaken the catalog requirement")
        minimum = min_app_version if min_app_version is not None else declared_minimum
        if kind == "capture" and not catalog.get("hcx_variants"):
            raise ValueError("Rebuild the capture bundle with designated hcx payloads before publication")
        if (evidence["catalog_sha256"] != hashlib.sha256(raw).hexdigest()
                or evidence["profiles_verified"] != len(catalog["profiles"])
                or evidence["objects_verified"] != len(objects)):
            raise ValueError("Coverage audit belongs to a different archive")
        names = zipped.namelist()
        if len(names) != len(set(names)) or len(names) > 30000:
            raise ValueError("Archive contains duplicate or too many entries")
        if set(names) != {"catalog.json"} | {"objects/"+sha for sha in objects}:
            raise ValueError("Archive contains missing or unreferenced objects")
    folder = ROOT / "build/publishing" / kind / digest
    folder.mkdir(parents=True, exist_ok=True)
    manifest = folder / "manifest.json"
    if manifest.exists():
        payload = verify_bundle_manifest(manifest.read_bytes(), kind=kind)
        if payload["sha256"] != digest or payload["filename"] != archive.name:
            raise ValueError("Publication retry points to different content or asset filename")
        if payload.get("min_app_version") != minimum:
            raise ValueError("Publication retry has different compatibility requirements; prepare a new archive")
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
        if minimum is not None:
            payload["min_app_version"] = minimum
        signed = sign(payload)
        verify_bundle_manifest(signed, kind=kind)
        write_metadata(manifest, signed)
    return archive, manifest, payload



def publish(kind, archive, coverage, *, min_app_version=None):
    """Compatibility entry point; all new bundles use the GitHub coordinator."""
    from .mirrored import publish_offline
    return publish_offline(kind,archive,coverage,min_app_version=min_app_version)
