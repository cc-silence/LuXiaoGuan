"""Signed identity for independently updated OpenWrt common-tool bundles."""
import base64
import hashlib
import json
import re
from urllib.parse import urlsplit
from modules.app_update_services.manifest import canonical
from modules.app_update_services.trust import PUBLIC_KEY

from modules.app_update_services.channel import GATEWAY
MANIFEST_URL = GATEWAY + "/offline/openwrt-common/manifest"
MAX_ARCHIVE = 512 * 1024 * 1024
MAX_CATALOG = 32 * 1024 * 1024
from .bundle_specs import COMMON_ROOTS, bundle_spec



def verify_bundle_manifest(raw, public_key=PUBLIC_KEY, kind="common"):
    """\u9a8c\u8bc1\u8d44\u6e90\u6e05\u5355\u7b7e\u540d\u548c\u8fb9\u754c\uff0c\u4e0d\u4fe1\u4efb\u672a\u7b7e\u540d\u5730\u5740\u3002"""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    spec = bundle_spec(kind)
    if not 1 <= len(raw) <= 65536:
        raise ValueError("\u79bb\u7ebf\u5305\u6e05\u5355\u5927\u5c0f\u65e0\u6548\u3002")
    envelope = json.loads(raw.decode("utf-8"))
    if not isinstance(envelope, dict) or set(envelope) != {"payload", "signature"}:
        raise ValueError("\u79bb\u7ebf\u5305\u7b7e\u540d\u683c\u5f0f\u65e0\u6548\u3002")
    key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key, validate=True))
    try:
        key.verify(base64.b64decode(envelope["signature"], validate=True), canonical(envelope["payload"]))
    except InvalidSignature:
        raise ValueError("\u79bb\u7ebf\u5305\u7b7e\u540d\u6821\u9a8c\u5931\u8d25\u3002") from None
    p = envelope["payload"]
    if p.get("schema") != 1 or p.get("product") != "RouterManager" or p.get("purpose") != spec.purpose:
        raise ValueError("\u4e0b\u8f7d\u8d44\u6e90\u7c7b\u578b\u4e0d\u5339\u914d\u3002")
    for field in ("sha256", "catalog_sha256"):
        if not re.fullmatch("[a-f0-9]{64}", str(p.get(field, ""))):
            raise ValueError("\u79bb\u7ebf\u5305\u6307\u7eb9\u65e0\u6548\u3002")
    if type(p.get("size")) is not int or not 1 <= p["size"] <= MAX_ARCHIVE:
        raise ValueError("\u79bb\u7ebf\u5305\u5bb9\u91cf\u65e0\u6548\u3002")
    if type(p.get("catalog_size")) is not int or not 1 <= p["catalog_size"] <= MAX_CATALOG:
        raise ValueError("\u79bb\u7ebf\u7d22\u5f15\u5bb9\u91cf\u65e0\u6548\u3002")
    if type(p.get("unpacked_size")) is not int or not 1 <= p["unpacked_size"] <= 1024**3:
        raise ValueError("Invalid signed unpacked size")
    filename = str(p.get("filename", ""))
    if not re.fullmatch(re.escape(spec.filename_prefix) + r"[0-9A-Za-z_-]+\.zip", filename):
        raise ValueError("\u79bb\u7ebf\u5305\u6587\u4ef6\u540d\u65e0\u6548\u3002")
    expected = GATEWAY + "/offline/" + spec.namespace + "/files/" + p["sha256"] + "/" + filename
    if p.get("url") != expected:
        raise ValueError("\u79bb\u7ebf\u5305\u5730\u5740\u4e0e\u6307\u7eb9\u4e0d\u5339\u914d\u3002")
    return p


def sha256_file(path, cancel=None):
    """\u6d41\u5f0f\u6821\u9a8c\u6587\u4ef6\uff0c\u5141\u8bb8\u4e2d\u9014\u53d6\u6d88\u3002"""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            if cancel is not None and cancel.is_set():
                raise InterruptedError("\u79bb\u7ebf\u5305\u64cd\u4f5c\u5df2\u53d6\u6d88\u3002")
            digest.update(block)
    return digest.hexdigest()
