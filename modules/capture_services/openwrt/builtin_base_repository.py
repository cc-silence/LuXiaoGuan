"""Immutable two-package fallback, separate from downloaded bundle storage."""
import hashlib
import json
from pathlib import Path
import re
import sys
import zipfile
from .common_bundle_manifest import sha256_file
from .common_bundle_repository import CommonBundleRepository
from .builtin_base_identity import SIZE, SHA256

ROOTS = frozenset({'libuci-lua', 'wpa-cli'})

def resource_path():
    from modules.config_manager import BASE_DIR
    prefix = Path(BASE_DIR) / 'bin' if getattr(sys, 'frozen', False) else Path(BASE_DIR)
    return prefix / 'tools/openwrt_base/base.zip'

class BuiltinBaseRepository:
    ipk_selection = CommonBundleRepository.ipk_selection

    def __init__(self, path=None):
        self.path = Path(path) if path is not None else resource_path()

    def profile(self, device, cancel=None):
        if getattr(device, 'distribution', '') != 'OpenWrt' or not self.path.is_file():
            return None
        if self.path.is_symlink() or self.path.stat().st_size != SIZE or sha256_file(self.path, cancel) != SHA256:
            raise ValueError('Built-in dependency checksum mismatch')
        with zipfile.ZipFile(self.path) as archive:
            if archive.getinfo('catalog.json').file_size > 4 * 1024 * 1024:
                raise ValueError('Built-in catalog exceeds limit')
            data = json.loads(archive.read('catalog.json'))
        if data.get('schema_version') != 1 or set(data.get('roots', [])) != ROOTS:
            raise ValueError('Invalid built-in catalog')
        profiles = data.get('profiles', [])
        if not isinstance(profiles, list) or len(profiles) > 5000:
            raise ValueError('Invalid built-in profiles')
        key = tuple(getattr(device, field, '') for field in
                    ('version', 'target', 'package_architecture', 'package_manager'))
        return next((p for p in profiles if tuple(p[k] for k in
                    ('release', 'target', 'architecture', 'package_manager')) == key), None)

    def materialize(self, row, directory, cancel=None):
        if cancel is not None and cancel.is_set():
            raise InterruptedError('Built-in installation cancelled')
        if (row['name'] not in ROOTS or not re.fullmatch(r'[a-f0-9]{64}', row['sha256'])
                or row['blob'] != 'objects/' + row['sha256']
                or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.+~\-]*\.(ipk|apk)', row['filename'])):
            raise ValueError('Invalid built-in package')
        with zipfile.ZipFile(self.path) as archive:
            info = archive.getinfo(row['blob'])
            if not 0 < info.file_size == row['size'] <= 1024 * 1024:
                raise ValueError('Invalid built-in package size')
            raw = archive.read(info)
        if hashlib.sha256(raw).hexdigest() != row['sha256']:
            raise ValueError('Built-in package checksum mismatch')
        path = Path(directory) / row['filename']
        path.write_bytes(raw)
        return path
