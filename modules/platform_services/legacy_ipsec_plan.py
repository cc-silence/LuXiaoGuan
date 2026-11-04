"""Pure legacy IPsec target plans and locale-independent dump verification."""
from dataclasses import dataclass
import hashlib
import ipaddress
import re

POLICY_ID=re.compile(r'ipsecPolicy\{[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}\}',re.IGNORECASE)


def target_values(address,port):
    if not isinstance(address,str) or len(address)>15:raise ValueError('Router address must be an IPv4 literal')
    try:ip=ipaddress.IPv4Address(address)
    except ipaddress.AddressValueError:
        raise ValueError('Router address must be an IPv4 literal') from None
    if ip.is_unspecified or ip.is_multicast or ip.is_loopback or str(ip)=='255.255.255.255':
        raise ValueError('Router address must be a unicast IPv4 address')
    if type(port) is str and len(port)<=5 and port.isascii() and port.isdecimal():port=int(port)
    if type(port) is not int or not 1<=port<=65535:raise ValueError('Invalid SSH port')
    return str(ip),port


@dataclass(frozen=True)
class IPsecPlan:
    policy: str
    policy_id: str
    address: str
    port: int

    def __post_init__(self):
        if (not isinstance(self.policy,str) or not 1<=len(self.policy)<=256
                or any(ord(c)<32 or c in '\\"' for c in self.policy)):
            raise ValueError('Unsupported IPsec policy name')
        if not isinstance(self.policy_id,str) or not POLICY_ID.fullmatch(self.policy_id):raise ValueError('Invalid active IPsec policy ID')
        if target_values(self.address,self.port)!=(self.address,self.port):raise ValueError('Noncanonical SSH target')

    @property
    def token(self):
        return 'RouterManager-SSH-'+hashlib.sha256((self.policy_id.lower()+'|'+self.address+'|'+str(self.port)).encode()).hexdigest()[:20]

    @property
    def names(self):return (self.token+'-filters',self.token+'-permit',self.token+'-rule')

    def additions(self):
        filters,action,rule=self.names
        return (
            ('add','filterlist','name='+filters,'description=RouterManager owned SSH endpoint'),
            ('add','filter','filterlist='+filters,'srcaddr=me','dstaddr='+self.address,'dstmask=255.255.255.255',
             'protocol=TCP','srcport=0','dstport='+str(self.port),'mirrored=yes'),
            ('add','filteraction','name='+action,'action=permit','description=RouterManager owned SSH endpoint'),
            ('add','rule','name='+rule,'policy='+self.policy,'filterlist='+filters,'filteraction='+action,
             'conntype=all','activate=yes','kerberos=yes','description=RouterManager owned SSH endpoint'),
        )

    def removals(self):
        filters,action,rule=self.names
        return (('delete','rule','name='+rule,'policy='+self.policy),
                ('delete','filterlist','name='+filters),('delete','filteraction','name='+action))


_OPTIONS=re.compile(r'([A-Za-z][A-Za-z0-9]*)=(?:"([^"\r\n]*)"|([^\s"]+))')


def dump_rows(text):
    if not isinstance(text,str) or len(text)>4*1024*1024:raise ValueError('IPsec dump exceeds limit')
    rows=[]
    for raw in text.splitlines():
        line=raw.strip()
        if line.lower().startswith('netsh ipsec static '):line=line[19:]
        match=re.match(r'(?i)^add (filterlist|filteraction|filter|rule|policy)\s+',line)
        if match is None:continue
        options={}
        for option in _OPTIONS.finditer(line[match.end():]):
            key=option[1].lower()
            if key in options:raise ValueError('Duplicate IPsec dump option')
            options[key]=option[2] if option[2] is not None else option[3]
        rows.append((match[1].lower(),options))
        if len(rows)>4096:raise ValueError('Too many IPsec dump rows')
    return rows


def verify_dump(plan,text):
    if isinstance(text,bytes):
        from .legacy_ipsec_output import text_candidates
        for candidate in text_candidates(text):
            try:
                verify_dump(plan,candidate)
                return True
            except ValueError:
                continue
        raise ValueError('IPsec dump does not verify in the supported output encodings')
    rows=dump_rows(text);filters,action,rule=plan.names
    def one(kind,key,value):
        found=[r for k,r in rows if k==kind and r.get(key,'').casefold()==value.casefold()]
        if len(found)!=1:raise ValueError('IPsec owned object is missing or ambiguous: '+kind)
        return found[0]
    one('filterlist','name',filters)
    f=one('filter','filterlist',filters)
    expected=dict(srcaddr='me',dstaddr=plan.address,srcport='0',dstport=str(plan.port),mirrored='yes')
    if f.get('protocol','').lower() not in ('tcp','6'):
        raise ValueError('IPsec filter is not TCP')
    if any(f.get(k,'').lower()!=v for k,v in expected.items()):raise ValueError('IPsec filter differs from exact SSH endpoint')
    if f.get('srcmask','255.255.255.255') not in ('255.255.255.255','32'):
        raise ValueError('IPsec filter has a broad source mask')
    if f.get('dstmask','255.255.255.255') not in ('255.255.255.255','32'):
        raise ValueError('IPsec filter has a broad destination mask')
    a=one('filteraction','name',action)
    if a.get('action','').lower()!='permit':raise ValueError('IPsec action is not permit')
    r=one('rule','name',rule)
    expected=dict(policy=plan.policy,filterlist=filters,filteraction=action,conntype='all',activate='yes')
    if r.get('tunnel','0.0.0.0') != '0.0.0.0':
        raise ValueError('IPsec rule unexpectedly uses a tunnel')
    if any(r.get(k,'').casefold()!=v.casefold() for k,v in expected.items()):raise ValueError('IPsec rule differs from planned association')
    if any(k=='rule' and item.get('name','').casefold()!=rule.casefold() and item.get('filterlist','').casefold()==filters.casefold() for k,item in rows):
        raise ValueError('Owned IPsec filter list is referenced by another rule')
    return True
