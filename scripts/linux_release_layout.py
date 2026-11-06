"""Linux release inventory and ZIP assembly without user or Windows runtime data."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import uuid
import zipfile
import zlib
from modules.app_update_services.catalog import BASELINE, MAX_CATALOG_BYTES
from modules.app_update_services.linux_catalog import managed_linux_path, validate_entries, MAX_FILES
from modules.app_update_services.manifest import canonical, version_key
from modules.app_update_services.locations import linked
from modules.platform_services.local_rename import rename_without_replace
from modules.platform_services.terminal_library import validate_linux_library
from modules.release_identity import PACKAGE_DIRECTORY
from modules.platform_services.linux_recovery_launcher import LAUNCHER, SCRIPT, verify_launcher

REQUIRED_RUNTIME_FILES = frozenset((
    'LuXiaoGuan', 'resources/base_library.zip',
    'bin/router_terminal/libroutermanager_terminal.so',
    'themes/dark.qss', 'themes/light.qss', 'images/icons/app-router.png',
    'bin/tools/scripts/hcx_autocap.sh', 'bin/tools/openwrt_base/base.zip'))


BLOCKED = {'.git', 'build', 'dist', 'tests', 'infra', '__pycache__',
           'RouterManagerData', '.wrangler', 'config', '.routermanager-update', 'hc_offline_pack'}


def _directory(path):
    path = Path(path).absolute()
    if not path.is_dir() or any(linked(p) for p in (path, *path.parents)):
        raise ValueError('Release directories must be real, unlinked directories')
    return path


def _identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_mode


def _permissions(path, info):
    return stat.S_IMODE(info.st_mode)


def _file_entry(path, relative):
    before = path.lstat(); mode = _permissions(path, before)
    if not stat.S_ISREG(before.st_mode) or mode not in {0o644, 0o755}:
        raise ValueError('Unsupported release file type or permissions: ' + relative)
    digest, compressed, compressor = hashlib.sha256(), hashlib.sha256(), zlib.compressobj(6)
    size = blob_size = 0
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd, 'rb') as source:
        if _identity(os.fstat(source.fileno())) != _identity(before):
            raise ValueError('Release file changed before reading')
        for block in iter(lambda: source.read(1024 * 1024), b''):
            size += len(block)
            if size > 2 * 1024**3: raise ValueError('Release member exceeds its size limit')
            digest.update(block)
            data = compressor.compress(block); compressed.update(data); blob_size += len(data)
        data = compressor.flush(); compressed.update(data); blob_size += len(data)
        if (_identity(os.fstat(source.fileno())) != _identity(before)
                or _identity(path.lstat()) != _identity(before)):
            raise ValueError('Release file changed while reading')
    return dict(path=relative, kind='file', mode=mode, sha256=digest.hexdigest(), size=size,
                blob_sha256=compressed.hexdigest(), blob_size=blob_size)


def inventory(package):
    package = _directory(package)
    entries, directories, total, total_bytes = [], [package], 0, 0
    while directories:
        folder = directories.pop()
        with os.scandir(folder) as scan:
            members = []
            for item in scan:
                if len(members) >= MAX_FILES: raise ValueError('Release directory exceeds its member limit')
                members.append(item)
        for item in sorted(members, key=lambda p: p.name):
            path = Path(item.path); relative = path.relative_to(package).as_posix()
            if any(p in BLOCKED or p.startswith('.env') for p in path.relative_to(package).parts):
                raise ValueError('Private or build directory in release: ' + relative)
            info = path.lstat()
            if stat.S_ISDIR(info.st_mode) and not linked(path):
                managed_linux_path(relative + '/.directory-check'); directories.append(path); total += 1
                if total > MAX_FILES: raise ValueError('Release has too many directories')
                continue
            if relative == LAUNCHER:
                verify_launcher(path,mode=_permissions(path,info));continue
            if relative == BASELINE:
                if linked(path) or not stat.S_ISREG(info.st_mode) or info.st_size > MAX_CATALOG_BYTES:
                    raise ValueError('Invalid release baseline')
                continue
            managed_linux_path(relative)
            if path.suffix.lower() in {'.key', '.enc', '.log', '.pfx', '.p12'} or (
                    path.suffix.lower() == '.pem' and relative != 'resources/certifi/cacert.pem'):
                raise ValueError('Private or runtime file in release: ' + relative)
            if stat.S_ISLNK(info.st_mode):
                entry = dict(path=relative, kind='symlink', target=os.readlink(path))
            else:
                total_bytes += info.st_size
                if info.st_size > 2 * 1024**3 or total_bytes > 4 * 1024**3:
                    raise ValueError('Release byte inventory exceeds its limit')
                entry = _file_entry(path, relative)
            entries.append(entry)
            if len(entries) > MAX_FILES: raise ValueError('Release has too many files')
    entries.sort(key=lambda row: row['path']); validate_entries(entries)
    return entries


def write_baseline(package, version):
    version_key(version)
    value = dict(schema=2, product='RouterManager', platform='linux-x64', version=version,
                 resource_revision=0, files=inventory(package), retired=[], migrations=[], history=[])
    data = canonical(value)
    if len(data) > MAX_CATALOG_BYTES: raise ValueError('Release baseline exceeds its limit')
    path = Path(package) / BASELINE
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as output: output.write(data)
    path.chmod(0o644)
    return path


def verify_files(package, version, *, entries=None):
    package = _directory(package)
    launcher=package/LAUNCHER
    verify_launcher(launcher,mode=_permissions(launcher,launcher.lstat()))
    entries = inventory(package) if entries is None else entries
    baseline = package / BASELINE
    with baseline.open('rb') as source: raw = source.read(MAX_CATALOG_BYTES + 1)
    if len(raw) > MAX_CATALOG_BYTES: raise ValueError('Release baseline exceeds its limit')
    value = json.loads(raw)
    if (value.get('schema') != 2 or value.get('product') != 'RouterManager'
            or value.get('platform') != 'linux-x64' or value.get('version') != version
            or value.get('files') != entries):
        raise ValueError('Release baseline differs from package contents or version')
    required = REQUIRED_RUNTIME_FILES
    mapping = {row['path']: row for row in entries}
    if any(name not in mapping or mapping[name]['kind'] != 'file' for name in required):
        raise ValueError('A required Linux release resource is missing')
    if not any(name.startswith('resources/libpython3.10.so') and row['kind'] == 'file'
               for name, row in mapping.items()):
        raise ValueError('The private Python shared runtime is missing')
    with (package / 'LuXiaoGuan').open('rb') as source: header = source.read(20)
    if (len(header) != 20 or header[:7] != b'\x7fELF\x02\x01\x01'
            or int.from_bytes(header[16:18], 'little') not in {2, 3}
            or int.from_bytes(header[18:20], 'little') != 62):
        raise ValueError('The Linux release requires a native x86-64 executable')
    validate_linux_library(package / 'bin/router_terminal/libroutermanager_terminal.so')
    return dict(platform='linux-x64', version=version, files=len(entries), private_data_excluded=True)


def make_zip(package, target, version):
    package = _directory(package); target = Path(target).absolute(); _directory(target.parent)
    if target.exists() or linked(target): raise FileExistsError('Refusing to overwrite a release')
    before = inventory(package); verify_files(package, version, entries=before)
    temporary = target.with_name(target.name + '.' + uuid.uuid4().hex + '.part')
    try:
        with zipfile.ZipFile(temporary, 'x', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            rows = before + [dict(path=BASELINE, kind='file', mode=0o644),dict(path=LAUNCHER,kind='file',mode=0o755)]
            for row in rows:
                path = package / row['path']
                info = zipfile.ZipInfo(PACKAGE_DIRECTORY + '/' + row['path'])
                info.create_system = 3; info.compress_type = zipfile.ZIP_DEFLATED
                mode = stat.S_IFLNK | 0o777 if row['kind'] == 'symlink' else stat.S_IFREG | row['mode']
                info.external_attr = mode << 16
                if row['kind'] == 'symlink': archive.writestr(info, row['target'].encode('utf-8'))
                else:
                    with path.open('rb') as source, archive.open(info, 'w', force_zip64=True) as output:
                        shutil.copyfileobj(source, output, 1024 * 1024)
        after = inventory(package)
        if after != before: raise ValueError('Package changed while archiving')
        verify_files(package, version, entries=after)
        verify_zip(temporary, before, (package / BASELINE).read_bytes())
        rename_without_replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return dict(zip_crc_verified=True, files=len(before) + 2, size=target.stat().st_size)


def verify_zip(archive_path, entries, baseline):
    expected = {row['path']: row for row in entries}
    expected[BASELINE] = dict(kind='baseline')
    expected[LAUNCHER] = dict(kind='launcher',mode=0o755)
    seen = set()
    with zipfile.ZipFile(archive_path) as archive:
        for info in archive.infolist():
            prefix = PACKAGE_DIRECTORY + '/'
            relative = info.filename.removeprefix(prefix)
            if relative == info.filename or relative not in expected or relative in seen:
                raise ValueError('Unexpected or duplicate Linux archive member')
            seen.add(relative); row = expected[relative]
            mode = stat.S_IFLNK | 0o777 if row['kind'] == 'symlink' else stat.S_IFREG | row.get('mode', 0o644)
            if info.create_system != 3 or info.external_attr >> 16 != mode:
                raise ValueError('Linux archive permissions differ from its baseline')
            if row['kind'] in {'symlink', 'baseline','launcher'}:
                data = row['target'].encode('utf-8') if row['kind'] == 'symlink' else (SCRIPT if row['kind']=='launcher' else baseline)
                if info.file_size != len(data) or archive.read(info) != data:
                    raise ValueError('Linux archive metadata differs from its baseline')
            else:
                digest = hashlib.sha256()
                if info.file_size != row['size']: raise ValueError('Linux archive member size mismatch')
                with archive.open(info) as source:
                    for block in iter(lambda:source.read(1024 * 1024), b''): digest.update(block)
                if digest.hexdigest() != row['sha256']:
                    raise ValueError('Linux archive bytes differ from its baseline')
        if seen != set(expected): raise ValueError('Linux archive is incomplete')


def verify_builtin_base(path):
    """Require the pinned immutable dependency ZIP, not a user cache or stub."""
    from modules.capture_services.openwrt.builtin_base_identity import SIZE, SHA256
    path = Path(path)
    try:
        info = path.lstat()
    except OSError as exc:
        raise ValueError('Required built-in dependency ZIP is missing') from exc
    if not stat.S_ISREG(info.st_mode) or info.st_size != SIZE or any(linked(p) for p in (path, *path.parents)):
        raise ValueError('Invalid built-in dependency ZIP source')
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(descriptor, 'rb') as incoming:
        data = incoming.read(SIZE + 1)
    if len(data) != SIZE or hashlib.sha256(data).hexdigest() != SHA256:
        raise ValueError('Built-in dependency ZIP fingerprint mismatch')


def seed_assets(root, package):
    root = _directory(root); package = _directory(package)
    raw = subprocess.check_output(['git', '-C', str(root), 'ls-files', '-z', '--',
                                   'images', 'themes', 'bin/openwrt_compatible', 'bin/hcx_compat', 'tools'], timeout=15)
    names = [name.decode('utf-8') for name in raw.split(b'\0') if name]
    names = [name for name in names if 'hc_offline_pack' not in Path(name).parts]
    required_tools={'tools/scripts/hcx_autocap.sh','tools/openwrt_base/base.zip'}
    if not required_tools.issubset(names):raise ValueError('Required bundled capture tools must be version-controlled')
    verify_builtin_base(root/'tools/openwrt_base/base.zip')
    names += ['bin/router_terminal/libroutermanager_terminal.so',
              'bin/router_terminal/libvterm-LICENSE.txt', 'bin/router_terminal/kodoterm-LICENSE.txt']
    hcx_assets=['bin/hcx_compat/manifest.json', *(f'bin/hcx_compat/{arch}/hcxdumptool' for arch in ('x86_64','aarch64','arm','mips','mipsel')), *(f'bin/hcx_compat/{name}' for name in ('hcxdumptool-LICENSE.txt','Zig-LICENSE.txt','musl-COPYRIGHT.txt'))]
    names += [name for name in hcx_assets if name not in names]
    validate_linux_library(root / 'bin/router_terminal/libroutermanager_terminal.so')
    for source_relative in names:
        relative='bin/'+source_relative if source_relative.startswith('tools/') else source_relative
        managed_linux_path(relative); source = root / source_relative
        if any(linked(p) for p in (source, *source.parents)) or not source.is_file():
            raise ValueError('Linked or missing source asset: ' + relative)
        destination = package / relative; destination.parent.mkdir(parents=True, exist_ok=True)
        if any(linked(p) for p in (destination, *destination.parents)):
            raise ValueError('Linked asset destination')
        before = source.lstat()
        with source.open('rb') as incoming, destination.open('xb') as output:
            if _identity(os.fstat(incoming.fileno())) != _identity(before):
                raise ValueError('Source asset changed before copying')
            header = incoming.read(4); output.write(header); shutil.copyfileobj(incoming, output, 1024 * 1024)
            if (_identity(os.fstat(incoming.fileno())) != _identity(before)
                    or _identity(source.lstat()) != _identity(before)):
                raise ValueError('Source asset changed while copying')
        destination.chmod(0o755 if header == b'\x7fELF' else 0o644)
    with (package/LAUNCHER).open('xb') as output:output.write(SCRIPT)
    (package/LAUNCHER).chmod(0o755)
    from modules.app_update_services.channel import GATEWAY
    from modules.app_update_services.manifest import canonical
    from modules.app_update_services.release_targets import manifest_endpoint
    channel=package/'resources/app-update-channel.json';channel.parent.mkdir(parents=True,exist_ok=True)
    with channel.open('xb') as output:
        output.write(canonical(dict(schema=1,enabled=True,manifest_url=GATEWAY+manifest_endpoint('linux-x64'),network_mode='auto',github_mirror=True)))
    channel.chmod(0o644)
    verify_builtin_base(package/'bin/tools/openwrt_base/base.zip')
    return len(names)+2
