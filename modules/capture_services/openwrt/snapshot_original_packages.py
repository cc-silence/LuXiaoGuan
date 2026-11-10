"""Exact authenticated originals for consumed OpenWrt package payloads.

No package installation or source configuration changes occur during backup.
"""
from contextlib import ExitStack
from types import SimpleNamespace
from pathlib import Path
import hashlib
import io
import tarfile
from .common_bundle_repository import CommonBundleRepository
from .bundle_storage_lock import bundle_lock
from .dependency_snapshot_archive import MAX_PACKAGE, digest_file, tar_members
from .installed_dependency_catalog import file_path
from .command_builder import quote_argument as q


class SnapshotOriginalPackages:
    def __init__(self, task, inventory, packages, remote, local):
        self.task, self.inventory, self.packages = task, inventory, packages
        self.remote, self.local = remote, Path(local)
        self.repositories = None
        self.stack = ExitStack()
        self.online = None
        self.provenance = {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return self.stack.__exit__(*args)

    def _device(self, pkg):
        release = self.inventory['identity']['release']
        return SimpleNamespace(distribution=release.get('distribution', ''),
            version=release.get('version', ''), target=release.get('target', ''),
            package_architecture=pkg.metadata.get('A', pkg.metadata.get('Architecture', '')),
            package_manager=self.inventory['manager'])

    def original(self, pkg):
        self.task.guard.check()
        device = self._device(pkg)
        # All/noarch packages still belong to the device's feed architecture.
        if device.package_architecture in ('all', 'noarch'):
            arch = {p.metadata.get('A', p.metadata.get('Architecture', ''))
                    for p in self.inventory['packages'].values()}
            arch -= {'', 'all', 'noarch'}
            if len(arch) != 1:
                raise ValueError('Cannot determine original package feed architecture')
            device.package_architecture = arch.pop()
        if self.repositories is None:
            self.repositories = {}
        for kind in ('common', 'capture'):
            if kind not in self.repositories:
                repo = CommonBundleRepository(kind=kind)
                self.stack.enter_context(bundle_lock(repo.root, shared=True))
                self.repositories[kind] = repo
            repo = self.repositories[kind]
            profile = repo.profile(device)
            row = repo.package_row(profile, pkg.name, pkg.version) if profile else None
            if row is not None:
                directory = self.local / ('original-' + pkg.name)
                directory.mkdir(exist_ok=True)
                path = repo.materialize(row, directory, self.task.guard.cancel)
                if path.stat().st_size > MAX_PACKAGE:
                    raise ValueError('Original package exceeds size limit')
                self.provenance[pkg.name] = dict(source='local-' + repo.kind,
                    version=pkg.version, sha256=digest_file(path, self.task.guard.check))
                return path
        return self._online(pkg, device)

    def _online(self, pkg, device):
        from .firmware_profile import release_kind
        from .signal_package_feed import (download_session, fetch_bytes, normalize_profile,
                                         fetch_index)
        from .apk_package_feed import (normalize_apk_profile, merge_index,
                                       fetch_sha256sums)
        if device.distribution != 'OpenWrt' or release_kind(device.version) != 'stable':
            raise ValueError('Exact original unavailable for this firmware: ' + pkg.name + ' ' + pkg.version)
        if self.online is None:
            self.stack.enter_context(download_session())
            self.online = {}
        key = (device.version, device.package_architecture, device.package_manager)
        if key not in self.online:
            if device.package_manager == 'opkg':
                profile = normalize_profile(device.version, device.package_architecture, 'opkg')
                index = fetch_index(profile, cancel_event=self.task.guard.cancel, require_complete=True)
                self.online[key] = (index, None)
            else:
                profile = normalize_apk_profile(device.version, device.package_architecture, 'apk')
                index = merge_index(fetch_bytes, profile, self.task.guard.cancel, require_complete=True)
                hashes = fetch_sha256sums(fetch_bytes, profile, self.task.guard.cancel)
                self.online[key] = (index, hashes)
        index, hashes = self.online[key]
        row = index.get(pkg.name)
        if row is None or row.version != pkg.version:
            raise ValueError('Exact original unavailable: ' + pkg.name + ' ' + pkg.version)
        expected = row.sha256 if hashes is None else hashes.get(row.filename, '')
        if len(expected) != 64:
            raise ValueError('Original package has no verified checksum: ' + pkg.name)
        url = row.url if hashes is None else row.base_url + row.filename
        self.task.guard.check()
        raw = fetch_bytes(url, timeout=60, cancel_event=self.task.guard.cancel, limit=MAX_PACKAGE)
        self.task.guard.check()
        if not raw or hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError('Original package checksum mismatch: ' + pkg.name)
        if hashes is None and len(raw) != row.size:
            raise ValueError('Original package size mismatch: ' + pkg.name)
        path = self.local / ('original-' + row.filename)
        path.write_bytes(raw)
        self.provenance[pkg.name] = dict(source=url, version=pkg.version, sha256=expected)
        return path

    def recover(self, missing):
        missing = {file_path(path) for path in missing}
        # Only documented once-only initialization files may be consumed normally.
        if not missing or any(not path.startswith('etc/uci-defaults/') for path in missing):
            raise ValueError('Missing ordinary package payload: ' + ', '.join(sorted(missing)))
        owners = {}
        for path in missing:
            matches = [pkg for pkg in self.packages if path in pkg.files]
            if len(matches) != 1:
                raise ValueError('Ambiguous original file owner: ' + path)
            owners.setdefault(matches[0].name, (matches[0], set()))[1].add(path)
        result = self.local / 'original-defaults.tgz'
        with tarfile.open(result, 'w:gz') as output:
            for pkg, paths in owners.values():
                self.task.guard.check()
                self.task.progress(dict(stage='\u6b63\u5728\u4ece\u539f\u5305\u8865\u9f50\u521d\u59cb\u5316\u811a\u672c\uff1a' + pkg.name))
                try:
                    original = self.original(pkg)
                    if self.inventory['manager'] == 'opkg':
                        self._ipk(original, pkg, paths, output)
                    else:
                        self._apk(original, pkg, paths, output)
                except (InterruptedError, TimeoutError):
                    raise
                except Exception as exc:
                    raise ValueError('\u8865\u9f50\u521d\u59cb\u5316\u811a\u672c\u5931\u8d25\uff1a' + pkg.name + ' ' + pkg.version
                                     + '\n' + str(exc) + '\n\u672a\u751f\u6210\u5b8c\u6574\u5907\u4efd\u3002') from exc
        return result

    def _ipk(self, original, pkg, paths, output):
        with tarfile.open(original, 'r:gz') as outer:
            entries = tar_members(outer)
            if not {'control.tar.gz', 'data.tar.gz'} <= entries.keys():
                raise ValueError('Invalid original IPK')
            with outer.extractfile(entries['control.tar.gz']) as stream, tarfile.open(fileobj=stream, mode='r:gz') as control:
                records = tar_members(control)
                item = records.get('control')
                if item is None or not item.isfile() or item.size > 1024*1024:
                    raise ValueError('Invalid original IPK control')
                text = control.extractfile(item).read().decode('utf-8')
                fields = dict(line.split(':', 1) for line in text.splitlines() if ':' in line and not line[:1].isspace())
                if (fields.get('Package', '').strip(), fields.get('Version', '').strip(), fields.get('Architecture', '').strip()) != (pkg.name, pkg.version, pkg.metadata.get('Architecture', '')):
                    raise ValueError('Original IPK identity mismatch')
            with outer.extractfile(entries['data.tar.gz']) as stream, tarfile.open(fileobj=stream, mode='r:gz') as data:
                self._copy(data, paths, output)

    def _apk(self, original, pkg, paths, output):
        # The router's native reader supports both official v3 and older v2 APKs.
        remote = self.remote + '/original-' + pkg.name + '.apk'
        destination = self.remote + '/original-' + pkg.name
        size = int(pkg.metadata.get('I', '0'))
        if not 0 < size <= 512*1024*1024:
            raise ValueError('Original APK installed size unavailable')
        self.task._check_space('/tmp', size + original.stat().st_size + 2*1024*1024)
        self.task.transfer.upload(original, remote)
        self.task._verify_remote(remote, original.stat().st_size, digest_file(original, self.task.guard.check))
        # OpenWrt signs its repository index, not every individual APK. The
        # exact catalog original and the uploaded bytes were hash-checked above.
        # Trust relaxation applies only to this private extraction command; it
        # never installs packages, runs hooks, or changes persistent APK policy.
        self.task.reader.run('original_extract', 'mkdir ' + q(destination)
            + ' && apk extract --allow-untrusted --no-network --destination '
            + q(destination) + ' ' + q(remote), 120)
        # Never execute originals or install them while backing up.
        listing = self.local / (pkg.name + '.defaults.paths')
        listing.write_text(''.join(path + '\n' for path in sorted(paths)), encoding='utf-8', newline='\n')
        remote_list = self.remote + '/' + pkg.name + '.defaults.paths'
        self.task.transfer.upload(listing, remote_list)
        # Keep the exec request short even for packages with many consumed files.
        command = ('cd ' + q(destination) + ' && while IFS= read -r p; do '
            'd="$p"; while :; do test ! -L "$d" || exit 51; '
            'case "$d" in */*) d=${d%/*};; *) break;; esac; done; '
            'test -f "$p" && test "$(wc -c < "$p")" -le 1048576 || exit 52; '
            'done < ' + q(remote_list) + ' && tar -czf - -T ' + q(remote_list))
        blob = self.task.transfer.read(command, limit=8*1024*1024)
        with tarfile.open(fileobj=io.BytesIO(blob), mode='r:gz') as data:
            self._copy(data, paths, output)
        self.task.reader.run('original_cleanup', 'rm -rf ' + q(destination) + ' && rm -f ' + q(remote) + ' ' + q(remote_list))

    def _copy(self, data, paths, output):
        entries = tar_members(data)
        for path in sorted(paths):
            self.task.guard.check()
            item = entries.get(path)
            if item is None or not item.isfile() or item.size > 1024*1024:
                raise ValueError('Original initialization file missing or unsafe: ' + path)
            with data.extractfile(item) as stream:
                output.addfile(item, stream)
