"""Linux-only durable file transaction used by the signed isolated worker.

The worker must verify the signed release and stop the parent application
before calling apply. Recovery never overwrites a divergent concurrent edit.
"""
from contextlib import contextmanager
import hashlib
import io
import math
from pathlib import Path
import re
import time
import uuid
from .catalog import BASELINE
from .linux_catalog import managed_linux_path, link_destination, MAX_FILES
from .linux_install_plan import plan, names
from .manifest import canonical
from .locations import validate_stage, cache_lock, linked, CACHE_DIRECTORY
from modules.platform_services.runtime import is_linux
from modules.platform_services.linux_update_files import RootedFiles

JOURNAL='linux-transaction.json'
STATES={'preparing','applying','validating','complete','rolling_back','rolled_back'}


class RecoveryConflict(ValueError):
    """Keep both old and new evidence when automatic recovery cannot be safe."""


def _deadline(cancel, timeout):
    if type(timeout) not in {int,float} or not math.isfinite(timeout) or not 0<timeout<=900:
        raise ValueError('Invalid Linux transaction deadline')
    end=time.monotonic()+timeout
    def check():
        if cancel():raise InterruptedError('Linux installation cancelled')
        if time.monotonic()>=end:raise TimeoutError('Linux installation timed out')
    check();return check


@contextmanager
def _session(root, stage):
    if not is_linux():raise OSError('Native Linux transactions require Linux')
    root=Path(root).absolute();stage=validate_stage(stage,root)
    if stage.parent.name!=CACHE_DIRECTORY:raise ValueError('Linux updates require the RouterManager-owned cache')
    if not stage.is_dir() or any(linked(p) for p in (root,stage,*stage.parents)):
        raise ValueError('Linked or missing Linux update stage')
    with cache_lock(stage.parent) as acquired:
        if not acquired:raise BlockingIOError('Another update owns the install lease')
        with RootedFiles(root) as files,RootedFiles(stage) as staged,RootedFiles(stage.parent) as cache:
            yield files,staged,cache,stage


def _active(files, stage):
    return dict(schema=1,platform='linux-x64',stage=stage.name,root=files.identity)


def _check_active(cache, expected, required=False):
    value=cache.read_json('active.json',65536) if cache.state('active.json') is not None else None
    if value is not None and value!=expected:raise RecoveryConflict('Another or unknown update owns active.json')
    if required and value is None:raise RecoveryConflict('The active Linux transaction record is missing')


def _clear_active(cache, expected):
    _check_active(cache,expected)
    if cache.state('active.json') is not None:cache.unlink('active.json')


def _state(value, path):
    if value is None:return
    if not isinstance(value,dict):raise ValueError('Invalid journal content state')
    if value.get('kind')=='file':
        if (set(value)!={'kind','sha256','size','mode'} or type(value['size']) is not int
                or not 0<=value['size']<=2*1024**3 or type(value['mode']) is not int
                or value['mode'] not in {0o600,0o644,0o755}
                or not isinstance(value['sha256'],str) or not re.fullmatch('[a-f0-9]{64}',value['sha256'])):
            raise ValueError('Invalid journal file state')
    elif value.get('kind')=='symlink' and set(value)=={'kind','target'} and path!=BASELINE:
        link_destination(path,value['target'])
    else:raise ValueError('Invalid journal member kind')


def _load(staged, files, stage):
    value=staged.read_json(JOURNAL)
    if (not isinstance(value,dict) or value.get('schema')!=1 or value.get('platform')!='linux-x64'
            or value.get('root')!=files.identity or value.get('stage')!=stage.name
            or value.get('state') not in STATES or not isinstance(value.get('token'),str)
            or not re.fullmatch('[a-f0-9]{24}',value['token']) or not isinstance(value.get('entries'),list)
            or not 1<=len(value['entries'])<=2*MAX_FILES+1):
        raise ValueError('Invalid Linux transaction journal')
    seen=set()
    for entry in value['entries']:
        if not isinstance(entry,dict) or set(entry)!={'path','before','after'}:raise ValueError('Invalid journal operation')
        path=entry['path']
        if path!=BASELINE:managed_linux_path(path)
        if path in seen:raise ValueError('Duplicate journal target')
        seen.add(path);_state(entry['before'],path);_state(entry['after'],path)
    baseline=value['entries'][-1]
    if (baseline['path']!=BASELINE or baseline['after'] is None or baseline['after'].get('kind')!='file'
            or baseline['after'].get('sha256')!=stage.name or baseline['after'].get('mode')!=0o644
            or baseline['after'].get('size',0)>8*1024*1024):
        raise ValueError('Journal does not end with its bound Linux baseline')
    return value


def _cleanup(files, journal, check):
    for index,entry in enumerate(journal['entries']):
        check();new,old=names(entry['path'],journal['token'],index)
        for path,expected in [(new,entry['after']),(old,entry['before'])]:
            actual=files.state(path,check)
            if actual is None:continue
            if expected is None or actual!=expected:raise RecoveryConflict('Changed transaction sidecar: '+path)
            files.unlink(path)


def _rollback(files,staged,cache,stage,journal,check):
    journal['state']='rolling_back';staged.write_json(JOURNAL,journal)
    for index in reversed(range(len(journal['entries']))):
        check();entry=journal['entries'][index];path=entry['path'];before=entry['before'];after=entry['after']
        new,old=names(path,journal['token'],index)
        current=files.state(path,check);parked=files.state(old,check)
        if parked is not None and (before is None or parked!=before):
            raise RecoveryConflict('Changed parked original: '+path)
        if current==before:
            pass
        elif current==after and after is not None:
            if before is not None and parked is None:raise RecoveryConflict('Original backup is missing: '+path)
            scratch=files.state(new,check)
            if scratch is not None:
                if scratch!=after:raise RecoveryConflict('Changed replacement sidecar: '+path)
                files.unlink(new)
            files.rename(path,new)
            if files.state(new,check)!=after:raise RecoveryConflict('Replacement changed during recovery: '+path)
            if before is not None:files.rename(old,path)
        elif current is None:
            if before is not None:
                if parked is None:raise RecoveryConflict('Original content cannot be recovered: '+path)
                files.rename(old,path)
        else:raise RecoveryConflict('Concurrent edit preserved during recovery: '+path)
        if files.state(path,check)!=before:raise RecoveryConflict('Restored content differs from original: '+path)
    _cleanup(files,journal,check)
    journal['state']='rolled_back';staged.write_json(JOURNAL,journal)
    _clear_active(cache,_active(files,stage))
    return dict(status='rolled_back',safe_to_restart=True)


def apply(root,stage,catalog,validate=lambda:None,*,cancel=lambda:False,timeout=300,progress=lambda *_:None):
    check=_deadline(cancel,timeout)
    with _session(root,stage) as (files,staged,cache,stage):
        if hashlib.sha256(canonical(catalog)).hexdigest()!=stage.name:raise ValueError('Catalog differs from update stage identity')
        expected=_active(files,stage);_check_active(cache,expected)
        if staged.state(JOURNAL) is not None:
            previous=_load(staged,files,stage)
            if previous['state']!='rolled_back':raise ValueError('Recover the previous transaction before installing')
        prepared=plan(files,catalog,check)
        for entry in prepared['operations']:
            if entry['after'] is None or entry['after']['kind']=='symlink' or entry['path']==BASELINE:continue
            source=entry['after']['sha256']+'.file'
            actual=staged.state(source,check)
            if (actual is None or actual['kind']!='file' or actual['sha256']!=entry['after']['sha256']
                    or actual['size']!=entry['after']['size']):raise ValueError('Staged content is missing or damaged: '+entry['path'])
            if entry['path']=='LuXiaoGuan':
                with staged.open_read(source) as stream:header=stream.read(20)
                if (len(header)!=20 or header[:7]!=b'\x7fELF\x02\x01\x01'
                        or int.from_bytes(header[16:18],'little') not in {2,3}
                        or int.from_bytes(header[18:20],'little')!=62):raise ValueError('Staged application is not a Linux x86-64 executable')
        journal=dict(schema=1,platform='linux-x64',root=files.identity,stage=stage.name,
                     token=uuid.uuid4().hex[:24],state='preparing',entries=prepared['operations'])
        staged.write_json(JOURNAL,journal);cache.write_json('active.json',expected)
        try:
            progress('preparing',0,len(journal['entries']))
            for index,entry in enumerate(journal['entries']):
                check();after=entry['after'];new,old=names(entry['path'],journal['token'],index)
                if files.state(new,check) is not None or files.state(old,check) is not None:
                    raise RecoveryConflict('Transaction sidecar already exists')
                if after is not None:
                    if after['kind']=='symlink':files.create(new,after,check=check)
                    elif entry['path']==BASELINE:files.create(new,after,io.BytesIO(prepared['baseline']),check)
                    else:
                        with staged.open_read(after['sha256']+'.file') as stream:files.create(new,after,stream,check)
                    if files.state(new,check)!=after:raise ValueError('Prepared replacement differs from inventory')
                progress('preparing',index+1,len(journal['entries']))
            journal['state']='applying';staged.write_json(JOURNAL,journal)
            for index,entry in enumerate(journal['entries']):
                check();path=entry['path'];new,old=names(path,journal['token'],index)
                if files.state(path,check)!=entry['before']:raise RecoveryConflict('Target changed after planning: '+path)
                if entry['before'] is not None:
                    files.flush(path);files.rename(path,old)
                    if files.state(old,check)!=entry['before']:raise RecoveryConflict('Original changed during parking: '+path)
                if entry['after'] is not None:files.rename(new,path)
                if files.state(path,check)!=entry['after']:raise RecoveryConflict('Installed target differs from inventory')
                progress('installing',index+1,len(journal['entries']))
            check();journal['state']='validating';staged.write_json(JOURNAL,journal);validate();check()
            for entry in journal['entries']:
                check()
                if files.state(entry['path'],check)!=entry['after']:
                    raise RecoveryConflict('Target changed during startup validation: '+entry['path'])
            journal['state']='complete';staged.write_json(JOURNAL,journal)
        except (Exception,KeyboardInterrupt):
            _rollback(files,staged,cache,stage,journal,_deadline(lambda:False,300))
            raise
        cleanup_pending=False
        try:
            _cleanup(files,journal,_deadline(lambda:False,120));_clear_active(cache,expected)
        except (OSError,ValueError,TimeoutError):cleanup_pending=True
        return dict(status='complete',safe_to_restart=True,preserved=prepared['preserved'],cleanup_pending=cleanup_pending)


def recover(root,stage,*,timeout=300):
    check=_deadline(lambda:False,timeout)
    with _session(root,stage) as (files,staged,cache,stage):
        expected=_active(files,stage);_check_active(cache,expected)
        journal=_load(staged,files,stage)
        if journal['state'] in {'complete','rolled_back'}:
            _cleanup(files,journal,check);_clear_active(cache,expected)
            return dict(status=journal['state'],safe_to_restart=True)
        return _rollback(files,staged,cache,stage,journal,check)
