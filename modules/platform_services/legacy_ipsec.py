"""Narrow, backed-up additions to an already assigned local legacy IPsec policy."""
import hashlib
from threading import Event
import time
from .process_capture import capture_process
from .legacy_ipsec_plan import IPsecPlan, target_values, verify_dump
from .legacy_ipsec_store import read_snapshot, preserved, object_keys
from .windows_network_services import require_legacy_services
from .legacy_ipsec_backup import create_backup_directory, windows_directory, write_result

BACKUP_LIMIT=16*1024*1024


class NativeStore:
    def __init__(self):
        self.deadline=time.monotonic()+90
        self.executable=windows_directory()/'System32'/'netsh.exe'

    def command(self,args):
        remaining=self.deadline-time.monotonic()
        if remaining<=0:raise TimeoutError('IPsec transaction deadline exceeded')
        result=capture_process([str(self.executable),'ipsec',*args],Event(),
                               timeout=min(10,remaining),output_limit=4*1024*1024)
        if result.returncode:
            error=OSError('IPsec command failed: '+str(result.returncode))
            error.ipsec_exit_code=result.returncode
            raise error
        return result.stdout

    def snapshot(self):
        require_legacy_services()
        return read_snapshot()

    def dump(self):return self.command(('dump',))

    def mutate(self,args):return self.command(('static',*args))

    def backup(self):
        directory=create_backup_directory()
        backup=directory/'before.ipsec'
        self.mutate(('exportpolicy','file='+str(backup)))
        if not backup.is_file():
            raise OSError('IPsec policy export is missing; no rules were added')
        size=backup.stat().st_size
        if not 0<size<=BACKUP_LIMIT:
            raise OSError('IPsec policy export is invalid; no rules were added')
        with backup.open('rb') as stream:data=stream.read(BACKUP_LIMIT+1)
        if len(data)!=size or len(data)>BACKUP_LIMIT:
            raise OSError('IPsec export changed or exceeded its limit while reading; no rules were added')
        return directory,hashlib.sha256(data).hexdigest()


def ensure_target(address,port,store=None):
    address,port=target_values(address,port)
    store=store or NativeStore()
    before=store.snapshot()
    if not before.active_id:
        return dict(success=True,status='no_assigned_policy',route='ipsec',address=address,port=port)
    matching={key for key,values in before.nodes.items()
              if key.lower().startswith('ipsecpolicy{') and str(values.get('ipsecname',(0,None))[1]).casefold()==before.name.casefold()}
    if matching!={before.active_id}:
        raise ValueError('Assigned IPsec policy name is ambiguous')
    plan=IPsecPlan(before.name,before.active_id,address,port)
    existing=object_keys(before,set(plan.names))
    if existing:
        verify_dump(plan,store.dump())
        if store.snapshot().digest!=before.digest:raise OSError('IPsec policy changed during verification')
        return dict(success=True,status='existing_verified',route='ipsec',address=address,port=port)
    directory,backup_hash=store.backup()
    result=dict(success=False,status='preparing',route='ipsec',address=address,port=port,backup=str(directory/'before.ipsec'),backup_sha256=backup_hash,completed_steps=0,policy_id=before.active_id,owned_names=list(plan.names),snapshot_before=before.digest)
    journal=directory/'result.json'
    last=before
    attempted=False
    try:
        # A durable receipt must exist before any policy mutation.
        write_result(journal,result)
        for args in plan.additions():
            if store.snapshot().digest!=last.digest:raise OSError('Concurrent IPsec policy change detected')
            attempted=True
            store.mutate(args)
            last=store.snapshot()
            preserved(before,last,set(plan.names))
            result['completed_steps']+=1
            write_result(journal,result)
        verify_dump(plan,store.dump())
        if store.snapshot().digest!=last.digest:raise OSError('IPsec policy changed during final verification')
        result.update(success=True,status='appended_verified')
        write_result(journal,result)
    except Exception as exc:
        result.update(success=False,error=str(exc),status='failed',rollback='not_needed')
        code=getattr(exc,'ipsec_exit_code',None)
        if type(code) is int:result['native_error']=code
        if attempted:
            # Never restore the whole policy, or delete objects after unrelated edits.
            try:
                if isinstance(store,NativeStore):store.deadline=time.monotonic()+30
                current=store.snapshot()
                preserved(before,current,set(plan.names))
                for args in plan.removals():
                    if store.snapshot().digest!=current.digest:raise OSError('Concurrent change during rollback')
                    name=next(x[5:] for x in args if x.startswith('name='))
                    if object_keys(current,{name}):store.mutate(args)
                    current=store.snapshot()
                    preserved(before,current,set(plan.names))
                # whenChanged may legitimately differ after removing our rule.
                if set(current.nodes)!=set(before.nodes):raise OSError('Owned IPsec objects remain')
                preserved(current,before,set())
                result['rollback']='verified'
            except Exception as rollback:
                result['rollback']='unconfirmed: '+str(rollback)
        try:
            write_result(journal,result)
        except OSError as journal_error:
            result['journal_error']=str(journal_error)
        return result
    return result


def apply_request(request):
    from .local_gpo_firewall import _policy_mutex
    with _policy_mutex(timeout_ms=10000):
        return ensure_target(request['address'],request['port'])
