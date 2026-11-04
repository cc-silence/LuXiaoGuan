"""Linux update jobs bind signed input, root identity and a stable local runtime."""
import copy
import hashlib
import io
import json
import math
import time
import os
from pathlib import Path
import re
import sys
import zlib
from .catalog import parse_catalog, BASELINE, MAX_CATALOG_BYTES
from .manifest import canonical, verify_manifest, MAX_MANIFEST_BYTES
from .trust import PUBLIC_KEY
from .locations import validate_stage, cache_lock, CACHE_DIRECTORY
from .linux_install_plan import catalog_files, content_state, byte_state, plan as make_install_plan
from modules.platform_services.runtime import is_linux
from modules.platform_services.linux_update_files import RootedFiles
from modules.platform_services.linux_update_process import capture_parent, validate_ticket
from modules.platform_services.linux_update_snapshot import prepare_runtime_snapshot, verify_runtime_snapshot
from modules.platform_services.linux_recovery_launcher import PENDING, verify_launcher, LAUNCHER

JOB='linux-job.json'


def stage_location(path):
    if not is_linux():raise OSError('Linux update jobs require Linux')
    path=Path(path).absolute()
    if path.name!=JOB:raise ValueError('Unexpected Linux update job filename')
    stage=validate_stage(path.parent)
    if stage.parent.name!=CACHE_DIRECTORY:raise ValueError('Use the RouterManager-owned Linux update cache')
    return stage.parent.parent,stage


def ownership(*stores):
    for store in stores:
        info=os.fstat(store.fd)
        if info.st_uid!=os.geteuid() or info.st_mode&0o022:
            raise PermissionError('Linux update directories require this user and non-writable group/other permissions')


def signed_catalog(staged,stage):
    manifest=staged.read_bytes('manifest.json',MAX_MANIFEST_BYTES)
    release=verify_manifest(manifest,PUBLIC_KEY,expected_platform='linux-x64')
    if release.catalog_url and release.catalog_sha256!=stage.name:raise ValueError('Signed update does not belong to this stage')
    raw=staged.read_bytes('catalog.json',MAX_CATALOG_BYTES)
    if not release.catalog_url:
        from .linux_full_package import inventory
        with staged.open_read('package.zip') as source:catalog=inventory(source,release)
        if hashlib.sha256(canonical(catalog)).hexdigest()!=stage.name:raise ValueError('Derived Linux inventory differs from stage identity')
    else:catalog=parse_catalog(raw,release)
    if canonical(catalog)!=raw:raise ValueError('Linux transaction catalogs must use canonical encoding')
    return release,catalog


def validate_job(job,identity,stage):
    if (not isinstance(job,dict) or set(job)!={'schema','product','platform','root','stage','runtime_sha256','parent'}
            or job.get('schema')!=1 or job.get('product')!='RouterManager' or job.get('platform')!='linux-x64'
            or job.get('root')!=identity or job.get('stage')!=stage.name
            or not isinstance(job.get('runtime_sha256'),str) or not re.fullmatch('[a-f0-9]{64}',job['runtime_sha256'])):
        raise ValueError('Invalid Linux worker job identity')
    validate_ticket(job['parent'])
    return job


def private_job(staged,identity,stage):
    state=staged.state(JOB,max_bytes=65536)
    if state is None or state['kind']!='file' or state['mode']!=0o600:
        raise ValueError('Linux job must be a private regular file')
    return validate_job(staged.read_json(JOB,65536),identity,stage)


def read_job(path):
    root,stage=stage_location(path)
    with RootedFiles(root) as files,RootedFiles(stage) as staged,RootedFiles(stage.parent) as cache:
        ownership(files,staged,cache)
        job=private_job(staged,files.identity,stage)
        release,catalog=signed_catalog(staged,stage)
    return root,stage,job,release,catalog


def local_runtime_inventory(files,baseline,cancel=lambda:False):
    value=copy.deepcopy(baseline);catalog_files(value)
    for index,entry in enumerate(value['files']):
        path=entry['path']
        if cancel():raise InterruptedError('Linux helper preparation cancelled')
        actual=files.state(path,lambda: _cancel(cancel))
        if actual==content_state(entry):continue
        if path.split('/')[0] not in {'images','themes','user_scripts'} or actual is None:
            raise ValueError('Installed runtime differs from its official baseline: '+path)
        replacement=dict(actual,path=path)
        if actual['kind']=='file':
            digest=hashlib.sha256();raw_digest=hashlib.sha256();compressor=zlib.compressobj(6);count=0;raw_size=0
            with files.open_read(path) as source:
                for block in iter(lambda:source.read(1024*1024),b''):
                    _cancel(cancel);raw_size+=len(block)
                    if raw_size>actual['size']:raise ValueError('Custom runtime resource grew during preparation')
                    raw_digest.update(block);data=compressor.compress(block);digest.update(data);count+=len(data)
                data=compressor.flush();digest.update(data);count+=len(data)
            if raw_digest.hexdigest()!=actual['sha256'] or files.state(path,lambda:_cancel(cancel))!=actual:raise ValueError('Custom runtime resource changed during preparation')
            replacement.update(blob_sha256=digest.hexdigest(),blob_size=count)
        value['files'][index]=replacement
    catalog_files(value)
    value['local_snapshot_data']=True
    return value


def _cancel(cancel):
    if cancel():raise InterruptedError('Linux helper preparation cancelled')


def prepare_job(root,stage,*,cancel=lambda:False,timeout=300):
    if type(timeout) not in {int,float} or not math.isfinite(timeout) or not 0<timeout<=300:
        raise ValueError('Invalid Linux helper preparation timeout')
    deadline=time.monotonic()+timeout;requested_cancel=cancel
    def bounded_cancel():
        if time.monotonic()>=deadline:raise TimeoutError('Linux helper preparation timed out')
        return requested_cancel()
    cancel=bounded_cancel
    root=Path(root).absolute();stage=Path(stage).absolute();stage_location(stage/JOB)
    if (not getattr(sys,'frozen',False) or Path(sys.executable).absolute()!=root/'LuXiaoGuan'
            or stage.parent.parent!=root):raise ValueError('Prepare Linux updates only from this packaged installation')
    _cancel(cancel)
    with RootedFiles(root) as files,RootedFiles(stage) as staged,RootedFiles(stage.parent) as cache:
        ownership(files,staged,cache);release,incoming=signed_catalog(staged,stage)
        verify_launcher(root/LAUNCHER)
        if cache.state(PENDING) is not None or cache.state('active.json') is not None:
            raise BlockingIOError('An existing Linux update must be resolved first')
        make_install_plan(files,incoming,lambda:_cancel(cancel))
        baseline=json.loads(files.read_bytes(BASELINE,MAX_CATALOG_BYTES))
        inventory=local_runtime_inventory(files,baseline,cancel)
        parent=capture_parent(root/'LuXiaoGuan')
        identity=files.identity
    digest=hashlib.sha256(canonical(inventory)).hexdigest()
    runtime=prepare_runtime_snapshot(root,stage,inventory,cancel=cancel)
    job=dict(schema=1,product='RouterManager',platform='linux-x64',root=identity,
             stage=stage.name,runtime_sha256=digest,parent=parent)
    with cache_lock(stage.parent) as acquired:
        if not acquired:raise BlockingIOError('Another update owns the Linux preparation lease')
        with RootedFiles(root) as files,RootedFiles(stage) as staged,RootedFiles(stage.parent) as cache:
            ownership(files,staged,cache)
            if files.identity!=identity or cache.state(PENDING) is not None or cache.state('active.json') is not None:
                raise BlockingIOError('Linux installation state changed during helper preparation')
            _cancel(cancel);signed_catalog(staged,stage);verify_runtime_snapshot(stage,digest,cancel=cancel)
            staged.flush('manifest.json');staged.flush('catalog.json')
            staged.write_json(JOB,job,65536)
    return runtime,stage/JOB


def arm_job(root,stage,*,appearance=None):
    """Short commit step after background preparation; no runtime scan or copy."""
    root=Path(root).absolute();stage=Path(stage).absolute();stage_location(stage/JOB)
    if (not getattr(sys,'frozen',False) or Path(sys.executable).absolute()!=root/'LuXiaoGuan'
            or stage.parent.parent!=root):raise ValueError('Arm Linux updates only from this packaged installation')
    with cache_lock(stage.parent) as acquired:
        if not acquired:raise BlockingIOError('Another Linux update owns the preparation lease')
        with RootedFiles(root) as files,RootedFiles(stage) as staged,RootedFiles(stage.parent) as cache:
            ownership(files,staged,cache)
            verify_launcher(root/LAUNCHER)
            job=private_job(staged,files.identity,stage)
            if job['parent']!=capture_parent(root/'LuXiaoGuan'):
                raise ValueError('Prepare this update again from the current application process')
            if cache.state(PENDING) is not None or cache.state('active.json') is not None:
                raise BlockingIOError('Another Linux update must be resolved first')
            if appearance is not None:
                theme=str(appearance.get('theme','dark.qss'))
                scale=appearance.get('scale',100)
                if Path(theme).name!=theme or not theme.endswith('.qss') or type(scale) is not int or not 50<=scale<=300:
                    raise ValueError('Invalid updater appearance')
                staged.write_json('linux-appearance.json',dict(theme=theme,scale=scale),4096)
            data=(stage.name+'\n').encode('ascii')
            cache.create(PENDING,byte_state(data,0o600),io.BytesIO(data))
    return stage/'runtime',stage/JOB


def disarm_job(root,stage):
    """Only undo this still-live parent's unstarted handoff, never an active transaction."""
    root=Path(root).absolute();stage=Path(stage).absolute();stage_location(stage/JOB)
    with cache_lock(stage.parent) as acquired:
        if not acquired:return False
        with RootedFiles(root) as files,RootedFiles(stage) as staged,RootedFiles(stage.parent) as cache:
            ownership(files,staged,cache);job=private_job(staged,files.identity,stage)
            if job['parent']!=capture_parent(root/'LuXiaoGuan') or cache.state('active.json') is not None:return False
            value=(stage.name+'\n').encode('ascii')
            if cache.state(PENDING)!=byte_state(value,0o600):return False
            cache.unlink(PENDING);return True
