"""Read-only supplicant evidence; association alone never establishes connectivity."""
import re
import time
from .command_builder import quote_argument

CONNECTING=frozenset(('SCANNING','AUTHENTICATING','ASSOCIATING','ASSOCIATED','4WAY_HANDSHAKE','GROUP_HANDSHAKE'))
DISCONNECTED=frozenset(('DISCONNECTED','INTERFACE_DISABLED','INACTIVE'))
MAC=re.compile(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}')


def valid_bssid(value):
    return isinstance(value,str) and MAC.fullmatch(value) is not None and value!='00:00:00:00:00:00' and int(value[:2],16)%2==0


def status_values(text):
    if not isinstance(text,str) or len(text)>8192:return {}
    result={}
    for line in text.splitlines():
        key,sep,value=line.partition('=')
        if sep and key in {'wpa_state','ssid','bssid'}:
            if key in result:return {}
            result[key]=value
    return result


def ssid_bytes(value):
    """Decode printf_encode used by supplicant STATUS without interpreting Python escapes."""
    if not isinstance(value,str) or len(value)>256:return None
    result=bytearray();index=0
    escapes={'n':10,'r':13,'t':9,'e':27,'\\':92,'"':34}
    while index<len(value):
        char=value[index];index+=1
        if char!='\\':
            try:result.extend(char.encode('utf-8'))
            except UnicodeError:return None
            continue
        if index==len(value):return None
        char=value[index];index+=1
        if char=='x' and re.fullmatch('[0-9a-fA-F]{2}',value[index:index+2]):
            result.append(int(value[index:index+2],16));index+=2
        elif char in escapes:result.append(escapes[char])
        else:return None
    return bytes(result)


def verdict(status,row):
    if not isinstance(status,dict):return 'unknown'
    state=status.get('wpa_state')
    if not isinstance(state,str):return 'unknown'
    if state in DISCONNECTED:return 'disconnected'
    if state in CONNECTING:return 'connecting'
    if state!='COMPLETED':return 'unknown'
    bssid=status.get('bssid')
    if not valid_bssid(bssid):return 'unknown'
    expected=row.get('configured_ssid')
    if not isinstance(expected,str):return 'unknown'
    try:expected_bytes=expected.encode('utf-8')
    except UnicodeError:return 'unknown'
    if not 1<=len(expected_bytes)<=32 or ssid_bytes(status.get('ssid'))!=expected_bytes:
        return 'unknown'
    locked=row.get('configured_bssid')
    # Some iwinfo drivers report this station's own MAC as info.bssid.
    # STATUS on the exact supplicant interface identifies the authenticated peer.
    if locked and (not valid_bssid(locked) or locked.lower()!=bssid.lower()):return 'unknown'
    return 'connected'


def enrich(data,runner,cancel,*,clock=time.monotonic):
    """Prefer native bss_info; older firmware uses bounded wpa_cli STATUS only."""
    if not isinstance(data,dict):return data
    rows=data.get('networks')
    if not isinstance(rows,list) or len(rows)>64:return data
    deadline=clock()+4
    cli_available=True
    for row in rows:
        if cancel.is_set():raise InterruptedError('Wireless status cancelled')
        if not isinstance(row,dict) or row.get('mode')!='sta':continue
        status=row.pop('authentication',None)
        if status is None and cli_available:
            iface=row.get('interface')
            remaining=deadline-clock()
            if isinstance(iface,str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,64}',iface) and remaining>0:
                command='command -v wpa_cli >/dev/null 2>&1 || exit 127; exec wpa_cli -i '+quote_argument(iface)+' status'
                try:
                    result=runner.run('wireless_authentication',command,timeout=min(2,remaining),cancel_event=cancel,output_limit=8192)
                except InterruptedError:raise
                except (OSError,TimeoutError):
                    cli_available=False
                else:
                    if cancel.is_set() or result.cancelled:raise InterruptedError('Wireless status cancelled')
                    if result.timed_out or result.exit_code==127:cli_available=False
                    if result.exit_code==0 and not result.timed_out and not result.exception_type and not result.failure_stage:
                        status=status_values(result.stdout)
        row['connection_state']=verdict(status,row)
        if row['connection_state']=='connected':
            row.update(associated=True,association_known=True,bssid=status['bssid'].lower(),ssid=row['configured_ssid'])
        elif row['connection_state']=='disconnected':
            row.update(associated=False,association_known=True)
        row.pop('configured_ssid',None);row.pop('configured_bssid',None)
    return data
