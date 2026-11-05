"""Plan Linux replacements without overwriting unknown or locally edited files."""
import hashlib
from pathlib import PurePosixPath
from .catalog import BASELINE
from .linux_catalog import validate_entries, managed_linux_path, MAX_FILES
from .manifest import canonical, version_key


def catalog_files(catalog):
    if (not isinstance(catalog,dict) or catalog.get('schema')!=2 or catalog.get('product')!='RouterManager'
            or catalog.get('platform')!='linux-x64' or catalog.get('migrations',[])!=[]):
        raise ValueError('A native Linux catalog without configuration migrations is required')
    version_key(catalog['version'])
    revision=catalog.get('resource_revision',0)
    if type(revision) is not int or revision<0:raise ValueError('Invalid Linux resource revision')
    retired=catalog.get('retired',[])
    if not isinstance(retired,list) or len(retired)>MAX_FILES:raise ValueError('Invalid Linux retired inventory')
    entries=validate_entries(catalog.get('files'))
    seen=set()
    for path in retired:
        path=managed_linux_path(path)
        if path in seen or path in entries:raise ValueError('Duplicate or active retired Linux path')
        seen.add(path)
    return entries


def content_state(entry):
    keys=('kind','target') if entry['kind']=='symlink' else ('kind','sha256','size','mode')
    return {key:entry[key] for key in keys}


def byte_state(data, mode=0o644):
    return dict(kind='file',sha256=hashlib.sha256(data).hexdigest(),size=len(data),mode=mode)


def plan(files, catalog, check=lambda:None, *, collect_conflicts=False):
    incoming=catalog_files(catalog)
    baseline_state=files.state(BASELINE,check,max_bytes=8*1024*1024)
    old_raw=files.read_bytes(BASELINE,8*1024*1024) if baseline_state is not None else None
    if old_raw is not None and byte_state(old_raw,baseline_state['mode'])!=baseline_state:
        raise ValueError('Installed baseline changed while reading')
    if old_raw is not None:
        import json
        old=json.loads(old_raw);previous=catalog_files(old)
        current=(version_key(old['version']),old.get('resource_revision',0))
        future=(version_key(catalog['version']),catalog.get('resource_revision',0))
        if future<=current:raise ValueError('Linux update must advance version or resource revision')
    else:previous={}
    operations=[];preserved=[];unchanged=0;conflicts=[]
    for path,item in incoming.items():
        check();actual=files.state(path,check);after=content_state(item)
        official=content_state(previous[path]) if path in previous else None
        if actual==after:unchanged+=1;continue
        custom=path.split('/')[0] in {'images','themes','user_scripts'}
        if actual is not None and official==after and custom:
            preserved.append(path);continue
        if actual is not None and actual!=official:
            if not collect_conflicts:raise ValueError('Locally modified or unowned Linux update target: '+path)
            conflicts.append(path);continue
        operations.append(dict(path=path,before=actual,after=after))
    retired=set(catalog.get('retired',[]))
    if catalog.get('delivery_mode')=='full-package':retired.update(set(previous)-set(incoming))
    for path in sorted(retired):
        check();actual=files.state(path,check)
        if actual is None:continue
        if path not in previous or actual!=content_state(previous[path]):
            if not collect_conflicts:raise ValueError('Locally modified or unowned retired Linux path: '+path)
            conflicts.append(path);continue
        operations.append(dict(path=path,before=actual,after=None))
    operations.sort(key=lambda row:(row['path']=='LuXiaoGuan',row['after'] is not None and row['after']['kind']=='symlink',row['path']))
    new_raw=canonical(catalog)
    if len(new_raw)>8*1024*1024:raise ValueError('Linux baseline exceeds limit')
    operations.append(dict(path=BASELINE,before=byte_state(old_raw) if old_raw is not None else None,after=byte_state(new_raw)))
    # Preserve the actual baseline mode too; it is not a configuration file.
    if old_raw is not None:operations[-1]['before']=baseline_state
    return dict(operations=operations,preserved=preserved,unchanged=unchanged,baseline=new_raw,conflicts=conflicts)


def names(path, token, index):
    parent=PurePosixPath(path).parent
    prefix='.routermanager-'+token+'-'+str(index)
    return str(parent/(prefix+'.new')),str(parent/(prefix+'.old'))
