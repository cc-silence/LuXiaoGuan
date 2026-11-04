"""Reclaim verified Linux stage members; never follow links or remove unknown data."""
import json
import logging
from pathlib import Path,PurePosixPath
import re
from .locations import CACHE_DIRECTORY,cache_lock,linked,validate_stage
from .manifest import version_key
from .linux_update_jobs import signed_catalog,ownership
from .linux_install_plan import catalog_files,content_state,byte_state
from .linux_transaction import JOURNAL
from .linux_update_worker import worker_lease
from modules.platform_services.linux_update_files import RootedFiles
from modules.platform_services.linux_recovery_launcher import PENDING

LOG=logging.getLogger('routermanager.update')
SMALL_RECORDS={'manifest.json','catalog.json','linux-result.json',JOURNAL}
TRANSIENT={'linux-download.json','linux-appearance.json','linux-job.json','linux-progress.json','linux-validation.json','runtime-snapshot.json','linux-package-download.json'}


def cleanup_stage(stage,*,remove_metadata=False,cancel=lambda:False):
    def check():
        if cancel():raise InterruptedError('Linux cache cleanup cancelled')
    check();stage=validate_stage(stage);root=stage.parent.parent
    with RootedFiles(stage) as staged:
        release,catalog=signed_catalog(staged,stage)
        from .linux_partial_cache import INTENT,intent as package_intent
        if staged.state(INTENT) is not None:
            expected_intent=package_intent(release,stage)
            moved_intent=dict(expected_intent,stage=release.sha256)
            if release.catalog_url or staged.read_json(INTENT,4096) not in (expected_intent,moved_intent):return False
        journal=staged.read_json(JOURNAL) if staged.state(JOURNAL) is not None else None
        if journal and journal.get('state') not in {'complete','rolled_back'}:return False
        snapshot=staged.read_json('runtime-snapshot.json',16*1024*1024) if staged.state('runtime-snapshot.json') is not None else None
        runtime=stage/'runtime'
        if runtime.exists() or linked(runtime):
            if snapshot is None:return False
            entries=catalog_files(snapshot['catalog']);directories=set()
            with RootedFiles(runtime) as files:
                for relative,entry in entries.items():
                    if cancel():return False
                    actual=files.state(relative,check)
                    if actual is not None:
                        if actual!=content_state(entry):return False
                        files.unlink(relative)
                    parent=PurePosixPath(relative).parent
                    while str(parent)!='.':directories.add(str(parent));parent=parent.parent
                for name in sorted(directories,key=lambda n:n.count('/'),reverse=True):
                    try:files.rmdir(name)
                    except FileNotFoundError:pass
                    except OSError:return False
            try:staged.rmdir('runtime')
            except OSError:return False
        intent=staged.read_json('linux-download.json',2*1024*1024) if staged.state('linux-download.json') is not None else None
        expected=dict(schema=1,stage=stage.name,partials=sorted({e['sha256']+'.partial' for e in catalog['files'] if e['kind']=='file'}))
        modern=dict(expected,schema=2,compressed_partials=sorted({e['blob_sha256']+'.z.part' for e in catalog['files'] if e['kind']=='file'}))
        if intent is not None:
            if intent not in (expected,modern):return False
            for name in intent['partials']+intent.get('compressed_partials',[]):
                state=staged.state(name,check)
                if state is not None:
                    if state['kind']!='file' or state['mode']!=0o600:return False
                    staged.unlink(name)
        for entry in catalog['files']:
            if entry['kind']!='file':continue
            for key,suffix,size in [('sha256','.file',entry['size']),('blob_sha256','.z',entry['blob_size'])]:
                if cancel():return False
                name=entry[key]+suffix;state=staged.state(name,check)
                if state is not None:
                    if state['kind']!='file' or state['sha256']!=entry[key] or state['size']!=size:return False
                    staged.unlink(name)
        # Never remove journals until the runtime and every signed payload are gone.
        for name in TRANSIENT:
            if staged.state(name) is not None:staged.unlink(name)
    if remove_metadata:
        with RootedFiles(stage) as staged:
            if not release.catalog_url:
                if any(name not in SMALL_RECORDS|{'package.zip'} for name in staged.list_names()):return False
                archive=staged.state('package.zip',check)
                if archive!=dict(kind='file',mode=0o600,size=release.size,sha256=release.sha256):return False
                staged.unlink('package.zip')
            if any(name not in SMALL_RECORDS for name in staged.list_names()):return False
            for name in SMALL_RECORDS:
                if staged.state(name) is not None:staged.unlink(name)
        with RootedFiles(stage.parent) as cache:cache.rmdir(stage.name)
    return True


def prune_completed(root,current_version=None,*,keep_stage=None,cancel=lambda:False):
    root=Path(root).absolute();cache_path=root/CACHE_DIRECTORY
    if not cache_path.exists():return
    if linked(cache_path):raise ValueError('Linked Linux update cache')
    with cache_lock(cache_path) as acquired:
        if not acquired:return
        with RootedFiles(cache_path) as cache:
            ownership(cache)
            if cache.state(PENDING) is not None or cache.state('active.json') is not None:return
            try:
                with worker_lease(cache),worker_lease(cache,'linux-ui.lock'):
                    stages=[]
                    for name in cache.list_names(limit=256):
                        if cancel():return
                        if not re.fullmatch('[a-f0-9]{64}',name):continue
                        stage=validate_stage(cache_path/name,root)
                        if keep_stage is not None and stage==Path(keep_stage).absolute():continue
                        try:
                            from .linux_partial_cache import prune_partial
                            if prune_partial(stage,current_version,cancel):continue
                            with RootedFiles(stage) as staged:
                                release,_=signed_catalog(staged,stage)
                                journal=staged.read_json(JOURNAL) if staged.state(JOURNAL) is not None else None
                                complete=journal and journal.get('state') in {'complete','rolled_back'}
                                obsolete=not journal and current_version and version_key(release.version)<=version_key(current_version)
                                if complete or obsolete:stages.append((version_key(release.version),name,stage))
                        except (OSError,ValueError,KeyError,TypeError):LOG.warning('Unverified Linux cache retained')
                    for index,(_,_,stage) in enumerate(sorted(stages,reverse=True)):
                        if cancel():return
                        try:cleanup_stage(stage,remove_metadata=index>=2,cancel=cancel)
                        except (OSError,ValueError,KeyError,TypeError):LOG.warning('Linux update cleanup deferred')
            except BlockingIOError:return
