"""Retire obsolete managed bundles only after verified activation.

No recursive deletion: inventories are validated before deleting known regular
files. Unknown files, links, the active revision and external exports survive.
The caller holds the common storage lease across activation and this cleanup.
"""
import json
from pathlib import Path
import re
import time
from .bundle_specs import bundle_spec
from .bundle_layout import revision_id, version_path
from .common_bundle_manifest import MAX_CATALOG, sha256_file
from .common_bundle_repository import safe_path, validate_catalog
from .offline_cleanup import report_cleanup_warning

_SHA = re.compile(r"[a-f0-9]{64}")
_WARNING = "\u65b0\u7248\u79bb\u7ebf\u5305\u5df2\u542f\u7528\uff1b\u90e8\u5206\u65e7\u8d44\u6e90\u672a\u80fd\u6e05\u7406\uff0c\u4e0b\u6b21\u6210\u529f\u4e0b\u8f7d\u540e\u4f1a\u518d\u6b21\u5c1d\u8bd5\u3002"


class _Budget:
    def __init__(self, cancel):
        self.cancel = cancel
        self.deadline = time.monotonic() + 30

    def is_set(self):
        return self.cancel.is_set() or time.monotonic() >= self.deadline

    def check(self):
        if self.is_set():
            raise InterruptedError("Bundle cleanup cancelled or timed out")


def _current(root, manifest, budget):
    budget.check()
    receipt = safe_path(root, "current.json")
    if receipt.stat().st_size > 65536 or receipt.read_bytes() != manifest:
        raise InterruptedError("Active bundle changed during cleanup")


def _old_version(root, directory, kind, budget):
    version = safe_path(root, "versions/" + directory.name)
    if not version.is_dir() or revision_id(version, kind) is None:
        return None
    compact = not _SHA.fullmatch(version.name)
    allowed = {"catalog.json", "objects"} | ({"revision.json"} if compact else set())
    if {p.name for p in version.iterdir()} - allowed:
        return None
    catalog = safe_path(version, "catalog.json")
    if catalog.is_file():
        if catalog.stat().st_size > MAX_CATALOG:
            return None
        objects = validate_catalog(json.loads(catalog.read_text(encoding="utf-8")), kind)
        metadata = [catalog]
    elif compact:
        # Resume a cleanup interrupted between catalog and locator deletion.
        objects, metadata = {}, []
    else:
        return None
    if compact:
        metadata.append(safe_path(version, "revision.json"))
    data = safe_path(version, "objects")
    files = []
    if data.exists():
        if not data.is_dir():
            return None
        for entry in data.iterdir():
            budget.check()
            path = safe_path(version, "objects/" + entry.name)
            if entry.name not in objects or entry.is_symlink() or not path.is_file():
                return None
            files.append(path)
    return files, metadata


def retire_old_bundles(root, payload, manifest, cancel, tell=None, kind="common"):
    """Best effort after activation; failure must not undo a successful update."""
    root = Path(root)
    keep = payload["sha256"]
    budget = _Budget(cancel)
    removed = size = 0
    failed = False
    try:
        _current(root, manifest, budget)
        current = version_path(root, keep, kind, payload=payload)
        if current is None:
            raise ValueError("Active resource is missing")
        versions = safe_path(root, "versions")
        if versions.is_dir():
            for directory in versions.iterdir():
                if directory.name == current.name:
                    continue
                _current(root, manifest, budget)
                try:
                    inventory = _old_version(root, directory, kind, budget)
                    if inventory is None:
                        continue
                    files, metadata = inventory
                    for path in files:
                        _current(root, manifest, budget)
                        # Recheck every target immediately before unlinking.
                        path = safe_path(root, path.relative_to(root))
                        count = path.stat().st_size
                        path.unlink()
                        size += count
                    data = safe_path(root, "versions/" + directory.name + "/objects")
                    if data.is_dir():
                        data.rmdir()
                    for path in metadata:
                        _current(root, manifest, budget)
                        path = safe_path(root, path.relative_to(root))
                        count = path.stat().st_size
                        path.unlink()
                        size += count
                    safe_path(root, "versions/" + directory.name).rmdir()
                    removed += 1
                except (OSError, ValueError, KeyError, TypeError):
                    failed = True
        archives = safe_path(root, "archives")
        if archives.is_dir():
            for directory in archives.iterdir():
                if directory.name == keep or not _SHA.fullmatch(directory.name):
                    continue
                _current(root, manifest, budget)
                try:
                    owned = safe_path(root, "archives/" + directory.name)
                    if not owned.is_dir():
                        continue
                    for entry in owned.iterdir():
                        _current(root, manifest, budget)
                        if not (entry.name.startswith(bundle_spec(kind).filename_prefix) and entry.suffix == ".zip"):
                            continue
                        archive = safe_path(root, entry.relative_to(root))
                        if not archive.is_file() or sha256_file(archive, budget) != directory.name:
                            continue
                        _current(root, manifest, budget)
                        count = archive.stat().st_size
                        archive.unlink()
                        size += count
                    if not any(owned.iterdir()):
                        owned.rmdir()
                except (OSError, ValueError):
                    failed = True
    except InterruptedError:
        failed = True
    except (OSError, ValueError):
        failed = True
    if failed:
        report_cleanup_warning(tell, _WARNING)
    if size and tell:
        try:
            tell("\u5df2\u6e05\u7406\u65e7\u79bb\u7ebf\u8d44\u6e90\uff0c\u91ca\u653e %.1f MB\u3002" % (size / 1024 / 1024), "info")
        except Exception:
            pass
    return dict(versions=removed, bytes=size, incomplete=failed)
