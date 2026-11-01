"""Real signed capture fixtures with the original hcx payload bytes."""
import base64
import hashlib
import json
import zipfile
from pathlib import Path
from threading import Event

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from modules.app_update_services.manifest import canonical
from scripts.openwrt_packages.collect_common_bundle import Collector
from scripts.openwrt_packages.hcx_payloads import collect_hcx_payloads
from tests.test_common_offline_bundle import fixture


def hcx_fixture(directory, apk=False):
    f = fixture(Path(directory) / "fixture", apk=apk, kind="capture")
    task = Collector(Path(directory) / "collector", kind="capture")
    f.catalog["hcx_variants"] = collect_hcx_payloads(task)
    for variant in f.catalog["hcx_variants"]:
        for row in variant["packages"]:
            f.blobs[row["sha256"]] = (task.root / "bundle" / row["blob"]).read_bytes()
    # Regular runtime libraries remain selected from the device's exact profile.
    branch = "25.12" if apk else "24.10"
    selected = next(v for v in f.catalog["hcx_variants"]
                    if v["branch"] == branch and v["architecture"] == f.profile["architecture"])
    for row in selected["packages"]:
        if row["name"] != "hcxdumptool":
            f.profile["packages"].append(dict(row, depends=["libc"], provides=[]))
    resign(f)
    return f


def resign(f):
    raw = canonical(f.catalog)
    with zipfile.ZipFile(f.archive, "w", zipfile.ZIP_DEFLATED) as zipped:
        zipped.writestr("catalog.json", raw)
        for sha, data in f.blobs.items():
            zipped.writestr("objects/" + sha, data)
    digest = hashlib.sha256(f.archive.read_bytes()).hexdigest()
    f.payload.update(sha256=digest, size=f.archive.stat().st_size,
                     catalog_sha256=hashlib.sha256(raw).hexdigest(), catalog_size=len(raw),
                     unpacked_size=len(raw) + sum(map(len, f.blobs.values())))
    f.payload["url"] = f.payload["url"].replace(f.payload["url"].split("/")[-2], digest)
    private = Ed25519PrivateKey.generate()
    f.public = base64.b64encode(private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
    f.envelope = canonical(dict(payload=f.payload, signature=base64.b64encode(
        private.sign(canonical(f.payload))).decode()))


def prepare_hcx(f, root):
    from modules.capture_services.openwrt.common_bundle_download import prepare_archive
    from modules.capture_services.openwrt.common_bundle_repository import CommonBundleRepository
    prepare_archive(f.archive, f.envelope, root, Event(), public_key=f.public, kind="capture")
    return CommonBundleRepository(root, f.public, kind="capture")
