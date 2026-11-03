"""Initialization must use the shared verified DHCP transition, without clicks."""
import unittest
from unittest.mock import Mock,patch
from modules.capture_services.configuration_progress import reporting
from modules.platform_services import lan_network as facade,lan_network_windows as windows
from tests.test_router_policy import PolicyDevice,LuaRuntime,environment
from modules.capture_services.openwrt.router_policy_plan import build_plan
from tests.test_lan_client_recovery import adapter


@unittest.skipUnless(LuaRuntime,'Lua runtime unavailable')
class InitialLanRecoveryIntegrationTests(unittest.TestCase):
    def check_initialization(self,items):
        device=PolicyDevice();policy=device.policy
        snapshot=policy.read(device.cancel)
        plan=build_plan(snapshot,'initial',dict(items=items,lan_ip='192.168.22.1',ap_ssid='Test_AP'),environment())
        plan['signature']=snapshot['signature']
        before=adapter(addresses=['192.168.8.123/24'],gateways=['192.168.8.1'],
                       dns=['192.168.8.1'],dhcp_server='192.168.8.1')
        ready=dict(before,addresses=['192.168.22.123/24'],gateways=['192.168.22.1'],
                   dns=['192.168.22.1'],dhcp_server='192.168.22.1')
        plan['client_network']=dict(adapters=[before],peer='192.168.8.1',selection_source='ssh_socket')
        automatic=[]
        with patch.object(facade,'current_os',return_value=facade.WINDOWS), \
             patch.object(windows,'renew') as request, \
             patch.object(facade,'inspect',side_effect=lambda **kw:dict(adapters=[before if request.call_count<2 else ready],error='')):
            def on_progress(value):
                payload=value.get('network_recovery')
                if not isinstance(payload,dict):return
                gate=payload['control'];identity=gate.take_automatic()
                if identity is None:return
                automatic.append(identity)
                selected,timeout=gate.claim(identity)
                self.assertEqual(selected['expected_addresses'],['192.168.22.1/24'])
                current=facade.renew(selected,timeout,gate.permit)
                gate.finish_renewal('verified',current)
            with reporting(on_progress):
                result=policy.apply(plan,None,None,device.cancel,Mock())
        self.assertEqual(automatic,[before['id']])
        self.assertEqual(request.call_count,2)
        self.assertEqual(result['outcome'],'applied')
        self.assertEqual(device.config['network']['lan']['ipaddr'],'192.168.22.1')

    def test_initial_lan_uses_common_stale_lease_correction(self):
        self.check_initialization(['lan'])

    def test_initial_hotspot_and_lan_use_common_stale_lease_correction(self):
        self.check_initialization(['replace_ap','lan'])


if __name__=='__main__':unittest.main()
