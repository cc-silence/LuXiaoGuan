"""Bounded PowerShell adapter changes; commands never enable DHCP or touch IPv6."""
import base64
import json
from pathlib import Path
import subprocess
import tempfile
import time
import uuid
from .lan_network_windows import powershell
from .runtime import subprocess_creationflags
from .static_network import StaticMigrationError
from .network_diagnostics import emit

IDENTITY_READ=r""" $nic=@([Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces() | Where-Object {$_.Id.Trim('{}') -eq $req.id})
 if($nic.Count -ne 1){throw 'Adapter identity or static mode changed'}
 $ipv4=$nic[0].GetIPProperties().GetIPv4Properties()
 if($ipv4.Index -ne $req.index -or $nic[0].GetPhysicalAddress().ToString().ToUpper() -ne $req.mac -or $ipv4.IsDhcpEnabled -or $nic[0].OperationalStatus -ne 'Up'){throw 'Adapter identity or static mode changed'}"""

SCRIPT=r"""
$req=__REQUEST__
function Deadline {if([DateTimeOffset]::UtcNow.ToUnixTimeSeconds() -ge $req.expires){throw 'Request expired'}}
function Identity {
 Deadline
__IDENTITY_READ__
}
function State {
 Identity
 $ips=@(Get-NetIPAddress -InterfaceIndex $req.index -AddressFamily IPv4 | Where-Object {$_.PrefixOrigin -eq 'Manual'} | ForEach-Object {$_.IPAddress+'/'+$_.PrefixLength} | Sort-Object)
 $routes=@(Get-NetRoute -InterfaceIndex $req.index -AddressFamily IPv4 -ErrorAction Stop | Where-Object {$_.DestinationPrefix -eq '0.0.0.0/0'})
 if($routes.Count -gt 2 -or @($routes | Select-Object -ExpandProperty RouteMetric -Unique).Count -gt 1){throw 'Ambiguous default routes'}
 $dns=@(Get-DnsClientServerAddress -InterfaceIndex $req.index -AddressFamily IPv4 | ForEach-Object {$_.ServerAddresses})
 return @{addresses=$ips;gateways=@($routes | ForEach-Object {$_.NextHop} | Sort-Object);dns=$dns;metric=$(if($routes.Count){[int]$routes[0].RouteMetric}else{0})}
}
function Same($a,$b){return (($a | ConvertTo-Json -Compress -Depth 5) -eq ($b | ConvertTo-Json -Compress -Depth 5))}
function EqualState($a,$b){
 return (Same @($a.addresses) @($b.addresses)) -and (Same @($a.gateways) @($b.gateways)) -and (Same @($a.dns) @($b.dns)) -and ($a.metric -eq $b.metric)
}
try {
 $state=State
 if($req.action -eq 'write'){
  if(!(EqualState $state $req.before)){throw 'Local network changed outside this operation'}
  foreach($address in $req.after.addresses){
   Deadline
   if($address -notin $state.addresses){
    $parts=$address.Split('/')
    $sameIP=@($state.addresses | Where-Object {$_.Split('/')[0] -eq $parts[0]})
    if($sameIP.Count -eq 1){
     Set-NetIPAddress -InterfaceIndex $req.index -IPAddress $parts[0] -PrefixLength ([int]$parts[1]) -ErrorAction Stop | Out-Null
    }else{
     if((Get-NetIPInterface -InterfaceIndex $req.index -AddressFamily IPv4).DadTransmits -lt 1){throw 'Duplicate address detection disabled'}
     New-NetIPAddress -InterfaceIndex $req.index -IPAddress $parts[0] -PrefixLength ([int]$parts[1]) -ErrorAction Stop | Out-Null
    }
    $limit=[DateTime]::UtcNow.AddSeconds(6)
    do {
     $entry=Get-NetIPAddress -InterfaceIndex $req.index -IPAddress $parts[0] -AddressFamily IPv4
     if($entry.AddressState -eq 'Duplicate'){throw 'Duplicate client address detected'}
     if($entry.AddressState -eq 'Preferred'){break}
     Start-Sleep -Milliseconds 100
    }while([DateTime]::UtcNow -lt $limit)
    if($entry.AddressState -ne 'Preferred'){throw 'Client address uniqueness could not be confirmed'}
   }
  }
  foreach($gateway in $req.after.gateways){
   Deadline
   if($gateway -notin $state.gateways){New-NetRoute -InterfaceIndex $req.index -DestinationPrefix '0.0.0.0/0' -NextHop $gateway -RouteMetric $req.after.metric -ErrorAction Stop | Out-Null}
  }
  foreach($gateway in $state.gateways){
   Deadline
   if($gateway -notin $req.after.gateways){Remove-NetRoute -InterfaceIndex $req.index -DestinationPrefix '0.0.0.0/0' -NextHop $gateway -Confirm:$false -ErrorAction Stop}
  }
  Deadline
  if(!(Same @($state.dns) @($req.after.dns))){
   if(@($req.after.dns).Count -eq 0){throw 'Empty static DNS replacement is unsupported'}
   $dnsObject=Get-DnsClientServerAddress -InterfaceIndex $req.index -AddressFamily IPv4
   $dnsObject | Set-DnsClientServerAddress -ServerAddresses @($req.after.dns) -ErrorAction Stop
  }
  foreach($address in $state.addresses){
   Deadline
   if($address -notin $req.after.addresses -and @($req.after.addresses | Where-Object {$_.Split('/')[0] -eq $address.Split('/')[0]}).Count -eq 0){Remove-NetIPAddress -InterfaceIndex $req.index -IPAddress $address.Split('/')[0] -Confirm:$false -ErrorAction Stop}
  }
  $state=State
  if(!(EqualState $state $req.after)){throw 'Static network readback mismatch'}
 }
 [IO.File]::WriteAllText('__OUTPUT__',($state | ConvertTo-Json -Compress -Depth 5))
 exit 0
}catch [System.UnauthorizedAccessException]{exit 5}
catch {
 if($_.Exception -is [Microsoft.Management.Infrastructure.CimException] -and ([int]$_.Exception.StatusCode -eq 2 -or $_.Exception.NativeErrorCode -eq 5)){exit 5}
 if($_.Exception.Message -eq 'Duplicate address detection disabled'){exit 25}
 if($_.Exception.Message -eq 'Duplicate client address detected'){exit 21}
 if($_.Exception.Message -eq 'Adapter identity or static mode changed'){exit 22}
 if($_.Exception.Message -eq 'Local network changed outside this operation'){exit 23}
 if($_.Exception.Message -eq 'Client address uniqueness could not be confirmed'){exit 24}
 exit 20
}
"""


SCRIPT=SCRIPT.replace('__IDENTITY_READ__',IDENTITY_READ)


class Driver:
    def __init__(self,adapter):
        self.adapter=adapter
        self.cancel=None;self.authorized=False;self.deadline=time.monotonic()+60

    def _call(self,action,before=None,after=None):
        remaining=min(25,self.deadline-time.monotonic())
        if remaining<=0:raise TimeoutError('Static network operation timed out')
        row=self.adapter
        request=dict(action=action,expires=int(time.time()+remaining),id=str(uuid.UUID(row['id'])),index=int(row['index']),
                     mac=''.join(c for c in row['mac'] if c.isalnum()).upper(),before=before,after=after)
        encoded=base64.b64encode(json.dumps(request).encode('utf-8')).decode('ascii')
        with tempfile.TemporaryDirectory(prefix='router-static-') as folder:
            output=Path(folder)/'result.json'
            script=SCRIPT.replace('__REQUEST__',"([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('"+encoded+"')) | ConvertFrom-Json)")
            script=script.replace('__OUTPUT__',str(output).replace("'","''"))
            exe,args=powershell(script)
            code=self._execute(exe,args,remaining)
            if code!=0:
                emit('static_prepare','address_conflict' if code==21 else 'adapter_changed' if code in {22,23} else 'system_error',native_error=code)
                messages={25:'\u7cfb\u7edf\u672a\u542f\u7528 IP \u51b2\u7a81\u68c0\u6d4b\uff0c\u672a\u81ea\u52a8\u4fee\u6539\u7535\u8111\u6216\u8def\u7531\u5668\u3002',21:'\u65b0\u7535\u8111 IP \u5df2\u88ab\u5360\u7528\uff0c\u5df2\u505c\u6b62\u5e76\u5c1d\u8bd5\u6062\u590d\u7535\u8111\u8bbe\u7f6e\u3002',
                          22:'\u7f51\u5361\u6216\u56fa\u5b9a IP \u6a21\u5f0f\u5df2\u53d8\u5316\uff0c\u5df2\u505c\u6b62\u3002',
                          23:'\u7535\u8111\u7f51\u7edc\u5df2\u88ab\u5176\u4ed6\u64cd\u4f5c\u4fee\u6539\uff0c\u672a\u8986\u76d6\u3002',
                          24:'\u65b0\u7535\u8111 IP \u51b2\u7a81\u68c0\u6d4b\u672a\u5b8c\u6210\uff0c\u672a\u4fee\u6539\u8def\u7531\u5668\u3002'}
                raise StaticMigrationError(messages.get(code,'\u7535\u8111\u56fa\u5b9a IP \u8c03\u6574\u5931\u8d25\uff0c\u8bf7\u68c0\u67e5\u7ba1\u7406\u5458\u6388\u6743\u548c\u7f51\u5361\u72b6\u6001\u3002'))
            with output.open('r',encoding='utf-8-sig') as stream:state=json.load(stream)
            return state

    def _execute(self,exe,args,timeout):
        from .windows_cancel_wait import CancelSignal
        started=time.monotonic()
        with CancelSignal(self.cancel) as signal:
            if self.cancel is not None and self.cancel.is_set():raise InterruptedError('Static network operation cancelled')
            emit('static_command',timeout_seconds=timeout)
            if not self.authorized:
                process=subprocess.Popen([exe,*args],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL,creationflags=subprocess_creationflags())
                try:code=signal.wait(process,timeout)
                finally:
                    if process.poll() is None:process.kill();process.wait(timeout=5)
            else:code=5
            if self.cancel is not None and self.cancel.is_set():raise InterruptedError('Static network operation cancelled')
            if code==5:
                remaining=min(timeout-(time.monotonic()-started),self.deadline-time.monotonic())
                if remaining<=0:raise TimeoutError('Static network authorization expired')
                from .update_elevation import run_elevated_update
                accepted,code=run_elevated_update(exe,subprocess.list2cmdline(args),
                    timeout_ms=max(1,int(remaining*1000)),show_window=False,cancel_event=signal.name)
                if code==1223 or (self.cancel is not None and self.cancel.is_set()):raise InterruptedError('Static network operation cancelled')
                if not accepted:raise StaticMigrationError('Static network administrator authorization failed')
                self.authorized=True
            emit('static_command',elapsed_seconds=time.monotonic()-started,system_exit_code=code)
            return code

    def read(self):
        if self.cancel is not None and self.cancel.is_set():raise InterruptedError('Static network operation cancelled')
        from .static_network_windows_read import read
        started=time.monotonic();state=read(self.adapter)
        emit('static_inspect','system_accepted',elapsed_seconds=time.monotonic()-started)
        return state
    def write(self,before,after):return self._call('write',before,after)
