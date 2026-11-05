"""Linux update inventory: signed contents, Unix permissions and internal file links.

Desktop installation requires a native-tested packaged updater admission record.
"""
import hashlib
import json
from pathlib import PurePosixPath
from .catalog import MAX_CATALOG_BYTES, ROOTS, BASELINE, digest, validate_history

EXECUTABLE = "LuXiaoGuan"
MAX_FILES = 12000


def managed_linux_path(value):
    if (not isinstance(value, str) or not value or len(value) > 240
            or "\\" in value or any(ord(c) < 32 for c in value)):
        raise ValueError("Invalid Linux managed path")
    parts = value.split("/")
    if len(parts) > 32 or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("Unsafe Linux managed path")
    if (value == BASELINE or "__pycache__" in parts or value.endswith((".pyc", ".tmp"))
            or value.lower().endswith((".exe", ".dll"))):
        raise ValueError("Reserved or foreign-platform Linux update file")
    if value != EXECUTABLE and (len(parts) < 2 or parts[0] not in ROOTS):
        raise ValueError("Protected Linux user path")
    return value


def link_destination(path, target):
    if (not isinstance(target, str) or not target or len(target) > 512
            or target.startswith("/") or "\\" in target or any(ord(c) < 32 for c in target)):
        raise ValueError("Invalid Linux file link")
    stack = list(PurePosixPath(path).parent.parts)
    for part in target.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            if not stack: raise ValueError("Escaping Linux file link")
            stack.pop()
        else:
            stack.append(part)
    return managed_linux_path("/".join(stack))


def validate_entries(entries):
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_FILES:
        raise ValueError("Invalid Linux file inventory")
    seen, total = {}, 0
    for entry in entries:
        if not isinstance(entry, dict): raise ValueError("Invalid Linux file entry")
        path = managed_linux_path(entry.get("path"))
        if path in seen: raise ValueError("Duplicate Linux update path")
        kind = entry.get("kind")
        if kind == "file":
            if set(entry) != {"path", "kind", "mode", "sha256", "size", "blob_sha256", "blob_size"}:
                raise ValueError("Invalid Linux file fields")
            if type(entry["mode"]) is not int or entry["mode"] not in {0o644, 0o755}:
                raise ValueError("Unsafe Linux file permissions")
            digest(entry["sha256"]); digest(entry["blob_sha256"])
            for name in ("size", "blob_size"):
                if type(entry[name]) is not int or not 0 <= entry[name] <= 2 * 1024**3:
                    raise ValueError("Invalid Linux file size")
            total += entry["size"]
        elif kind == "symlink":
            if set(entry) != {"path", "kind", "target"} or path == EXECUTABLE:
                raise ValueError("Invalid Linux link fields")
            link_destination(path, entry["target"])
        else:
            raise ValueError("Unsupported Linux file kind")
        seen[path] = entry
    if total > 4 * 1024**3: raise ValueError("Linux installation exceeds size limit")
    primary = seen.get(EXECUTABLE, {})
    if primary.get("kind") != "file" or primary.get("mode") != 0o755 or primary.get("size", 0) == 0:
        raise ValueError("Linux native executable is missing or not executable")
    directories = set()
    for path in seen:
        parent = PurePosixPath(path).parent
        while str(parent) != ".":
            directories.add(str(parent))
            if len(directories) > MAX_FILES: raise ValueError("Linux directory inventory exceeds limit")
            if str(parent) in seen:
                raise ValueError("A Linux file or link cannot be a parent directory")
            parent = parent.parent
    for path, entry in seen.items():
        if entry["kind"] != "symlink": continue
        visited, destination = {path}, link_destination(path, entry["target"])
        for _ in range(32):
            if destination in visited or destination not in seen:
                raise ValueError("Cyclic or missing Linux link destination")
            visited.add(destination); linked = seen[destination]
            if linked["kind"] == "file": break
            destination = link_destination(destination, linked["target"])
        else: raise ValueError("Linux file link chain exceeds limit")
    return seen


def parse_linux_catalog(data, release):
    if (release.platform != "linux-x64" or len(data) > MAX_CATALOG_BYTES
            or len(data) != release.catalog_size
            or hashlib.sha256(data).hexdigest() != release.catalog_sha256):
        raise ValueError("Linux catalog target or fingerprint mismatch")
    catalog = json.loads(data)
    if (not isinstance(catalog, dict) or catalog.get("schema") != 2
            or catalog.get("product") != "RouterManager" or catalog.get("platform") != "linux-x64"
            or catalog.get("version") != release.version
            or catalog.get("resource_revision", 0) != release.resource_revision):
        raise ValueError("Invalid Linux catalog identity")
    active = validate_entries(catalog.get("files"))
    retired = catalog.get("retired", [])
    if not isinstance(retired, list) or len(retired) > MAX_FILES:
        raise ValueError("Invalid Linux retired inventory")
    for path in retired:
        if managed_linux_path(path) in active:
            raise ValueError("An active Linux path is marked retired")
    if catalog.get("migrations", []) != []:
        raise ValueError("Linux configuration migration requires a native implementation")
    validate_history(catalog, release)
    return catalog
