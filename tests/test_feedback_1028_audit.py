"""Compatibility audit edge cases; all network changes are mocked."""
import copy
import base64
import os
import subprocess
import time
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch,Mock
from tests.test_lan_client_recovery import adapter,review,NEW
from tests.test_factory_reset_recovery import RecoveryOwner
from modules.capture_services.lan_client_recovery import RenewalGate,client_plan,factory_reset_plan
from modules.capture_services.lan_recovery_service import LanRecoveryService
from modules.platform_services import lan_network as facade


class RecoveryBaselineTests(unittest.TestCase):
    def test_successful_renewal_never_changes_original_rollback_instructions(self):
        plan=client_plan(review(adapter()),NEW);before=copy.deepcopy(plan)
        gate=RenewalGate(plan);gate.start(time.monotonic()+90);gate.arm()
        gate.claim(adapter()['id'])
        fresh=adapter(addresses=['192.168.22.123/24'],gateways=['192.168.22.1'],dns=['192.168.22.1'])
        gate.finish_renewal('done',fresh)
        self.assertEqual(gate.payload()['adapters'][0]['addresses'],fresh['addresses'])
        self.assertEqual(gate.plan,before)
        restored=gate.start(time.monotonic()+30,restoring=True)
        self.assertIn('192.168.11.123/24',restored['guide'])
        self.assertIn('192.168.11.1',restored['guide'])
        self.assertNotIn('192.168.22.123/24',restored['guide'])
        gate.arm()  # Explicit permission belongs to the completed-result offer.
        selected,_=gate.claim(adapter()['id'])
        self.assertEqual(selected['expected_addresses'],['192.168.11.1/24'])

    def test_long_router_boot_gets_a_fresh_manual_recovery_window(self):
        owner=RecoveryOwner(NS(),NS());self.addCleanup(owner.deleteLater)
        broker=LanRecoveryService(owner)
        key=broker.create_offer(factory_reset_plan(dict(adapters=[adapter()])),ready=False)
        gate=broker.offers[key][0];gate.deadline=time.monotonic()-1
        self.assertFalse(broker.payload(key)['can_renew'])
        broker.set_ready(key,True)
        self.assertTrue(broker.payload(key)['can_renew'])
        self.assertGreater(gate.deadline-time.monotonic(),590)
        broker.release(key)

    def test_recovery_readiness_cannot_be_revoked_while_renewal_is_running(self):
        owner=RecoveryOwner(NS(),NS());self.addCleanup(owner.deleteLater)
        broker=LanRecoveryService(owner)
        key=broker.create_offer(factory_reset_plan(dict(adapters=[adapter()])))
        gate=broker.offers[key][0];gate.claim(adapter()['id'])
        broker.set_ready(key,False)
        self.assertTrue(gate.permit());self.assertTrue(gate.busy)
        gate.finish_renewal('done');broker.release(key)


class AddressReadbackTests(unittest.TestCase):
    def test_disconnect_or_manual_static_change_during_renewal_is_not_success(self):
        for current in (adapter(connected=False),adapter(dhcp=False),adapter(addresses=['224.0.0.2/24'])):
            with self.subTest(current=current),patch.object(facade,'current_os',return_value=facade.WINDOWS),patch('modules.platform_services.lan_network_windows.renew'),patch.object(facade,'inspect',return_value=dict(adapters=[current])):
                with self.assertRaises(RuntimeError):facade.renew(adapter(),30)

    def test_linux_missing_manager_is_explicit_and_has_no_side_effect(self):
        from modules.platform_services import lan_network_linux as linux
        with patch.object(facade,'current_os',return_value=facade.LINUX),patch.object(linux,'renew') as renew:
            with self.assertRaises(RuntimeError):facade.renew(adapter(renewable=False),30)
        renew.assert_not_called()


@unittest.skipUnless(os.name=='nt','Windows PowerShell execution only')
class WindowsLeaseContractTests(unittest.TestCase):
    def test_actual_powershell_flow_with_fake_cim_never_calls_real_adapter(self):
        from modules.platform_services import lan_network_windows as windows
        for released,renewed,expected,renew_called in ((0,0,0,True),(1,0,15,True),(0,1,15,True),(91,0,5,False),(64,0,16,False),(83,0,14,False)):
            with self.subTest(released=released,renewed=renewed):
                with patch.object(windows.subprocess,'run',return_value=Mock(returncode=0)) as run:
                    windows.renew(adapter(),30,lambda:True)
                source=base64.b64decode(run.call_args.args[0][-1]).decode('utf-16-le')
                fake="""
function Get-CimInstance {
 param($ClassName,$Filter)
 [pscustomobject]@{DHCPEnabled=$true;InterfaceIndex=14;MACAddress='00:50:56:2B:29:2D'}
}
function Invoke-CimMethod {
 param($InputObject,$MethodName)
 [Console]::WriteLine('METHOD:'+$MethodName)
 if($MethodName -eq 'ReleaseDHCPLease'){[pscustomobject]@{ReturnValue=__RELEASE__}}
 elseif($MethodName -eq 'RenewDHCPLease'){[pscustomobject]@{ReturnValue=__RENEW__}}
 else {throw 'Unexpected method'}
}
""".replace('__RELEASE__',str(released)).replace('__RENEW__',str(renewed))
                exe,args=windows.powershell(fake+source)
                result=subprocess.run([exe,*args],stdin=subprocess.DEVNULL,capture_output=True,
                    timeout=12,creationflags=windows.subprocess_creationflags())
                self.assertEqual(result.returncode,expected,result.stderr.decode('utf-8',errors='replace'))
                self.assertIn(b'METHOD:ReleaseDHCPLease',result.stdout)
                self.assertEqual(b'METHOD:RenewDHCPLease' in result.stdout,renew_called)

    def test_windows_special_statuses_are_not_reported_as_success(self):
        from modules.platform_services import lan_network_windows as windows
        for code in (15,16):
            with self.subTest(code=code),patch.object(windows.subprocess,'run',return_value=Mock(returncode=code)):
                with self.assertRaises(RuntimeError):windows.renew(adapter(),30,lambda:True)


class HomepageServerCompatibilityTests(unittest.TestCase):
    def test_custom_index_order_is_reported_instead_of_ineffective_success(self):
        from tests.test_router_policy import PolicyDevice
        d=PolicyDevice();d.config['uhttpd']['main']['index_page']=['landing.htm','index.html']
        before=copy.deepcopy(d.files)
        review=d.policy.preview('homepage',dict(homepage='wireless'),d.cancel)
        self.assertFalse(review['files']);self.assertTrue(review['skipped'])
        with self.assertRaises(ValueError):d.policy.apply(review,None,None,d.cancel,Mock())
        self.assertEqual(d.files,before)

    def test_standard_index_with_trailing_root_slash_is_supported(self):
        from tests.test_router_policy import PolicyDevice
        d=PolicyDevice();d.config['uhttpd']['main'].update(home='/www/',index_page=['index.html','index.htm'])
        review=d.policy.preview('homepage',dict(homepage='wireless'),d.cancel)
        self.assertFalse(review['skipped']);self.assertEqual(len(review['files']),1)
        result=d.policy.apply(review,None,None,d.cancel,Mock())
        self.assertTrue(result['applied'])


class HomepageFailurePresentationTests(unittest.TestCase):
    from tests.common_settings_fixture import create_page

    def test_unsupported_custom_homepage_reports_the_reason_without_apply(self):
        page,_=self.create_page();actions=page.policy_actions
        page.manager.manager.runner=NS()
        actions.pending['preview']=('preview','homepage',actions._identity())
        result=dict(kind='homepage',files=[],skipped=['Custom index order prevents this change'])
        with patch('modules.main_pages.packet_capture.openwrt.router_policy_actions.MessageDialog') as dialog:
            actions.completed('preview',result)
        page.manager.submit.assert_not_called()
        self.assertIn('Custom index order',dialog.call_args.args[1])
