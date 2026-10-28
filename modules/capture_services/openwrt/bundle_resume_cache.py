"""Retain only the selected signed revision's managed partial archive."""
import re
from .common_bundle_repository import safe_path
from .common_bundle_manifest import verify_bundle_manifest
from .bundle_archive_paths import archive_directories, reserve_archive, _LABEL


def select_resume_revision(root, payload, manifest, cancel, kind):
    """Delete only partials named by an authenticated ownership receipt.

    Complete archives, unknown files and links are never cleanup targets here.
    The caller holds the bundle storage lease and has approved the new revision.
    """
    archives = safe_path(root, "archives")
    if archives.is_dir():
        for directory in archive_directories(root):
            if cancel.is_set():
                raise InterruptedError("Download cancelled")
            if not (re.fullmatch("[a-f0-9]{64}", directory.name) or _LABEL.fullmatch(directory.name)):
                continue
            try:
                owned = safe_path(root, "archives/" + directory.name)
                receipt = safe_path(owned, "resume.json")
                if not receipt.is_file() or receipt.stat().st_size > 65536:
                    continue
                previous = verify_bundle_manifest(receipt.read_bytes(), kind=kind)
                if previous["sha256"] == payload["sha256"]:
                    continue
                compact = bool(_LABEL.fullmatch(directory.name))
                if not compact and previous["sha256"] != directory.name:
                    continue
                part = safe_path(owned, "bundle.zip.part" if compact else previous["filename"] + ".part")
                if part.exists():
                    if not part.is_file():
                        continue
                    part.unlink()
                if not any(path.suffix == ".zip" for path in owned.iterdir()):
                    receipt.unlink()
                if not any(owned.iterdir()):
                    owned.rmdir()
            except (OSError, ValueError, TypeError, KeyError, AttributeError):
                # Unowned or externally changed files are not disposable cache.
                continue
    return reserve_archive(root, payload, manifest, cancel)
