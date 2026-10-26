"""Short local version labels, with unchanged signed bundle identities.

revision.json is only a local locator. It never replaces signed catalog checks
or full object SHA-256 verification. Legacy directories remain readable.
"""
from datetime import datetime
import json
import re
from .common_bundle_repository import safe_path
from .common_bundle_manifest import sha256_file

_SHA = re.compile(r"[a-f0-9]{64}")
_LABEL = re.compile(r"[0-9]{8}-[0-9]{2,4}")
_MAX_VERSIONS = 4096


def revision_id(directory, kind):
    """Identify managed legacy/short folders without interpreting arbitrary paths."""
    if _SHA.fullmatch(directory.name):
        return directory.name
    if not _LABEL.fullmatch(directory.name):
        return None
    try:
        marker = safe_path(directory, "revision.json")
        if not marker.is_file() or marker.stat().st_size > 512:
            return None
        value = json.loads(marker.read_text(encoding="utf-8"))
        if (isinstance(value, dict) and value.get("schema") == 1 and value.get("kind") == kind
                and isinstance(value.get("sha256"), str) and _SHA.fullmatch(value["sha256"])):
            return value["sha256"]
    except (OSError, ValueError):
        pass
    return None


def version_path(root, sha256, kind="common", *, payload=None):
    """Resolve by complete identity; a date alone never selects a version."""
    if not _SHA.fullmatch(sha256):
        raise ValueError("Invalid bundle identity")
    legacy = safe_path(root, "versions/" + sha256)
    candidates = [legacy] if legacy.is_dir() else []
    versions = safe_path(root, "versions")
    if not versions.is_dir():
        return candidates[0] if candidates else None
    for count, entry in enumerate(versions.iterdir(), 1):
        if count > _MAX_VERSIONS:
            raise ValueError("Too many local bundle directories")
        if not _LABEL.fullmatch(entry.name):
            continue
        try:
            directory = safe_path(root, "versions/" + entry.name)
            identity = revision_id(directory, kind)
        except (OSError, ValueError):
            continue
        if identity == sha256:
            candidates.append(directory)
    candidates.sort(key=lambda p: (p.name != sha256, p.name))
    if payload is not None and len(candidates) > 1:
        # An incomplete old copy must not hide an already prepared resource.
        # Only duplicate locations need this additional catalog check.
        for candidate in candidates:
            try:
                catalog = safe_path(candidate, "catalog.json")
                if (catalog.is_file() and catalog.stat().st_size == payload["catalog_size"]
                        and sha256_file(catalog) == payload["catalog_sha256"]):
                    return candidate
            except (OSError, ValueError):
                continue
    return candidates[0] if candidates else None


def reserve_version(root, sha256, kind="common", *, payload=None):
    """Caller holds storage lease; reserve once, reuse partial downloads safely."""
    existing = version_path(root, sha256, kind, payload=payload)
    if existing is not None:
        return existing
    versions = safe_path(root, "versions")
    versions.mkdir(parents=True, exist_ok=True)
    date = datetime.now().strftime("%Y%m%d")
    for sequence in range(1, 10000):
        directory = safe_path(root, "versions/" + date + "-%02d" % sequence)
        try:
            directory.mkdir()
        except FileExistsError:
            continue
        safe_path(directory, "revision.json").write_text(
            json.dumps(dict(schema=1, kind=kind, sha256=sha256), separators=(",", ":")), encoding="utf-8")
        return directory
    raise ValueError("Local bundle date sequence exhausted")
