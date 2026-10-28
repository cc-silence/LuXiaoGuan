"""Short ZIP cache locations; full signed receipts retain immutable identity."""
from datetime import datetime
import json
import re
from .common_bundle_repository import safe_path
from .bundle_metadata import write_metadata
from .common_bundle_manifest import verify_bundle_manifest, sha256_file

_LABEL = re.compile(r"[0-9]{8}-[0-9]{2,4}")


def archive_directories(root):
    base = safe_path(root, "archives")
    if not base.is_dir():
        return
    for index, directory in enumerate(base.iterdir(), 1):
        if index > 4096:
            raise ValueError("Too many offline archive directories")
        if _LABEL.fullmatch(directory.name) or re.fullmatch("[a-f0-9]{64}", directory.name):
            yield safe_path(root, "archives/" + directory.name)


def archive_receipt(directory):
    receipt = safe_path(directory, "resume.json")
    if not receipt.is_file() or receipt.stat().st_size > 65536:
        return None
    return receipt.read_bytes()


def archive_candidates(root, payload):
    """Receipts locate files only; consumers still verify signed byte identities."""
    paths = []
    for directory in archive_directories(root):
        if directory.name == payload["sha256"]:
            paths.append(safe_path(directory, payload["filename"]))
            continue
        if not _LABEL.fullmatch(directory.name):
            continue
        raw = archive_receipt(directory)
        try:
            previous = json.loads(raw.decode("utf-8"))["payload"] if raw else {}
            if all(previous.get(key) == payload[key] for key in ("sha256", "size", "filename")):
                paths.append(safe_path(directory, "bundle.zip"))
        except (ValueError, TypeError, KeyError, AttributeError):
            continue
    return paths


def reserve_archive(root, payload, manifest, cancel):
    """Caller holds the storage lease. Interrupted moves resume without copying."""
    if cancel.is_set():
        raise InterruptedError("Download cancelled")
    paths = archive_candidates(root, payload)
    legacy = next((path for path in paths if path.parent.name == payload["sha256"]), None)
    short = [path for path in paths if path != legacy]
    if short:
        target = short[0]
    else:
        base = safe_path(root, "archives")
        base.mkdir(parents=True, exist_ok=True)
        date = datetime.now().strftime("%Y%m%d")
        for sequence in range(1, 10000):
            directory = safe_path(root, "archives/" + date + "-%02d" % sequence)
            try:
                directory.mkdir()
                break
            except FileExistsError:
                continue
        else:
            raise ValueError("Local archive date sequence exhausted")
        target = safe_path(directory, "bundle.zip")
    if archive_receipt(target.parent) != manifest:
        write_metadata(safe_path(target.parent, "resume.json"), manifest)
    moves = ((legacy, target), (safe_path(legacy.parent, legacy.name + ".part"), safe_path(target.parent, "bundle.zip.part"))) if legacy is not None else ()
    for source, destination in moves:
        if cancel.is_set():
            raise InterruptedError("Download cancelled")
        if source.is_file() and not destination.exists():
            source.rename(destination)
    legacy_part = safe_path(legacy.parent, legacy.name + ".part") if legacy is not None else None
    if legacy is not None and not legacy.exists() and not legacy_part.exists() and legacy.parent.is_dir():
        if archive_receipt(legacy.parent) == manifest:
            safe_path(legacy.parent, "resume.json").unlink()
        if not any(legacy.parent.iterdir()):
            legacy.parent.rmdir()
    return target


def retire_short_archive(root, directory, keep, kind, budget, check_current, public_key=None):
    """Authenticate all cleanup targets before touching a retired short cache."""
    if not _LABEL.fullmatch(directory.name):
        return None
    raw = archive_receipt(directory)
    if raw is None:
        return 0
    payload = verify_bundle_manifest(raw, kind=kind, **({"public_key": public_key} if public_key else {}))
    if payload["sha256"] == keep:
        return 0
    if {entry.name for entry in directory.iterdir()} - {"bundle.zip", "bundle.zip.part", "resume.json"}:
        return 0
    files = []
    for name in ("bundle.zip", "bundle.zip.part", "resume.json"):
        budget.check()
        path = safe_path(directory, name)
        if not path.exists():
            continue
        if not path.is_file():
            return 0
        if name == "bundle.zip" and (path.stat().st_size != payload["size"] or sha256_file(path, budget) != payload["sha256"]):
            return 0
        files.append(path)
    removed = 0
    for path in files:
        budget.check()
        check_current()
        path = safe_path(root, path.relative_to(root))
        removed += path.stat().st_size
        path.unlink()
    directory.rmdir()
    return removed
