"""Keep one verified local build and discard reproducible staging copies."""
import argparse
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import zipfile

ROOT = Path(__file__).resolve().parents[1]
STAGE = re.compile(r'[0-9]+(?:\.[0-9]+)*(?:_[a-z0-9]+)?_([0-9]{8}_[0-9]{6})')
OFFLINE_PARTS = ('bundle', 'metadata', 'http-cache', 'completed_profiles')


def locked_build(function):
    """Serialize build staging and manual cleanup on this host."""
    @wraps(function)
    def wrapped(*args,**kwargs):
        path=ROOT/'build/local-build.lock';path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('a+b') as handle:
            if handle.tell()==0:handle.write(b'0');handle.flush()
            handle.seek(0)
            try:
                if os.name=='nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
                else:
                    import fcntl
                    fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError:
                raise ValueError('Another local build or cleanup is running') from None
            try:return function(*args,**kwargs)
            finally:
                handle.seek(0)
                if os.name=='nt':msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
                else:fcntl.flock(handle,fcntl.LOCK_UN)
    return wrapped


def digest(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda:source.read(1024*1024),b''):
            value.update(block)
    return value.hexdigest()


def guarded(root,path):
    root=Path(root).resolve(strict=True)
    path=Path(path).absolute()
    resolved=path.resolve(strict=True)
    build=root/'build'
    if resolved == build or not resolved.is_relative_to(build) or path != resolved:
        raise ValueError('Cleanup path escaped build or contains a link: '+str(path))
    for parent in (path,*path.parents):
        if parent == root:
            break
        info=parent.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:
            raise ValueError('Cleanup does not follow reparse points: '+str(parent))
    for current,dirs,files in os.walk(path,followlinks=False) if path.is_dir() else ():
        for name in dirs+files:
            item=Path(current)/name
            info=item.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:
                raise ValueError('Linked cleanup descendant: '+str(item))
    return resolved


def bytes_used(path):
    if path.is_file():return path.stat().st_size
    return sum((Path(current)/name).stat().st_size
               for current,dirs,files in os.walk(path,followlinks=False) for name in files)


def verified_zip(path,sha256):
    path=Path(path)
    if not path.is_file() or not re.fullmatch(r'[0-9a-f]{64}',str(sha256)) or digest(path)!=sha256:
        raise ValueError('Retained delivery ZIP is missing or differs from its build report: '+str(path))
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:raise ValueError('Retained delivery ZIP has a CRC error')
    return path


def release_candidates(root,keep_stage=None):
    root=Path(root).resolve();base=root/'build/releases';stages=[]
    if not base.exists():return [],None
    for path in base.iterdir():
        if not path.is_dir() or not STAGE.fullmatch(path.name):continue
        guarded(root,path)
        report=path/'report.json'
        if not report.is_file():continue
        data=json.loads(report.read_text(encoding='utf-8'))
        stages.append((path,data))
    complete=[(p,d) for p,d in stages if d.get('status')=='complete' and d.get('zip_crc_verified') is True]
    if not complete:
        failed=[p for p,d in stages if d.get('status')=='failed']
        keep=max(failed,key=lambda p:STAGE.fullmatch(p.name)[1]) if failed else None
        return [p for p in failed if p!=keep],keep
    if keep_stage is None:
        keep,data=max(complete,key=lambda row:STAGE.fullmatch(row[0].name)[1])
    else:
        keep=Path(keep_stage).resolve()
        data=next((d for p,d in complete if p==keep),None)
        if data is None:raise ValueError('Requested retained build is not complete')
    archive=Path(data['archive']).resolve()
    if not archive.is_relative_to(root/'dist'):raise ValueError('Retained archive is outside delivery')
    verified_zip(archive,data['sha256'])
    candidates=[p for p,d in stages if p!=keep and d.get('status') in ('complete','failed')]
    return candidates,keep


def publishing_candidates(root,keep_folder=None):
    root=Path(root).resolve();base=root/'build/publishing/application'
    if not base.exists():return [],None
    folders=[p for p in base.iterdir() if p.is_dir() and re.fullmatch(r'[0-9a-f]{64}',p.name)]
    if not folders:return [],None
    if keep_folder is not None:
        keep=Path(keep_folder).resolve()
        if keep.parent!=base or keep not in folders:raise ValueError('Unexpected publication cache')
    else:
        published=[]
        for p in folders:
            record=p/'publication.json'
            if record.is_file():
                data=json.loads(record.read_text(encoding='utf-8'))
                if data.get('status')=='published-and-client-verified':published.append(p)
        if not published:return [],None
        keep=max(published,key=lambda p:(p/'publication.json').stat().st_mtime_ns)
    return [p/'blobs' for p in folders if p!=keep and (p/'blobs').is_dir()],keep


def offline_candidates(root,folder,delivery=None):
    root=Path(root).resolve();folder=Path(folder).resolve()
    guarded(root,folder)
    report=folder/'report.json'
    if not report.is_file():return []
    data=json.loads(report.read_text(encoding='utf-8'))
    if data.get('complete') is not True:return []
    sha=data.get('zip_sha256')
    if delivery is None:
        matches=[p for p in (root/'dist').glob('openwrt_*/*.zip')
                 if p.stat().st_size==data.get('zip_bytes') and digest(p)==sha]
        if not matches:return []
        delivery=matches[0]
    delivery=Path(delivery).resolve()
    if not delivery.is_relative_to(root/'dist'):raise ValueError('Offline delivery is outside dist')
    verified_zip(delivery,sha)
    result=[folder/name for name in OFFLINE_PARTS if (folder/name).exists()]
    # Remove a staged ZIP only when the delivered copy has exactly the same bytes.
    for staged in folder.glob('*.zip'):
        candidates=[p for p in (root/'dist').glob('openwrt_*/*.zip') if p.stat().st_size==staged.stat().st_size]
        if candidates:
            staged_hash=digest(staged)
            duplicate=next((p for p in candidates if digest(p)==staged_hash),None)
            if duplicate is not None:
                verified_zip(duplicate,staged_hash)
                result.append(staged)
    return result


def allowed_target(root,path):
    parts=Path(path).resolve().relative_to(Path(root).resolve()/'build').parts
    if len(parts)==2 and parts[0]=='releases' and STAGE.fullmatch(parts[1]):return True
    if (len(parts)==4 and parts[:2]==('publishing','application')
            and re.fullmatch(r'[0-9a-f]{64}',parts[2]) and parts[3]=='blobs'):return True
    if (len(parts)==2 and parts[0].startswith('openwrt-')
            and (parts[1] in OFFLINE_PARTS or parts[1].endswith('.zip'))):return True
    return parts==('compatibility-audit','superseded')


def remove_candidates(root,paths,apply=False):
    removed=[];skipped=[];total=0
    for candidate in paths:
        if not allowed_target(root,candidate):raise ValueError('Cleanup target is outside the generated-data allowlist')
        path=guarded(root,candidate)
        size=bytes_used(path)
        if apply:
            # Validate again immediately before each recursive removal.
            path=guarded(root,path)
            try:
                if path.is_dir():shutil.rmtree(path)
                else:path.unlink()
            except OSError as exc:
                skipped.append({'path':str(path),'error_type':type(exc).__name__})
                continue
        removed.append({'path':str(path),'bytes':size})
        total+=size
    return {'applied':apply,'bytes':total,'paths':removed,'skipped':skipped}


def cleanup_releases(root=ROOT,keep_stage=None,apply=True):
    candidates,keep=release_candidates(root,keep_stage)
    result=remove_candidates(root,candidates,apply)
    result['retained']=str(keep) if keep else None
    return result


def cleanup_failed_build(root,stage):
    root=Path(root).resolve();stage=guarded(root,stage)
    # Preserve a single small failure record while releasing failed staging data.
    folder=root/'build/last-build-failure';folder.mkdir(exist_ok=True)
    guarded(root,folder)
    for name in ('report.json','pyinstaller.log','ssh-smoke.log','gui-smoke.log','updater-smoke.log','gpo-smoke.log'):
        source=stage/name;target=folder/name
        if source.is_file():
            if target.exists():guarded(root,target)
            shutil.copy2(source,target)
    return cleanup_releases(root)


def cleanup_publishing(root=ROOT,keep_folder=None,apply=True):
    candidates,keep=publishing_candidates(root,keep_folder)
    result=remove_candidates(root,candidates,apply)
    result['retained']=str(keep) if keep else None
    return result


def cleanup_offline(root,folder,delivery):
    root=Path(root).resolve();folder=Path(folder).resolve()
    if not folder.is_relative_to(root/'build'):return {'applied':False,'reason':'external_build_directory'}
    return remove_candidates(root,offline_candidates(root,folder,delivery),True)


@locked_build
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    candidates,keep=release_candidates(ROOT)
    publication,published=publishing_candidates(ROOT)
    candidates+=publication
    for folder in sorted((ROOT/'build').glob('openwrt-*')):
        if folder.is_dir():candidates+=offline_candidates(ROOT,folder)
    obsolete=ROOT/'build/compatibility-audit/superseded'
    if obsolete.is_dir():candidates.append(obsolete)
    result=remove_candidates(ROOT,candidates,args.apply)
    result.update(retained_build=str(keep),retained_publication_cache=str(published))
    target=ROOT/'build/local-cleanup.json'
    target.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result),flush=True)


if __name__=='__main__':main()
