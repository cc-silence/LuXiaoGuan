"""Native Lua regression: an AP can run without a hostapd ubus object."""
import json
import threading
import unittest
from tests.test_wireless_version_contracts import VersionDevice, PROFILES
from tests.test_wireless_setup import LuaRuntime
from modules.capture_services.openwrt.wireless_setup_manager import WirelessSetupManager

class KernelApDevice(VersionDevice):
    def __init__(self):
        super().__init__(next(p for p in PROFILES if p['series']=='23.05'))
        self.status_error=4
        self.kernel_ssid='Home'
        self.kernel_type='AP'
        self.kernel_frequency=5180
        self.kernel_carrier='1'
        self.kernel_flags='0x1003'
        self.kernel_ssid_present=True
        self.lua.globals().bridge_ap_file=lambda kind:self.kernel_flags if kind=='flags' else self.kernel_carrier
        self.lua.execute(r"""
require("uci").cursor().foreach=function(self,config,kind,callback) bridge_foreach(config,kind,callback);return true end
local original=io.open
io.open=function(path,mode)
 local kind=path:match('^/sys/class/net/iface%-home/(flags)$') or path:match('^/sys/class/net/iface%-home/(carrier)$')
 if not kind then return original(path,mode) end
 return {read=function() return bridge_ap_file(kind)..'\n' end,close=function() return true end}
end
""")
    def foreach(self,*args):
        super().foreach(*args)
        return True
    def read(self,command):
        if command.startswith("iw dev 'iface-home' info"):
            ssid=''.join(chr(b) if 33<=b<=126 and b!=92 else (' ' if b==32 and i not in (0,len(self.kernel_ssid.encode())-1) else '\\x%02x'%b) for i,b in enumerate(self.kernel_ssid.encode()))
            return 'Interface iface-home\n\twiphy 2\n'+(('\tssid '+ssid+'\n') if self.kernel_ssid_present else '')+'\ttype '+self.kernel_type+'\n\tchannel 36 ('+str(self.kernel_frequency)+' MHz), width: 80 MHz\n'
        return super().read(command)
    def status(self):
        return json.loads(self.execute(dict(action='status',radio='radio7',section='home',mode='ap')))['result']

@unittest.skipUnless(LuaRuntime,'Lua unavailable')
class KernelApReadinessTests(unittest.TestCase):
    def test_running_ap_without_control_object_is_ready(self):
        device=KernelApDevice()
        self.assertTrue(device.status()['ready'])

    def test_up_interface_without_kernel_start_is_not_ready(self):
        for field,value in (('kernel_ssid_present',False),('kernel_carrier','0'),('kernel_flags','0x1002'),('kernel_type','managed'),('kernel_frequency',0),('kernel_ssid','Another')):
            with self.subTest(field=field):
                device=KernelApDevice();setattr(device,field,value)
                self.assertFalse(device.status()['ready'])

    def test_explicit_disabled_dfs_permission_and_timeout_never_use_kernel_path(self):
        for error,status in ((None,'DFS'),(None,'DISABLED'),(6,'ENABLED'),(7,'ENABLED')):
            device=KernelApDevice();device.status_error=error;device.status_value=status
            self.assertFalse(device.status()['ready'])


    def test_parent_enable_commits_only_parent_and_child_without_control_object(self):
        import copy
        device=KernelApDevice()
        device.config['wireless']['radio7']['disabled']='1'
        device.config['wireless']['home']['disabled']='1'
        before=copy.deepcopy(device.config)
        manager=WirelessSetupManager(device)
        manager.toggle('home',True,threading.Event(),enable_parent=True,expected_revision=device.revision()[:64])
        before['wireless']['radio7']['disabled']='0';before['wireless']['home']['disabled']='0'
        self.assertEqual(device.config,before)
        self.assertIn(('uci','confirm'),device.calls)
        self.assertFalse(device.sessions)

    def test_nonrunning_ap_rolls_back_parent_and_child(self):
        import copy
        from unittest.mock import patch
        from modules.capture_services.openwrt.signal_manager import SignalError
        device=KernelApDevice();device.kernel_carrier='0'
        device.config['wireless']['radio7']['disabled']='1';device.config['wireless']['home']['disabled']='1'
        before=copy.deepcopy(device.config);manager=WirelessSetupManager(device)
        def check(request,section,cancel,*args,**kwargs):
            value=manager._call('status',cancel,radio=request.radio,section=section,mode='ap')
            if not value['ready']:raise SignalError('authentication','not ready')
        with patch.object(manager,'_wait_ready',side_effect=check):
            with self.assertRaises(SignalError):
                manager.toggle('home',True,threading.Event(),enable_parent=True,expected_revision=device.revision()[:64])
        self.assertEqual(device.config,before)
        self.assertIn(('uci','rollback'),device.calls)
        self.assertFalse(device.sessions)

    def test_escaped_ssid_bytes_and_literal_escape_are_compared_exactly(self):
        for name in (' Home ',r'Home\x20',chr(0x4e2d)+chr(0x6587)):
            device=KernelApDevice()
            device.kernel_ssid=name;device.config['wireless']['home']['ssid']=name
            self.assertTrue(device.status()['ready'])
        device=KernelApDevice();device.kernel_ssid='Other'
        self.assertFalse(device.status()['ready'])

    def test_inventory_and_transaction_share_the_same_verdict(self):
        from modules.capture_services.openwrt.signal_commands import signal_command
        for carrier,ready in (('1',True),('0',False)):
            device=KernelApDevice();device.kernel_carrier=carrier
            expected=device.status()['ready']
            command=signal_command('read');body=command.split("<<'WINBOX_SIGNAL_LUA'\n",1)[1].rsplit('\nWINBOX_SIGNAL_LUA',1)[0]
            device._output=[];device.lua.globals().arg=device.wrap({1:json.dumps(dict(action='read'))})
            device.lua.execute(body)
            data=json.loads(''.join(device._output))
            self.assertTrue(data['ok'],data)
            row=next(r for r in data['result']['rows'] if r['section']=='home')
            self.assertEqual((row['running']=='active',expected),(ready,ready))

    def test_export_records_proof_flags_without_raw_ssid(self):
        from modules.capture_services.openwrt.operation_diagnostics import OperationDiagnostics
        from modules.capture_services.openwrt.wireless_readiness_evidence import WirelessReadinessEvidence
        device=KernelApDevice();value=device.status();journal=OperationDiagnostics()
        with WirelessReadinessEvidence(journal,'ap').observing() as evidence:evidence.sample(value)
        final=journal.snapshot()['configuration'][-1]
        self.assertEqual((final['ap_control_available'],final['kernel_ap_started'],final['ap_carrier']),(False,True,True))
        self.assertNotIn('Home',json.dumps(final))
