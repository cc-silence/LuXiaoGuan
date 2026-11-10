"""One-time Windows settings migration, with verified backups and no recursive deletion."""
import os
import json
import hashlib
from pathlib import Path
from uuid import uuid4
from .config_store import _read_ini, _read_json

MARKER = '.portable-settings-v1'
FILES = ('settings.ini', 'download.ini', 'proxy_config.json', 'log_export.json',
         'capture/openwrt_settings.json', 'capture/openwrt_state.json')
MAX_FILE = 2 * 1024 * 1024


def _check(path, root):
    path, root = Path(path).absolute(), Path(root).absolute()
    if not path.is_relative_to(root):
        raise ValueError('Configuration path escapes root')
    for entry in (path, *path.parents):
        if entry == root.parent:
            break
        if entry.is_symlink() or (entry.exists() and getattr(entry.lstat(), 'st_file_attributes', 0) & 0x400):
            raise ValueError('Linked configuration path')


def _snapshot(path):
    if not path.is_file():
        return None, None, -1
    with path.open('rb') as stream:
        raw = stream.read(MAX_FILE + 1)
    if len(raw) > MAX_FILE:
        raise ValueError('Configuration exceeds migration limits')
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError as error:
        raise ValueError('Configuration encoding is invalid') from error
    value, stamp = (_read_ini(text) if path.suffix.lower() == '.ini' else _read_json(text))
    return raw, value, stamp


def _matches(path, raw):
    with path.open('rb') as stream:
        return stream.read(len(raw) + 1) == raw


def _backup(path, raw):
    if path.is_symlink() or (path.exists() and getattr(path.lstat(), 'st_file_attributes', 0) & 0x400):
        raise ValueError('Linked configuration backup')
    if path.exists():
        if not path.is_file() or not _matches(path, raw):
            raise OSError('Configuration backup verification failed')
        return
    with path.open('xb') as output:
        output.write(raw)
        output.flush()
        os.fsync(output.fileno())
    if not _matches(path, raw):
        raise OSError('Configuration backup verification failed')


def migrate(old_root, app_root):
    old_root, app_root = Path(old_root).absolute(), Path(app_root).absolute()
    marker = app_root / MARKER
    _check(marker, app_root)
    if marker.is_file():
        return 0
    app_root.mkdir(parents=True, exist_ok=True)
    if old_root == app_root or (old_root.exists() and app_root.exists() and os.path.samefile(old_root, app_root)):
        marker.write_text('1\n', encoding='ascii')
        return 0
    migrated = 0
    for relative in FILES:
        source, target = old_root / relative, app_root / relative
        _check(source, old_root); _check(target, app_root)
        if not source.is_file():
            continue
        if source.stat().st_size > MAX_FILE or (target.exists() and (not target.is_file() or target.stat().st_size > MAX_FILE)):
            raise ValueError('Configuration exceeds migration limits')
        old_bytes, old_value, old_time = _snapshot(source)
        local_bytes, local_value, local_time = _snapshot(target)
        if old_value is None or (target.exists() and local_value is None):
            raise ValueError('Configuration cannot be validated for migration')
        key = hashlib.sha256(relative.encode('utf-8') + b'\0' + old_bytes + b'\0'
                             + (local_bytes if local_bytes is not None else b'<absent>')).hexdigest()
        archive = app_root / 'portable-migration-backup' / key
        _check(archive, app_root)
        archive.mkdir(parents=True, exist_ok=True)
        _backup(archive / 'old', old_bytes)
        if local_bytes is not None:
            _backup(archive / 'local', local_bytes)
        _backup(archive / 'manifest.json', json.dumps({'file': relative}, ensure_ascii=True).encode('ascii'))
        if not _matches(source, old_bytes) or (target.exists() if local_bytes is None else not target.is_file() or not _matches(target, local_bytes)):
            raise ValueError('Configuration changed during migration')
        if local_value is None or old_time > local_time:
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + '.portable-' + uuid4().hex)
            try:
                with temporary.open('xb') as output:
                    output.write(old_bytes)
                    output.flush()
                    os.fsync(output.fileno())
                if not _matches(temporary, old_bytes):
                    raise OSError('Configuration copy verification failed')
                if local_bytes is None and target.exists():
                    raise ValueError('Configuration appeared during migration')
                os.replace(temporary, target)
                if not _matches(target, old_bytes):
                    raise OSError('Configuration destination verification failed')
            finally:
                temporary.unlink(missing_ok=True)
        if not _matches(source, old_bytes):
            raise ValueError('Configuration changed before cleanup')
        source.unlink()
        migrated += 1
    marker.write_text('1\n', encoding='ascii')
    return migrated


def prepare():
    import sys
    from modules.platform_services import application_paths
    if application_paths.is_linux() or not getattr(sys, 'frozen', False) or os.environ.get('ROUTERMANAGER_USER_CONFIG_DIR'):
        return 0
    try:
        return migrate(application_paths.user_config_dir(), Path(sys.executable).parent / 'config')
    except (OSError, ValueError, TypeError):
        import logging
        logging.getLogger(__name__).warning('Portable settings migration was not completed; original data retained')
        return 0
