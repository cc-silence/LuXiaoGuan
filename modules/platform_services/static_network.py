"""Compare-and-restore static IPv4 migration shared by all LAN apply callers."""
import copy
import ipaddress
import json
import os
import time
from pathlib import Path
from .runtime import current_os, WINDOWS, LINUX
from .network_diagnostics import emit


class StaticMigrationError(RuntimeError):
    pass


def migration_plan(plan):
    rows=plan.get('adapters',[])
    static=[r for r in rows if r.get('dhcp') is False]
    if not static:return None
    if plan.get('selection_source')!='ssh_socket' or len(rows)!=1 or not rows[0].get('route'):
        raise StaticMigrationError('\u56fa\u5b9a IP \u7f51\u5361\u5f52\u5c5e\u4e0d\u660e\u786e\uff0c\u672a\u4fee\u6539\u7535\u8111\u548c\u8def\u7531\u5668\u3002')
    if plan.get('static_route_conflict'):raise StaticMigrationError('Another adapter overlaps the new LAN subnet; automatic migration stopped')
    old=[ipaddress.IPv4Interface(v) for v in plan['old_addresses']]
    new=[ipaddress.IPv4Interface(v) for v in plan['new_addresses']]
    if len(old)!=1 or len(new)!=1 or new[0].network.prefixlen>30:
        raise StaticMigrationError('Automatic static migration requires one ordinary IPv4 LAN subnet')
    row=copy.deepcopy(rows[0]);addresses=[ipaddress.IPv4Interface(v) for v in row['addresses']]
    related=[a for a in addresses if a.ip in old[0].network]
    if len(related)!=1:raise StaticMigrationError('Multiple local LAN addresses require manual review')
    local=related[0]
    if local.ip==new[0].ip:raise StaticMigrationError('Client address conflicts with the new router address')
    if local.network==new[0].network or (local.ip in new[0].network and new[0].ip in local.network):
        wanted=ipaddress.IPv4Interface(str(local.ip)+'/'+str(new[0].network.prefixlen))
    else:
        host=int(local.ip)-int(old[0].network.network_address)
        candidate=int(new[0].network.network_address)+host
        if candidate>=int(new[0].network.broadcast_address) or candidate<=int(new[0].network.network_address):
            raise StaticMigrationError('The current host number does not fit the new subnet')
        wanted=ipaddress.IPv4Interface(str(ipaddress.IPv4Address(candidate))+'/'+str(new[0].network.prefixlen))
        if wanted.ip==new[0].ip:raise StaticMigrationError('Client address conflicts with the new router address')
    if not wanted.network.network_address < wanted.ip < wanted.network.broadcast_address:
        raise StaticMigrationError('\u7f29\u5c0f\u7f51\u6bb5\u540e\uff0c\u7535\u8111 IP \u4f1a\u53d8\u6210\u7f51\u7edc\u5730\u5740\u6216\u5e7f\u64ad\u5730\u5740\uff0c\u672a\u4fee\u6539\u7f51\u7edc\u3002')
    return dict(adapter=row,old_router=str(old[0].ip),new_router=str(new[0].ip),old_address=str(local),new_address=str(wanted))


def transition(current,before,after):
    """Accept only partial application of our exact write, never unrelated edits."""
    if set(current)!=set(before):return False
    for key,value in current.items():
        if key in {'addresses','gateways'}:
            if not set(before[key]) & set(after[key]) <= set(value) <= set(before[key]) | set(after[key]):return False
        elif value!=before[key] and value!=after[key]:return False
    return True


class StaticMigration:
    def __init__(self,plan,driver=None,folder=None):
        self.plan=plan
        if driver is None:
            if current_os()==WINDOWS:
                from .static_network_windows import Driver
            elif current_os()==LINUX:
                from .static_network_linux import Driver
            else:raise StaticMigrationError('Unsupported static network platform')
            driver=Driver(plan['adapter'])
        self.driver=driver;self.baseline=None;self.expected=None;self.pending=None;self.committed=False;self.router_confirmed=False;self.confirmation_pending=False;self.record_owned=False
        if folder is None:
            from modules.config_services.config_store import user_config_dir
            folder=user_config_dir()/'network-recovery'
        self.folder=Path(folder)
        import hashlib
        self.path=self.folder/(hashlib.sha256(str(plan['adapter']['id']).encode()).hexdigest()[:24]+'.json')

    def _save(self):
        self.folder.mkdir(parents=True,exist_ok=True)
        payload=dict(schema=1,plan=self.plan,baseline=self.baseline,expected=self.expected,pending=self.pending,router_confirmed=self.router_confirmed,confirmation_pending=self.confirmation_pending)
        temp=self.path.with_suffix('.tmp')
        with temp.open('w',encoding='utf-8') as stream:
            json.dump(payload,stream,ensure_ascii=True);stream.flush();os.fsync(stream.fileno())
        temp.replace(self.path)
        self.record_owned=True

    def _verify_live(self,state):
        verify=getattr(self.driver,'verify',None)
        if verify is not None:verify(state)

    def _write(self,wanted):
        current=self.driver.read()
        if current!=self.expected:raise StaticMigrationError('Local network changed outside this operation; not overwriting it')
        self.pending=copy.deepcopy(wanted);self._save()
        self.driver.write(self.expected,wanted)
        actual=self.driver.read()
        if actual!=wanted:raise StaticMigrationError('Local network readback did not match the requested settings')
        self._verify_live(wanted)
        self.expected=copy.deepcopy(wanted);self.pending=None;self._save()

    def _reconcile_record(self,current):
        """Retire only a proven restored local transaction; retain uncertain ones."""
        if not self.path.exists():return
        try:
            with self.path.open('rb') as stream:
                raw=stream.read(65537)
            if len(raw)>65536:raise ValueError('Oversized recovery record')
            saved=json.loads(raw)
            if not isinstance(saved,dict):raise ValueError('Invalid recovery record')
            old_plan=saved['plan'];old_adapter=old_plan['adapter']
            identity=all(old_adapter[k]==self.plan['adapter'][k] for k in ('id','index','mac'))
            restored=(saved.get('router_confirmed') is False
                      and saved.get('confirmation_pending') is False
                      and saved.get('baseline')==current and saved.get('expected')==current
                      and 'pending' in saved and saved['pending'] in (None,current)
                      and old_plan['old_router']==self.plan['old_router']
                      and old_plan['old_address']==self.plan['old_address'])
            completed=False
            if saved.get('router_confirmed') is True and isinstance(saved.get('expected'),dict):
                final=copy.deepcopy(saved['expected'])
                final['addresses']=sorted(set(a for a in final['addresses'] if a!=old_plan['old_address']) | {old_plan['new_address']})
                completed=(current==final and 'pending' in saved and saved['pending'] in (None,final)
                           and old_plan['new_router']==self.plan['old_router']
                           and old_plan['new_address']==self.plan['old_address'])
            safe=saved.get('schema')==1 and identity and (restored or completed)
            if not safe:raise ValueError('Recovery remains unresolved')
            self._verify_live(current)
            if self.path.read_bytes()!=raw:raise ValueError('Recovery record changed during verification')
        except (OSError,ValueError,KeyError,TypeError):
            raise StaticMigrationError('\u53d1\u73b0\u4e0a\u6b21\u672a\u5b8c\u6210\u7684\u7f51\u7edc\u6062\u590d\u8bb0\u5f55\uff0c\u8bf7\u5148\u6838\u5bf9\u6062\u590d\uff0c\u672a\u4fee\u6539\u8def\u7531\u5668\u3002') from None
        # One archive per adapter bounds storage while preserving the latest evidence.
        self.path.replace(self.path.with_suffix('.resolved'))

    def prepare(self,cancel):
        if cancel.is_set():raise InterruptedError('Cancelled before local network preparation')
        self.driver.cancel=cancel
        self.driver.deadline=time.monotonic()+60
        self.baseline=self.driver.read();self.expected=copy.deepcopy(self.baseline)
        original=self.plan['adapter']
        if set(self.baseline['addresses'])!=set(original['addresses']) or self.baseline['gateways']!=original['gateways'] or self.baseline['dns']!=original['dns']:
            raise StaticMigrationError('Static network snapshot is stale; reopen LAN settings')
        if len(self.baseline['gateways'])>1:raise StaticMigrationError('Multiple default routes require manual review')
        self._verify_live(self.baseline)
        self._reconcile_record(self.baseline)
        self._save()
        wanted=copy.deepcopy(self.baseline)
        if not any(v.split('/')[0]==self.plan['new_address'].split('/')[0] for v in wanted['addresses']):
            wanted['addresses']=sorted(wanted['addresses']+[self.plan['new_address']])
            self._write(wanted)
        emit('static_prepare','system_accepted',adapter_count=1,target_count=1)
        if cancel.is_set():raise InterruptedError('Cancelled after local network preparation')

    def applying(self):
        """After remote apply starts, local completion or compensation must finish."""
        if self.driver.cancel is not None and self.driver.cancel.is_set():
            raise InterruptedError('Cancelled before applying network settings')
        self.driver.cancel=None

    def activate(self,deadline=None):
        self.driver.deadline=min(time.monotonic()+25,deadline or float('inf'))
        wanted=copy.deepcopy(self.expected)
        for key in ('gateways','dns'):
            wanted[key]=list(dict.fromkeys(self.plan['new_router'] if v==self.plan['old_router'] else v for v in wanted[key]))
        if wanted!=self.expected:self._write(wanted)
        elif self.driver.read()!=self.expected:raise StaticMigrationError('Local network changed before confirmation')
        self._verify_live(self.expected)
        emit('static_activate','system_accepted')

    def confirming(self):
        # A lost confirm reply is not evidence that the router rolled back.
        self.confirmation_pending=True
        try:self._save()
        except Exception:
            self.confirmation_pending=False
            raise

    def router_restored(self):
        self.confirmation_pending=False
        self._save()

    def commit(self):
        self.router_confirmed=True
        self._save()
        self.driver.deadline=time.monotonic()+25
        wanted=copy.deepcopy(self.expected)
        if self.plan['old_address']!=self.plan['new_address']:
            wanted['addresses']=sorted(set(v for v in wanted['addresses'] if v!=self.plan['old_address']) | {self.plan['new_address']})
        self._write(wanted)
        self.committed=True;self.path.unlink(missing_ok=True)
        emit('static_commit','system_accepted')

    def rollback(self):
        if self.router_confirmed or self.committed or self.baseline is None or not self.record_owned:return
        if self.confirmation_pending:
            raise StaticMigrationError('Router confirmation is unresolved; retaining both client addresses')
        self.driver.cancel=None  # Recovery must finish even after cancellation.
        self.driver.deadline=time.monotonic()+25
        actual=self.driver.read()
        if actual!=self.expected and (self.pending is None or not transition(actual,self.expected,self.pending)):
            raise StaticMigrationError('\u7535\u8111\u7f51\u7edc\u5df2\u88ab\u5176\u4ed6\u64cd\u4f5c\u4fee\u6539\uff0c\u672a\u8986\u76d6\uff1b\u6062\u590d\u8bb0\u5f55\u5df2\u4fdd\u7559\u3002')
        self.expected=copy.deepcopy(actual);self.pending=copy.deepcopy(self.baseline);self._save()
        if actual!=self.baseline:self.driver.write(actual,self.baseline)
        if self.driver.read()!=self.baseline:raise StaticMigrationError('Original local network restoration could not be verified')
        self._verify_live(self.baseline)
        self.path.unlink(missing_ok=True);self.baseline=None;self.record_owned=False
        emit('static_restore','system_accepted')


def for_recovery(recovery):
    if recovery is None:return None
    plan=migration_plan(recovery.plan)
    return StaticMigration(plan) if plan else None
