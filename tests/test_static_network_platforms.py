"""Platform contracts: real PowerShell with fake cmdlets, simulated NetworkManager."""
import base64
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch
from tests.test_lan_client_recovery import adapter
from modules.platform_services.static_network import StaticMigrationError
from modules.platform_services import static_network_windows as windows
from modules.platform_services import static_network_linux as linux

FAKE=r"""
$script:ips=@('192.168.11.123/24');$script:gates=@('192.168.11.1');$script:dns=@('192.168.11.1','1.1.1.1')
function Get-NetAdapter {param($InterfaceIndex) [pscustomobject]@{InterfaceGuid=[guid]'7862e3c3-2780-4f50-98c7-8d29dc0cef7a';MacAddress='00-50-56-2B-29-2D';Status='Up'}}
function Get-NetIPInterface {param($InterfaceIndex,$AddressFamily) [pscustomobject]@{Dhcp='Disabled';DadTransmits=__DAD_COUNT__}}
function Get-NetIPAddress {
 param($InterfaceIndex,$IPAddress,$AddressFamily)
 foreach($item in $script:ips){$parts=$item.Split('/');if(!$IPAddress -or $IPAddress -eq $parts[0]){[pscustomobject]@{IPAddress=$parts[0];PrefixLength=[int]$parts[1];PrefixOrigin='Manual';AddressState='__DAD__'}}}
}
function Get-NetRoute {param($InterfaceIndex,$AddressFamily,$DestinationPrefix) foreach($item in $script:gates){[pscustomobject]@{NextHop=$item;RouteMetric=7;DestinationPrefix='0.0.0.0/0'}}}
function Get-DnsClientServerAddress {param($InterfaceIndex,$AddressFamily) [pscustomobject]@{ServerAddresses=$script:dns}}
function New-NetIPAddress {param($InterfaceIndex,$IPAddress,$PrefixLength) if(@($script:ips | Where-Object {$_.Split('/')[0] -eq $IPAddress}).Count){throw 'IP already exists'}; $script:ips+=($IPAddress+'/'+$PrefixLength)}
function Set-NetIPAddress {param($InterfaceIndex,$IPAddress,$PrefixLength) $script:ips=@($script:ips | ForEach-Object {if($_.Split('/')[0] -eq $IPAddress){$IPAddress+'/'+$PrefixLength}else{$_}})}
function Remove-NetIPAddress {param($InterfaceIndex,$IPAddress,$Confirm) $script:ips=@($script:ips | Where-Object {$_.Split('/')[0] -ne $IPAddress})}
function New-NetRoute {param($InterfaceIndex,$DestinationPrefix,$NextHop,$RouteMetric) $script:gates+=$NextHop}
function Remove-NetRoute {param($InterfaceIndex,$DestinationPrefix,$NextHop,$Confirm) $script:gates=@($script:gates | Where-Object {$_ -ne $NextHop})}
function Set-DnsClientServerAddress {param([Parameter(ValueFromPipeline=$true)]$InputObject,$ServerAddresses) process {$script:dns=@($ServerAddresses)}}
"""


@unittest.skipUnless(os.name=='nt','Windows PowerShell required')
class PowerShellTests(unittest.TestCase):
    def execute(self,duplicate=False,stale=False,dad_enabled=True,route_error=False,mask_change=False):
        before=dict(addresses=['192.168.11.123/24'],gateways=['192.168.11.1'],dns=['192.168.11.1','1.1.1.1'],metric=7)
        after=dict(addresses=['192.168.22.123/24'],gateways=['192.168.22.1'],dns=['192.168.22.1','1.1.1.1'],metric=7)
        if mask_change:after=dict(after,addresses=['192.168.11.123/25'],gateways=['192.168.11.2'],dns=['192.168.11.2','1.1.1.1'])
        if stale:before['dns']=['8.8.8.8']
        request=dict(action='write',expires=int(time.time()+30),id=adapter()['id'],index=14,mac='0050562B292D',before=before,after=after)
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/'result.json'
            encoded=base64.b64encode(json.dumps(request).encode()).decode()
            source=windows.SCRIPT.replace(windows.IDENTITY_READ," $nic=Get-NetAdapter -InterfaceIndex $req.index; if($nic.InterfaceGuid.ToString() -ne $req.id){throw 'Adapter identity or static mode changed'}").replace('__REQUEST__',"([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('"+encoded+"')) | ConvertFrom-Json)").replace('__OUTPUT__',str(output).replace("'","''"))
            fake=FAKE.replace('__DAD__','Duplicate' if duplicate else 'Preferred').replace('__DAD_COUNT__','1' if dad_enabled else '0')
            if route_error:fake+="function Get-NetRoute {throw 'Route read failed'}\n"
            exe,args=windows.powershell(fake+source)
            result=subprocess.run([exe,*args],capture_output=True,timeout=15,creationflags=windows.subprocess_creationflags())
            return result,json.loads(output.read_text(encoding='utf-8-sig')) if output.exists() else None,after
    def test_real_script_replaces_only_requested_ipv4_fields(self):
        result,state,wanted=self.execute()
        self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'))
        self.assertEqual(state,wanted)
    def test_real_script_rejects_duplicate_address(self):
        result,state,_=self.execute(duplicate=True)
        self.assertEqual(result.returncode,21);self.assertIsNone(state)
    def test_mask_update_does_not_readd_or_delete_the_existing_ip(self):
        result,state,wanted=self.execute(mask_change=True,dad_enabled=False)
        self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'))
        self.assertEqual(state,wanted)
    def test_disabled_system_conflict_detection_is_not_accepted(self):
        result,state,_=self.execute(dad_enabled=False)
        self.assertEqual(result.returncode,25);self.assertIsNone(state)
    def test_route_read_error_is_not_treated_as_an_empty_gateway(self):
        result,state,_=self.execute(route_error=True)
        self.assertEqual(result.returncode,20);self.assertIsNone(state)
    def test_real_script_rejects_concurrent_edit(self):
        result,state,_=self.execute(stale=True)
        self.assertEqual(result.returncode,23);self.assertIsNone(state)


class NetworkManagerTests(unittest.TestCase):
    def test_unmanaged_adapter_is_rejected_before_any_command(self):
        with patch.object(linux,'command') as run:
            with self.assertRaises(StaticMigrationError):linux.Driver(adapter(manager='unmanaged'))
        run.assert_not_called()
    def test_missing_collision_detector_stops_before_any_command(self):
        with patch.object(linux.shutil,'which',return_value=None),patch.object(linux,'command') as run:
            with self.assertRaises(StaticMigrationError):linux.Driver(adapter(manager='networkmanager'))
        run.assert_not_called()
    def test_reapply_updates_profile_and_live_state_without_link_cycle(self):
        before=dict(addresses=['192.168.11.123/24'],gateways=['192.168.11.1'],dns=['192.168.11.1'],metric=-1)
        after=dict(before,addresses=['192.168.11.123/24','192.168.22.123/24'])
        profile=dict(before);live=dict(before);calls=[]
        row=adapter(manager='networkmanager',dhcp=False)
        def inspect(**kwargs):return [dict(row,addresses=list(reversed(live['addresses'])),gateways=live['gateways'],dns=live['dns'])]
        def command(args,timeout):
            calls.append(args)
            if args[0]=='arping':return 'arping from iputils' if '-V' in args else ''
            if args[:2]==['nmcli','-g']:
                field=args[2]
                return {'ipv4.method':'manual','ipv4.addresses':','.join(profile['addresses']),
                        'ipv4.gateway':','.join(profile['gateways']),'ipv4.dns':','.join(profile['dns']),
                        'ipv4.route-metric':str(profile['metric'])}[field]
            if 'modify' in args:
                for name,key in [('ipv4.addresses','addresses'),('ipv4.gateway','gateways'),('ipv4.dns','dns')]:
                    profile[key]=[v for v in args[args.index(name)+1].split(',') if v]
                return ''
            if 'reapply' in args:live.update(profile);return ''
            raise AssertionError(args)
        with patch.object(linux.shutil,'which',return_value='/usr/bin/arping'),patch.object(linux,'command',side_effect=command),patch.object(linux,'inspect',side_effect=inspect):
            driver=linux.Driver(row);driver.write(before,after);self.assertEqual(driver.read(),after)
            driver.write(after,before);self.assertEqual(driver.read(),before)
        self.assertTrue(any(args[0]=='arping' for args in calls))
        self.assertTrue(any('reapply' in args for args in calls))
        self.assertFalse(any('down' in args or 'up' in args or 'disconnect' in args for args in calls))


if __name__=='__main__':unittest.main()
