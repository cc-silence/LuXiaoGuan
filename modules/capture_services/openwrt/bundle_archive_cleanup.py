"""Reclaim owned ZIP duplicates only after the prepared resource is usable."""
import time
from cryptography.exceptions import InvalidSignature
from .common_bundle_repository import safe_path, validate_catalog
from .common_bundle_manifest import verify_bundle_manifest
from .offline_cleanup import report_cleanup_warning
from .bundle_archive_paths import archive_candidates


def discard_prepared_archive(root, payload, tell=None):
    """Caller has verified and activated every object in this signed revision."""
    try:
        for archive in archive_candidates(root, payload):
            archive.unlink(missing_ok=True)
            safe_path(archive.parent, archive.name + ".part").unlink(missing_ok=True)
            safe_path(archive.parent, "resume.json").unlink(missing_ok=True)
            if archive.parent.is_dir() and not any(archive.parent.iterdir()):
                archive.parent.rmdir()
        return True
    except (OSError, ValueError):
        report_cleanup_warning(tell, "\u79bb\u7ebf\u8d44\u6e90\u5df2\u5c31\u7eea\uff0c\u4f46\u91cd\u590d ZIP \u6e05\u7406\u5931\u8d25\uff0c\u8bf7\u68c0\u67e5\u6587\u4ef6\u5360\u7528\u6216\u6743\u9650\u3002")
        return False


def cleanup_repository_archive(repository, cancel, tell=None):
    """One-time migration for the current ZIP left by previous clients."""
    class IntegrityBudget:
        deadline = time.monotonic() + 30

        def is_set(self):
            return cancel.is_set() or time.monotonic() >= self.deadline

    budget = IntegrityBudget()
    try:
        receipt = safe_path(repository.root, "current.json")
        if receipt.stat().st_size > 65536:
            raise ValueError("Resource receipt exceeds limit")
        options = {"public_key": repository.public_key} if repository.public_key else {}
        payload = verify_bundle_manifest(receipt.read_bytes(), kind=repository.kind, **options)
        from .bundle_layout import version_path
        expected = version_path(repository.root, payload["sha256"], repository.kind, payload=payload)
        if repository.content_root != expected:
            raise ValueError("Resource changed during installation")
        if not any(path.is_file() for path in archive_candidates(repository.root, payload)):
            return
        # An installed subset does not prove the complete offline resource is usable.
        # Only legacy ZIPs trigger this streaming integrity pass, once per revision.
        for row in validate_catalog(repository.catalog, repository.kind).values():
            if budget.is_set():
                return
            repository.object_path(row, budget)
        if not budget.is_set():
            discard_prepared_archive(repository.root, payload, tell)
    except InterruptedError:
        return
    except (OSError, ValueError, InvalidSignature):
        report_cleanup_warning(tell, "\u5b89\u88c5\u5df2\u5b8c\u6210\uff1b\u672a\u80fd\u786e\u8ba4\u5b8c\u6574\u79bb\u7ebf\u8d44\u6e90\uff0c\u4fdd\u7559 ZIP \u4f9b\u4fee\u590d\u3002")
