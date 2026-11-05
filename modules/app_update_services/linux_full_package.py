"""Derive a Unix installation inventory exclusively from a verified complete ZIP."""
import hashlib
import json
import stat
import zipfile
from .catalog import BASELINE, MAX_CATALOG_BYTES
from .linux_catalog import managed_linux_path, validate_entries
from .linux_install_plan import catalog_files, content_state
from modules.release_identity import archive_root
from modules.platform_services.linux_recovery_launcher import LAUNCHER, SCRIPT, verify_launcher_bytes


def fingerprint(source, release, check):
    source.seek(0)
    digest=hashlib.sha256();size=0
    for data in iter(lambda:source.read(65536),b''):
        check();size+=len(data)
        if size>release.size:raise ValueError('Linux ZIP exceeds signed size')
        digest.update(data)
    if size!=release.size or digest.hexdigest()!=release.sha256:
        raise ValueError('Linux ZIP fingerprint mismatch')
    source.seek(0)


def inventory(source, release, *, store=None, cancel=lambda:False, free_bytes=None):
    """Keep Unix permissions and internal links; never extract to archive paths."""
    if release.platform!='linux-x64':raise ValueError('A Linux signed package is required')
    def check():
        if cancel():raise InterruptedError('Linux full-package preparation cancelled')
    fingerprint(source,release,check)
    entries=[];seen=set();total=0;launcher=False;embedded=None
    with zipfile.ZipFile(source) as zipped:
        infos=zipped.infolist();prefix=archive_root(zipped.namelist())
        if not 1<=len(infos)<=24000:raise ValueError('Invalid Linux ZIP member count')
        for item in infos:
            check();name=item.filename
            if (name in seen or '\\' in name or name.startswith('/') or '\x00' in name
                    or not name.startswith(prefix) or item.flag_bits&1 or item.create_system!=3
                    or item.compress_type not in {zipfile.ZIP_STORED,zipfile.ZIP_DEFLATED}
                    or any(p in {'','.','..'} for p in name.rstrip('/').split('/'))):
                raise ValueError('Unsafe or duplicate Linux ZIP member')
            seen.add(name);total+=item.file_size
            if item.file_size>2*1024**3 or total>4*1024**3:raise ValueError('Linux ZIP expansion exceeds limit')
        if free_bytes is not None and free_bytes<total*2+128*1024**2:
            raise ValueError('Not enough space for Linux extraction and rollback')
        for item in infos:
            check();relative=item.filename[len(prefix):];raw_mode=item.external_attr>>16
            if item.is_dir():
                if not stat.S_ISDIR(raw_mode):raise ValueError('Invalid Unix ZIP directory')
                if relative.rstrip('/'):managed_linux_path(relative.rstrip('/')+'/.directory-check')
                continue
            if relative==LAUNCHER:
                if not stat.S_ISREG(raw_mode) or item.file_size!=len(SCRIPT):raise ValueError('Invalid stable launcher')
                verify_launcher_bytes(zipped.read(item),stat.S_IMODE(raw_mode));launcher=True;continue
            if relative==BASELINE:
                if embedded is not None or not stat.S_ISREG(raw_mode) or item.file_size>MAX_CATALOG_BYTES:
                    raise ValueError('Invalid bundled Linux baseline')
                embedded=json.loads(zipped.read(item));continue
            path=managed_linux_path(relative)
            if stat.S_ISLNK(raw_mode):
                if item.file_size>512:raise ValueError('Excessive Linux ZIP link')
                entries.append(dict(path=path,kind='symlink',target=zipped.read(item).decode('utf-8')))
                continue
            if not stat.S_ISREG(raw_mode) or stat.S_IMODE(raw_mode) not in {0o644,0o755}:
                raise ValueError('Unsafe Unix ZIP file type or permissions')
            digest=hashlib.sha256();count=0;header=bytearray()
            with zipped.open(item) as incoming:
                for data in iter(lambda:incoming.read(65536),b''):
                    check();count+=len(data)
                    if count>item.file_size:raise ValueError('Linux ZIP member grew')
                    digest.update(data)
                    if len(header)<20:header.extend(data[:20-len(header)])
            if count!=item.file_size:raise ValueError('Truncated Linux ZIP member')
            if path=='LuXiaoGuan' and (header[:7]!=b'\x7fELF\x02\x01\x01' or int.from_bytes(header[18:20],'little')!=62):
                raise ValueError('Foreign Linux application executable')
            sha=digest.hexdigest()
            row=dict(path=path,kind='file',mode=stat.S_IMODE(raw_mode),sha256=sha,size=count,blob_sha256=sha,blob_size=count)
            entries.append(row)
            if store is not None:
                state=dict(kind='file',mode=0o600,sha256=sha,size=count);name=sha+'.file'
                actual=store.state(name,check)
                if actual is not None and actual!=state:raise ValueError('Conflicting Linux package cache')
                if actual is None:
                    with zipped.open(item) as incoming:store.create(name,state,incoming,check)
    incoming=validate_entries(entries)
    if not launcher or embedded is None:raise ValueError('Linux package lacks launcher or baseline')
    official=catalog_files(embedded)
    if (embedded['version']!=release.version or set(official)!=set(incoming)
            or any(content_state(official[p])!=content_state(e) for p,e in incoming.items())):
        raise ValueError('Linux ZIP differs from its bundled baseline')
    fingerprint(source,release,check)
    result=dict(schema=2,product='RouterManager',platform='linux-x64',version=release.version,
                resource_revision=release.resource_revision,files=sorted(entries,key=lambda e:e['path']),
                retired=embedded.get('retired',[]),migrations=[],delivery_mode='full-package')
    catalog_files(result)
    return result
