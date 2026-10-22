"""RouterManager \u5ba2\u6237\u7aef\u548c\u53d1\u5e03\u5de5\u5177\u5171\u7528\u7684\u7b7e\u540d\u5408\u7ea6\u3002"""
from __future__ import annotations
import base64
import json
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

MAX_MANIFEST_BYTES = 64 * 1024
MAX_PACKAGE_BYTES = 2 * 1024 ** 3 - 1
_VERSION = re.compile(r"(\d+(?:\.\d+){1,3})(?:[_-](dev|alpha|beta|rc)(?:[._-]?(\d+))?)?")


def version_key(value: str) -> tuple:
    """\u6bd4\u8f83\u6570\u5b57\u7248\u672c\u3001\u9636\u6bb5\u548c\u5c3e\u53f7\uff1b\u62d2\u7edd\u672a\u77e5\u683c\u5f0f\u3002"""
    match = _VERSION.fullmatch(value) if isinstance(value, str) else None
    if not match or len(value) > 64:
        raise ValueError("Invalid RouterManager version")
    parts = tuple(map(int, match[1].split(".")))
    return (parts + (0,) * (4 - len(parts)),
            {"dev": 0, "alpha": 1, "beta": 2, "rc": 3, None: 4}[match[2]],
            int(match[3] or 0))


def https_url(value: str) -> str:
    """\u62d2\u7edd\u975e HTTPS\u3001\u7528\u6237\u51ed\u636e\u3001\u63a7\u5236\u5b57\u7b26\u548c\u65e0\u6548\u7aef\u53e3\u3002"""
    if not isinstance(value, str) or len(value) > 2048 or any(ord(c) <= 32 for c in value):
        raise ValueError("Invalid update URL")
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.fragment or parsed.port not in (None, 443)):
        raise ValueError("Update URL must use HTTPS without credentials")
    return value


def canonical(payload: dict) -> bytes:
    """\u7528\u786e\u5b9a\u7684 UTF-8 \u5e8f\u5217\u5316\u7b7e\u540d\u548c\u9a8c\u7b7e\u3002"""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


@dataclass(frozen=True)
class Release:
    version: str
    notes: str
    url: str
    sha256: str
    size: int
    published_at: str
    catalog_url: str = ""
    catalog_sha256: str = ""
    catalog_size: int = 0
    catalog: dict | None = None
    resource_revision: int = 0


def parse_payload(payload: dict) -> Release:
    """\u9a8c\u8bc1\u53d1\u5e03\u5bf9\u8c61\u548c\u5927\u5c0f\uff0c\u62d2\u7edd\u9519\u8bef\u6e05\u5355\u3002"""
    if (not isinstance(payload, dict) or payload.get("schema") != 1
            or payload.get("product") != "RouterManager" or payload.get("platform") != "win-x64"):
        raise ValueError("Unsupported RouterManager update manifest")
    version_key(payload.get("version"))
    notes = payload.get("notes")
    if not isinstance(notes, str) or len(notes) > 16000:
        raise ValueError("Invalid update notes")
    digest = payload.get("sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Invalid package SHA256")
    size = payload.get("size")
    if type(size) is not int or not 1 <= size <= MAX_PACKAGE_BYTES:
        raise ValueError("Invalid package size")
    published = payload.get("published_at")
    if not isinstance(published, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+]08:00", published):
        raise ValueError("Invalid publication date")
    catalog_url = payload.get("catalog_url", "")
    catalog_sha = payload.get("catalog_sha256", "")
    catalog_size = payload.get("catalog_size", 0)
    if catalog_url:
        https_url(catalog_url)
        from .catalog import digest as check_digest, MAX_CATALOG_BYTES
        check_digest(catalog_sha)
        if type(catalog_size) is not int or not 1 <= catalog_size <= MAX_CATALOG_BYTES:
            raise ValueError("Invalid catalog size")
    revision = payload.get("resource_revision", 0)
    if type(revision) is not int or not 0 <= revision <= 1000000000:
        raise ValueError("Invalid resource revision")
    return Release(payload["version"], notes, https_url(payload.get("url")),
                   digest, size, published, catalog_url, catalog_sha, catalog_size, None, revision)


def verify_manifest(data: bytes, public_key: str) -> Release:
    """\u5148\u9a8c\u7b7e\u518d\u4f7f\u7528\u5730\u5740\uff0c\u4fe1\u4efb\u516c\u94a5\u53ea\u4ece\u7a0b\u5e8f\u5185\u7f6e\u503c\u83b7\u53d6\u3002"""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    if len(data) > MAX_MANIFEST_BYTES:
        raise ValueError("Update manifest exceeds size limit")
    envelope = json.loads(data.decode("utf-8"))
    if not isinstance(envelope, dict) or set(envelope) != {"payload", "signature"}:
        raise ValueError("Invalid signed manifest envelope")
    key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key, validate=True))
    key.verify(base64.b64decode(envelope["signature"], validate=True), canonical(envelope["payload"]))
    return parse_payload(envelope["payload"])
