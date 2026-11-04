"""Verify the exact public source snapshot reviewed for both working trees."""
import hashlib
import json
from pathlib import PurePosixPath

RECORD='docs/debian12-source-inventory.json'
EXCLUDED={RECORD,'docs/debian12-source-sync.json'}
ROOTS=('modules/','router_app/','scripts/','tests/','themes/','images/','native/','tools/','infra/','.github/','docs/','bin/')


def source_member(name):
    if not isinstance(name,str) or name in EXCLUDED or '\\' in name or ':' in name or any(ord(c)<32 for c in name):return False
    path=PurePosixPath(name)
    if path.is_absolute() or any(p in {'','.','..'} for p in name.split('/')):return False
    return (name.startswith(ROOTS) or name in {'AGENTS.md','config/app_update.json','.gitignore','launch.pyw'}
            or len(path.parts)==1 and (name.endswith(('.py','.spec')) or name.startswith('requirements')))


def digest(path):
    if not path.is_file() or path.is_symlink():return None
    value=hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda:source.read(65536),b''):value.update(block)
    return value.hexdigest()


def members(root,git):
    tracked=git(root,'ls-files','-z').split('\0')
    pending=git(root,'ls-files','--others','--exclude-standard','-z').split('\0')
    return {name for name in tracked+pending if source_member(name)}


def inspect_snapshot(upstream,root,saved,git):
    reference=saved.get('source_inventory')
    if reference is None:return [],False
    if not isinstance(reference,dict) or set(reference)!={'path','sha256'} or reference['path']!=RECORD:
        raise ValueError('Invalid source inventory reference')
    path=root/RECORD
    if path.stat().st_size>4*1024**2 or digest(path)!=reference['sha256']:
        raise ValueError('Reviewed source inventory fingerprint mismatch')
    record=json.loads(path.read_text(encoding='utf-8'));rows=record.get('files')
    if (record.get('schema')!=1 or record.get('upstream_commit')!=saved['upstream_commit']
            or not isinstance(rows,dict) or not 1<=len(rows)<=20000):raise ValueError('Invalid reviewed source inventory')
    current=members(upstream,git)|members(root,git)
    drift=set(current)^set(rows)
    for name,row in rows.items():
        if (not source_member(name) or not isinstance(row,dict) or set(row)!={'upstream_sha256','linux_sha256'}
                or any(v is not None and (not isinstance(v,str) or len(v)!=64 or any(c not in '0123456789abcdef' for c in v)) for v in row.values())):
            raise ValueError('Invalid reviewed source inventory member')
        if digest(upstream/name)!=row['upstream_sha256'] or digest(root/name)!=row['linux_sha256']:drift.add(name)
    return sorted(drift),True
