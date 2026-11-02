"""Debian NetworkManager support; never launch a competing DHCP client."""
import ipaddress
import json
import os
import shutil
import subprocess
import time
from .network_diagnostics import emit


def command(args,timeout=5):
    value=subprocess.run(args,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout,
                         env=dict(os.environ,LC_ALL='C'))
    if value.returncode:
        error=RuntimeError('\u7cfb\u7edf\u7f51\u7edc\u7ba1\u7406\u5668\u672a\u5b8c\u6210\u8bf7\u6c42\uff0c\u8bf7\u5728\u7cfb\u7edf\u7f51\u7edc\u8bbe\u7f6e\u4e2d\u68c0\u67e5\u8be5\u8fde\u63a5\u3001\u64cd\u4f5c\u6388\u6743\u548c\u8def\u7531\u5668\u72b6\u6001\u3002')
        error.system_exit_code=value.returncode
        raise error
    if len(value.stdout)>1048576:raise RuntimeError('Network response too large')
    return value.stdout.decode('utf-8').strip()


def inspect(peer=None,timeout=10,*,adapter_id=None):
    deadline=time.monotonic()+timeout
    def query(args):
        remaining=deadline-time.monotonic()
        if remaining<=0:raise TimeoutError("Adapter inspection timed out")
        return command(args,min(5,remaining))
    from PySide6.QtNetwork import QNetworkInterface
    route=None
    try:
        address=str(ipaddress.IPv4Address(peer))
        route=json.loads(query(['ip','-j','-4','route','get',address]))[0].get('dev')
    except (ValueError,TypeError,KeyError,IndexError,RuntimeError):pass
    from .network_environment import linux as environment
    context=environment()
    rows=[]
    inventory=json.loads(query(['ip','-j','-4','address','show']))
    if adapter_id is not None:
        inventory=[row for row in inventory if str(row.get('ifindex'))==str(adapter_id)][:1]
    for row in inventory[:128]:
        if row.get('ifname')=='lo':continue
        if adapter_id is not None and str(row.get('ifindex'))!=str(adapter_id):continue
        name=row['ifname'];connection='';dhcp=None;gateways=[];dns=[]
        if shutil.which('nmcli'):
            try:
                connection=query(['nmcli','-g','GENERAL.CON-UUID','device','show',name])
                if connection and connection!='--':
                    dhcp=query(['nmcli','-g','ipv4.method','connection','show','uuid',connection])=='auto'
                    gateways=query(['nmcli','-g','IP4.GATEWAY','device','show',name]).splitlines()
                    dns=query(['nmcli','-g','IP4.DNS','device','show',name]).splitlines()
            except RuntimeError:connection=''
        kind=QNetworkInterface.interfaceFromIndex(row['ifindex']).type()
        rows.append(dict(**context,wireless=True if kind==QNetworkInterface.Wifi else False if kind==QNetworkInterface.Ethernet else None,id=str(row['ifindex']),index=row['ifindex'],name=name,mac=row.get('address',''),
            addresses=[str(ipaddress.IPv4Interface(str(a['local'])+'/'+str(a['prefixlen']))) for a in row.get('addr_info',[]) if a.get('family')=='inet'],
            gateways=gateways,dns=dns,dhcp=dhcp,connected='UP' in row.get('flags',[]),manager='networkmanager' if connection else 'unmanaged',
            connection=connection,renewable=bool(connection and dhcp is True),route=name==route))
    return rows


def renew(adapter,timeout,permit):
    started=time.monotonic()
    rows=inspect(timeout=min(5,timeout/3),adapter_id=adapter['id']);current=next((r for r in rows if r['id']==adapter['id']),None)
    if not current or current['mac']!=adapter['mac'] or current['connection']!=adapter.get('connection') or current['dhcp'] is not True:
        raise RuntimeError('\u7f51\u5361\u6216\u8fde\u63a5\u914d\u7f6e\u5df2\u53d8\u5316\uff0c\u672a\u66f4\u6539\u7f51\u7edc\u3002')
    if not permit():raise RuntimeError('Network recovery request expired')
    remaining=timeout-(time.monotonic()-started)
    if remaining<=1:raise TimeoutError('Network recovery request expired')
    try:
        command(['nmcli','--wait',str(max(1,int(remaining)-1)),'connection','up','uuid',current['connection'],'ifname',current['name']],remaining)
    except (RuntimeError,TimeoutError,subprocess.TimeoutExpired) as exc:
        code=getattr(exc,'system_exit_code',None)
        emit('lease_process','lease_timeout' if isinstance(exc,(TimeoutError,subprocess.TimeoutExpired)) else 'lease_failed',system_exit_code=code)
        exc.network_retryable=code==3
        exc.network_reason='lease_timeout' if code==3 else 'lease_failed'
        raise
    emit('lease_process','system_accepted',system_exit_code=0)
