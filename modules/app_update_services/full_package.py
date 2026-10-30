"""Verified full-package staging, shared by downloader and independent installer."""
import hashlib
from pathlib import Path,PurePosixPath
import shutil,stat,zipfile
from .catalog import managed_path,BASELINE
from .planner import sha256_file,baseline
from modules.release_identity import archive_root,APPLICATION_EXECUTABLES

MAX_EXPANDED=4*1024**3


def prepare_package(archive,release,stage,root,*,extract=True,cancel=lambda:False):
    archive=Path(archive);stage=Path(stage)
    from .locations import linked
    if linked(archive) or archive.stat().st_size!=release.size or sha256_file(archive,cancel)!=release.sha256:
        raise ValueError('Full package fingerprint mismatch')
    files=[];seen=set();total=0
    with zipfile.ZipFile(archive) as zipped:
        infos=zipped.infolist()
        if not 1<=len(infos)<=12000:raise ValueError('Invalid full-package entry count')
        prefix=archive_root(zipped.namelist())
        for info in infos:
            if cancel():raise InterruptedError('Update cancelled')
            name=info.filename
            if '\\' in name or name.startswith('/') or any(p in ('','..','.') for p in name.rstrip('/').split('/')):
                raise ValueError('Unsafe ZIP entry')
            if not name.startswith(prefix) or name.casefold() in seen:raise ValueError('Duplicate or foreign ZIP entry')
            seen.add(name.casefold())
            if stat.S_ISLNK(info.external_attr>>16) or info.flag_bits&1:raise ValueError('Linked or encrypted ZIP entry')
            total+=info.file_size
            if total>MAX_EXPANDED:raise ValueError('Expanded package exceeds limit')
        if extract and shutil.disk_usage(stage).free<total*2+128*1024**2:
            raise ValueError('Not enough space for extraction and rollback')
        for index,info in enumerate(infos):
            if info.is_dir():continue
            relative=info.filename[len(prefix):]
            if relative.startswith('config/') or relative==BASELINE:continue
            relative=managed_path(relative)
            temporary=stage/'extracting.part'
            if linked(temporary):raise ValueError('Linked extraction output')
            size=0;digest=hashlib.sha256()
            out=temporary.open('wb') if extract else None
            try:
                with zipped.open(info) as source:
                    while True:
                        if cancel():raise InterruptedError('Update cancelled')
                        block=source.read(1024*1024)
                        if not block:break
                        size+=len(block)
                        if size>info.file_size:raise ValueError('ZIP entry exceeds size')
                        digest.update(block)
                        if out is not None:out.write(block)
                if size!=info.file_size:raise ValueError('Truncated ZIP entry')
            finally:
                if out is not None:out.close()
            checksum=digest.hexdigest()
            if extract:
                target=stage/(checksum+'.file')
                if linked(target):raise ValueError('Linked staged file')
                temporary.replace(target)
            files.append(dict(path=relative,size=size,sha256=checksum,blob_sha256=checksum,blob_size=size))
    if not any(row['path'] in APPLICATION_EXECUTABLES for row in files):raise ValueError('Full package lacks application')
    if sha256_file(archive,cancel)!=release.sha256:raise ValueError('Full package changed during extraction')
    active={row['path'] for row in files}
    retired=[path for path in baseline(root) if path not in active]
    return dict(schema=1,version=release.version,resource_revision=0,files=files,retired=retired,migrations=[])
