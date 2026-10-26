"""Local signed resource identity and comparison, without network or package scans."""
from pathlib import Path
from .bundle_storage_lock import locked_bundle
from datetime import datetime
from .common_bundle_repository import CommonBundleRepository, bundle_root, safe_path
from .common_bundle_manifest import verify_bundle_manifest


def revision_label(payload):
    version = payload.get('revision') or str(payload.get('published_at', '')).replace('T', ' ')[:19]
    return str(version)[:48] + ' / ' + payload.get('sha256', '')[:12]


@locked_bundle
def local_bundle_status(cancel, progress=None, stage=None, *, kind='common', root=None, public_key=None):
    if cancel.is_set():
        raise InterruptedError('Local resource check cancelled')
    root = bundle_root(kind) if root is None else Path(root)
    receipt = safe_path(root, 'current.json')
    if not receipt.is_file():
        return dict(installed=False, reason='')
    try:
        options = {'public_key': public_key} if public_key else {}
        if receipt.stat().st_size > 65536:
            raise ValueError('Local bundle manifest is too large')
        payload = verify_bundle_manifest(receipt.read_bytes(), kind=kind, **options)
        repository = CommonBundleRepository(root, public_key, kind)
        repository._load()
        if cancel.is_set():
            raise InterruptedError('Local resource check cancelled')
        return dict(installed=True, path=str(repository.content_root.resolve()), revision=payload['sha256'],
                    published_at=payload.get('published_at', ''), label=revision_label(payload))
    except (ValueError, OSError, UnicodeError) as exc:
        return dict(installed=False, reason=str(exc))


def compare_bundle(local, remote):
    if not local.get('installed'):
        return 'missing'
    if local['revision'] == remote['sha256']:
        return 'current'
    try:
        if datetime.fromisoformat(local['published_at']) >= datetime.fromisoformat(remote['published_at']):
            return 'local_newer'
    except (ValueError, TypeError, KeyError):
        pass
    return 'update'
