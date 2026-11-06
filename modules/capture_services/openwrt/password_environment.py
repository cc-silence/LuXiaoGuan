"""Scoped wireless ownership and isolated network configuration for verification."""
import re
import shlex
from .command_builder import quote_argument
_SECTION=r'(?:[A-Za-z0-9_]+|@wifi-iface\[[0-9]+\])'


def interface_snapshot(text):
    if not isinstance(text,str):raise ValueError('Invalid wireless configuration snapshot')
    if len(text) > 1024 * 1024:
        raise ValueError('Wireless configuration snapshot exceeds limit')
    disabled={};devices={};options=[];seen=set()
    # UCI prints shell-quoted values, including literal newlines inside quotes.
    records=shlex.shlex(text,posix=True)
    records.whitespace='\r\n'
    records.whitespace_split=True
    records.commenters=''
    for index,record in enumerate(records):
        if index >= 16384:
            raise ValueError('Wireless configuration record count exceeds limit')
        key,separator,value=record.partition('=')
        if not separator:
            raise ValueError('Invalid UCI snapshot record')
        key=key.strip()
        if key in seen:
            raise ValueError('Duplicate UCI snapshot field')
        seen.add(key)
        declaration=re.fullmatch(r'wireless\.('+_SECTION+r')',key)
        if declaration and value=='wifi-iface':disabled[declaration.group(1)]=None
        match=re.fullmatch(r'wireless\.('+_SECTION+r')\.(device|disabled)',key)
        if match:options.append((*match.groups(),value))
    for name,key,value in options:
        if name not in disabled:continue
        if key=='disabled':
            if value not in ('0','1','true','false','yes','no','on','off'):raise ValueError('Invalid wireless disabled flag')
            disabled[name]=value
        elif re.fullmatch('[A-Za-z0-9_]+',value):devices[name]=value
        else:raise ValueError('Invalid wireless radio ownership')
    if any(name not in devices for name in disabled):
        raise ValueError('Wireless interface radio ownership is unavailable')
    return disabled,devices


def restore_disabled(name,value):
    key='wireless.'+name+'.disabled'
    if value is None:return 'uci -q delete '+quote_argument(key)+' || true'
    return 'uci set '+quote_argument(key+'='+value)


def isolated_network(net_name):
    return [f"uci set network.{net_name}.{key}='{value}'" for key,value in
            (('defaultroute','0'),('peerdns','0'),('ipv6','0'),('delegate','0'),('metric','60000'))]


# netifd applies configuration differences. The legacy wifi reload wrapper also
# runs legacy driver hooks without forwarding the requested device on OpenWrt 23.05.
RELOAD_CHANGED_CONFIG='ubus call network reload >/dev/null 2>&1'
