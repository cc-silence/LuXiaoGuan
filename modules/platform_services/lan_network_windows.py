"""Read IPv4 adapter identity and renew only one revalidated DHCP interface."""
import base64
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import time
import tempfile
import uuid
from .runtime import subprocess_creationflags
from .network_diagnostics import emit


def powershell(script):
    pre="$ErrorActionPreference='Stop';[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false);"
    encoded=base64.b64encode((pre+script).encode('utf-16-le')).decode('ascii')
    executable=str(Path(os.environ.get('SystemRoot','C:/Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe')
    return executable,['-NoProfile','-NonInteractive','-EncodedCommand',encoded]


def inspect(peer=None,timeout=10,*,adapter_id=None):
    # Qt uses the native adapter API; registry reads supply DHCP lease metadata.
    # Avoid slow PowerShell network-provider initialization during preflight.
    import socket
    import ctypes
    import winreg
    from PySide6.QtNetwork import QNetworkInterface
    local=''
    try:
        address=str(ipaddress.IPv4Address(peer))
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as probe:
            probe.connect((address,9));local=probe.getsockname()[0]
    except (OSError,ValueError,TypeError):pass
    from .network_environment import windows as environment
    context=environment()
    rows=[]
    for nic in QNetworkInterface.allInterfaces():
        addresses=[]
        for entry in nic.addressEntries():
            try:addresses.append(str(ipaddress.IPv4Interface(entry.ip().toString()+'/'+str(entry.prefixLength()))))
            except ValueError:continue
        if not addresses and adapter_id is None:continue
        luid=ctypes.c_ulonglong();guid=(ctypes.c_ubyte*16)()
        api=ctypes.windll.iphlpapi
        if api.ConvertInterfaceIndexToLuid(nic.index(),ctypes.byref(luid)):continue
        if api.ConvertInterfaceLuidToGuid(ctypes.byref(luid),ctypes.byref(guid)):continue
        identity=str(uuid.UUID(bytes_le=bytes(guid)))
        if adapter_id is not None and identity!=str(adapter_id):continue
        keypath=r'SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces\{'+identity+'}'
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,keypath) as key:
                def value(name,default=None):
                    try:return winreg.QueryValueEx(key,name)[0]
                    except OSError:return default
                enabled=value('EnableDHCP')
                dhcp=enabled==1 if enabled in (0,1) else None
                dhcp_server=value('DhcpServer','') if dhcp else ''
                lease_end=value('LeaseTerminatesTime') if dhcp else None
                gateway=value('DhcpDefaultGateway' if dhcp else 'DefaultGateway',[])
                dns=value('NameServer','') or (value('DhcpNameServer','') if dhcp else '')
        except OSError:dhcp=None;gateway=[];dns='';dhcp_server='';lease_end=None
        gateway=[gateway] if isinstance(gateway,str) else gateway
        rows.append(dict(**context,dhcp_server=dhcp_server,
            lease_expired=(lease_end<=time.time()) if type(lease_end) is int and lease_end>0 else None,id=identity,index=nic.index(),name=nic.humanReadableName(),mac=nic.hardwareAddress(),
            addresses=addresses,gateways=list(gateway or []),dns=str(dns).replace(',',' ').split(),dhcp=dhcp,
            connected=bool(nic.flags() & QNetworkInterface.IsRunning),manager='windows',renewable=dhcp is True,
            wireless=True if nic.type()==QNetworkInterface.Wifi else False if nic.type()==QNetworkInterface.Ethernet else None,
            route=any(str(ipaddress.IPv4Interface(v).ip)==local for v in addresses)))
    return rows


def renew(adapter,timeout,permit):
    if not permit():raise RuntimeError('Network recovery request expired')
    identity=str(uuid.UUID(adapter['id']));index=int(adapter['index'])
    mac=''.join(c for c in str(adapter['mac']) if c.isalnum()).upper()
    if len(mac)!=12 or any(c not in '0123456789ABCDEF' for c in mac):raise ValueError('Invalid adapter identity')
    expires=int(time.time()+timeout)
    with tempfile.TemporaryDirectory(prefix='router-lan-') as evidence_dir:
        evidence_path=Path(evidence_dir)/'evidence.jsonl'
        script=r"""
function Read-SelectedAdapter {
 $selected=Get-CimInstance Win32_NetworkAdapterConfiguration -Filter "SettingID='{__GUID__}'"
 if(@($selected).Count -ne 1 -or !$selected.DHCPEnabled -or $selected.InterfaceIndex -ne __INDEX__){exit 12}
 if(($selected.MACAddress -replace '[:-]','').ToUpper() -ne '__MAC__'){exit 12}
 return $selected
}
function Write-LeaseEvidence($stage,$selected,$code) {
 try {
  $row=@{stage=$stage;native_error=$code;adapter_found=($null -ne $selected);
   ip_enabled=$selected.IPEnabled;dhcp_enabled=$selected.DHCPEnabled;
   adapter_index=$selected.InterfaceIndex;address_count=@($selected.IPAddress | Where-Object {$_}).Count}
  [IO.File]::AppendAllText('__EVIDENCE__','LEASE:'+($row | ConvertTo-Json -Compress)+[Environment]::NewLine)
 } catch {} # Evidence must not prevent address recovery.
}
if([DateTimeOffset]::UtcNow.ToUnixTimeSeconds() -ge __EXPIRES__){exit 12}
$nic=Read-SelectedAdapter
Write-LeaseEvidence 'lease_before' $nic $null
try{
 if([DateTimeOffset]::UtcNow.ToUnixTimeSeconds() -ge __EXPIRES__){exit 12}
 $released=Invoke-CimMethod -InputObject $nic -MethodName ReleaseDHCPLease
 Write-LeaseEvidence 'lease_release' $nic ([int]$released.ReturnValue)
 if($released.ReturnValue -notin @(0,1,83)){exit (1000+[int]$released.ReturnValue)}
 # Revalidate identity and DHCP mode; a rejected old lease must not block renewal.
 $nic=Read-SelectedAdapter
 Write-LeaseEvidence 'lease_after_release' $nic $null
 # Always reacquire after releasing, even if the deadline has just elapsed.
 $renewed=Invoke-CimMethod -InputObject $nic -MethodName RenewDHCPLease
 Write-LeaseEvidence 'lease_renew' $nic ([int]$renewed.ReturnValue)
 try {
  $observed=Get-CimInstance Win32_NetworkAdapterConfiguration -Filter "SettingID='{__GUID__}'"
  Write-LeaseEvidence 'lease_after_renew' $observed $null
 } catch {Write-LeaseEvidence 'lease_after_renew' $null $null}
 if($renewed.ReturnValue -ne 0 -and $renewed.ReturnValue -ne 1){exit (2000+[int]$renewed.ReturnValue)}
 if($released.ReturnValue -eq 1 -or $renewed.ReturnValue -eq 1){exit 15}
}catch [System.UnauthorizedAccessException]{exit 5}
catch [Microsoft.Management.Infrastructure.CimException]{if([int]$_.Exception.StatusCode -eq 2){exit 5};throw}
    """.replace('__GUID__',identity).replace('__INDEX__',str(index)).replace('__MAC__',mac).replace('__EXPIRES__',str(expires)).replace('__EVIDENCE__',str(evidence_path).replace("'","''"))
        executable,args=powershell(script)
        native=None;code=None;authorization_failed=False
        def decode(code,elevated=False):
            nonlocal native
            emit('lease_process',system_exit_code=code,elevated=elevated)
            if 1000<=code<3000:
                native=code%1000
                emit('lease_release' if code<2000 else 'lease_renew',
                     'permission_denied' if native==91 else 'lease_unsupported' if native==64 else 'lease_failed',
                     native_error=native,elevated=elevated)
                return 5 if native==91 else 16 if native==64 else 14
            return code
        try:
            result=subprocess.run([executable,*args],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                                  timeout=timeout,creationflags=subprocess_creationflags())
            code=decode(result.returncode)
            if code==5 and permit() and expires-time.time()>1:
                from .update_elevation import run_elevated_update
                accepted,code=run_elevated_update(executable,subprocess.list2cmdline(args),
                    timeout_ms=max(1000,int((expires-time.time())*1000)),show_window=False)
                if accepted:code=decode(code,elevated=True)
                if not accepted:
                    authorization_failed=True
                    emit('lease_process','authorization_failed',system_exit_code=code,elevated=True)
                    raise RuntimeError('\u7f51\u7edc\u66f4\u65b0\u672a\u83b7\u5f97\u7ba1\u7406\u5458\u6388\u6743\u3002')
            if code==15:raise RuntimeError("\u7cfb\u7edf\u62a5\u544a\u7f51\u7edc\u53d8\u66f4\u9700\u8981\u91cd\u542f\u7535\u8111\u540e\u624d\u80fd\u5b8c\u6210\uff0c\u5f53\u524d\u5730\u5740\u5237\u65b0\u7ed3\u679c\u672a\u786e\u8ba4\u3002")
            if code==16:raise RuntimeError("\u8be5\u7f51\u5361\u4e0d\u652f\u6301\u7cfb\u7edf\u7684\u5730\u5740\u91ca\u653e\u6216\u7eed\u79df\u63a5\u53e3\uff0c\u8bf7\u5728\u7cfb\u7edf\u7f51\u7edc\u8bbe\u7f6e\u4e2d\u91cd\u65b0\u8fde\u63a5\u8be5\u7f51\u5361\u3002")
            if code:raise RuntimeError('\u7f51\u5361\u5730\u5740\u91cd\u65b0\u83b7\u53d6\u672a\u5b8c\u6210\uff0c\u8bf7\u5728\u7cfb\u7edf\u7f51\u7edc\u8bbe\u7f6e\u4e2d\u68c0\u67e5\u8be5\u7f51\u5361\u3002')
            emit('lease_process','system_accepted')
        except subprocess.TimeoutExpired:
            emit('lease_process','lease_timeout')
            raise RuntimeError('\u7b49\u5f85\u7f51\u5361\u91cd\u65b0\u83b7\u53d6\u5730\u5740\u8d85\u65f6\uff0c\u8bf7\u68c0\u67e5\u8be5\u7f51\u5361\u7684\u5f53\u524d\u914d\u7f6e\u3002') from None
        except RuntimeError as exc:
            exc.network_reason='authorization_failed' if authorization_failed else 'permission_denied' if code==5 else 'lease_unsupported' if code==16 else 'reboot_required' if code==15 else 'lease_failed'
            exc.network_retryable=not authorization_failed and code==14 and native==82
            emit('lease_process',exc.network_reason)
            raise
        finally:
            try:
                with evidence_path.open('rb') as stream:_lease_evidence(stream.read(16384))
            except OSError:emit('lease_after_renew','inspection_failed')


def _lease_evidence(output):
    """Read bounded numeric evidence without persisting raw output or addresses."""
    if not isinstance(output,bytes):return
    for line in output[:16384].decode('utf-8',errors='replace').splitlines()[:16]:
        if not line.startswith('LEASE:'):continue
        try:row=json.loads(line[6:])
        except (ValueError,TypeError):continue
        if not isinstance(row,dict):continue
        stage=row.pop('stage',None)
        if stage not in {'lease_before','lease_release','lease_after_release','lease_renew','lease_after_renew'}:continue
        emit(stage,**{k:v for k,v in row.items() if k in {
            'native_error','adapter_found','ip_enabled','dhcp_enabled','adapter_index','address_count'}})
