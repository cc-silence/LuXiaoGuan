"""Read-only legacy policy snapshots and preservation checks; no registry writes."""
from dataclasses import dataclass
import base64
import hashlib
import json
from .legacy_ipsec_plan import POLICY_ID

BASE=r'SOFTWARE\Policies\Microsoft\Windows\IPSec'
LOCAL=BASE+r'\Policy\Local'


@dataclass(frozen=True)
class PolicySnapshot:
    nodes: dict
    active_id: str
    name: str

    @property
    def digest(self):
        return hashlib.sha256(json.dumps(self.nodes,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def _values(key,registry):
    count=registry.QueryInfoKey(key)[1]
    if count>2048:raise ValueError('IPsec registry value limit exceeded')
    result={};total_bytes=2
    for i in range(count):
        name,value,kind=registry.EnumValue(key,i)
        if isinstance(value,(str,bytes)) and len(value)>4*1024*1024:
            raise ValueError('IPsec value exceeds snapshot limit')
        if isinstance(value,list):
            if len(value)>4096 or any(not isinstance(item,str) for item in value):
                raise ValueError('Unsupported or oversized IPsec multi-string')
            if sum(len(item) for item in value)>4*1024*1024:
                raise ValueError('IPsec multi-string exceeds snapshot limit')
        if isinstance(value,bytes):value={'base64':base64.b64encode(value).decode('ascii')}
        if not isinstance(value,(str,int,list,dict)):raise ValueError('Unsupported IPsec value type')
        if len(json.dumps(value,ensure_ascii=True))>4*1024*1024:raise ValueError('IPsec value exceeds snapshot limit')
        total_bytes+=len(json.dumps({name.lower():(kind,value)},ensure_ascii=True))
        if total_bytes>8*1024*1024:raise ValueError('IPsec registry node exceeds snapshot limit')
        result[name.lower()]=(kind,value)
    return result


def _tree(path,registry):
    rows={};queue=[('',path)];total_bytes=2
    while queue:
        relative,current=queue.pop()
        try:key=registry.OpenKey(registry.HKEY_LOCAL_MACHINE,current,0,registry.KEY_READ|registry.KEY_WOW64_64KEY)
        except FileNotFoundError:
            if relative:raise
            return {}
        with key:
            info=registry.QueryInfoKey(key)
            if len(rows)+len(queue)+info[0]>=2048:raise ValueError('IPsec policy snapshot exceeds node limit')
            values=_values(key,registry)
            total_bytes+=len(json.dumps({relative:values},ensure_ascii=True))
            if total_bytes>8*1024*1024:raise ValueError('IPsec snapshot exceeds memory limit')
            rows[relative]=values
            for i in range(info[0]):
                child=registry.EnumKey(key,i)
                queue.append(((relative+'\\' if relative else '')+child,current+'\\'+child))
    if len(json.dumps(rows))>8*1024*1024:raise ValueError('IPsec snapshot exceeds memory limit')
    return rows


def read_snapshot():
    import winreg
    from .windows_network_services import domain_joined
    if domain_joined():raise PermissionError('Domain-managed computers require administrator-managed IPsec exceptions')
    for suffix in (r'\GPTIPSECPolicy',r'\Policy\Cache',r'\Policy\Persistent'):
        data=_tree(BASE+suffix,winreg)
        if len(data)>1 or any(data.get('',{}).values()):
            raise PermissionError('Domain, cached or persistent IPsec policy detected; automatic append stopped')
    rows=_tree(LOCAL,winreg)
    root=rows.get('',{})
    if 'activepolicy' not in root:return PolicySnapshot(rows,'','')
    pointer_kind,pointer=root['activepolicy']
    if pointer_kind!=winreg.REG_SZ or not isinstance(pointer,str):raise ValueError('Unknown IPsec active policy format')
    if not pointer:return PolicySnapshot(rows,'','')
    prefix=LOCAL+'\\'
    identifier=pointer[len(prefix):]
    if not pointer.casefold().startswith(prefix.casefold()) or not POLICY_ID.fullmatch(identifier):
        raise ValueError('Active IPsec policy points outside the local store or has an invalid ID')
    active=next((name for name in rows if name.casefold()==identifier.casefold()),None)
    if active is None:raise ValueError('Assigned local IPsec policy was not found')
    name_kind,name=rows[active].get('ipsecname',(0,''))
    if name_kind!=winreg.REG_SZ or not isinstance(name,str) or not name:raise ValueError('Cannot read assigned IPsec policy name')
    return PolicySnapshot(rows,active,name)


def object_keys(snapshot,names):
    normalized={name.casefold() for name in names}
    return {key for key,values in snapshot.nodes.items()
            if isinstance(values.get('ipsecname',(0,None))[1],str)
            and values['ipsecname'][1].casefold() in normalized}


def preserved(before,after,owned_names):
    if before.active_id!=after.active_id or before.name!=after.name:
        raise ValueError('Assigned IPsec policy changed during the transaction')
    created=set(after.nodes)-set(before.nodes)
    if len(created)>3 or created-object_keys(after,owned_names):
        raise ValueError('Unrelated IPsec objects changed during the transaction')
    for key,values in before.nodes.items():
        current=after.nodes.get(key)
        if current is None:raise ValueError('An original IPsec object disappeared')
        if key!=before.active_id:
            if current!=values:raise ValueError('An original IPsec object was modified')
            continue
        ignored={'whenchanged','ipsecnfareference'}
        if {k:v for k,v in values.items() if k not in ignored}!={k:v for k,v in current.items() if k not in ignored}:
            raise ValueError('Assigned policy settings were modified')
        def refs(value):
            kind,data=value.get('ipsecnfareference',(7,[]))
            if kind!=7 or not isinstance(data,list) or any(not isinstance(v,str) for v in data):raise ValueError('Unknown IPsec rule reference format')
            return {v.casefold() for v in data}
        old,new=refs(values),refs(current)
        added_rules={(LOCAL+'\\'+key).casefold() for key in created if key.lower().startswith('ipsecnfa{')}
        if not old.issubset(new) or new-old-added_rules:
            raise ValueError('Assigned policy references changed outside this transaction')
    return True
