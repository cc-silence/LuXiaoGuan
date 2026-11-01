"""Bind DHCP recovery to the actual SSH source; no real adapter mutations."""
from copy import deepcopy
import json
from types import SimpleNamespace as NS
import time
import unittest
from unittest.mock import Mock,patch
from modules.capture_services.lan_client_recovery import attach_inventory,client_plan,RenewalGate
from modules.platform_services import lan_network as facade
from modules.platform_services import lan_network_windows as windows
from modules.capture_services.openwrt.operation_diagnostics import OperationDiagnostics,sanitized_operation_evidence
from modules.display_privacy import masker_for
from tests.test_lan_client_recovery import adapter,review,NEW

class ActualLoginAdapterTests(unittest.TestCase):
    def inventory(self,rows,source='192.168.11.123',source_error=None):
        transport=NS(getpeername=Mock(return_value=('192.168.11.1',22)),
                     sock=NS(getsockname=Mock(return_value=(source,50000),side_effect=source_error)))
        ssh=NS(_host='192.168.11.1',_client=NS(get_transport=lambda:transport))
        with patch.object(facade,'current_os',return_value=facade.WINDOWS),patch.object(windows,'inspect',return_value=rows) as read:
            result=attach_inventory(review(),ssh)
        return result,read

    def gate(self,result):
        plan=client_plan(result,NEW);gate=RenewalGate(plan)
        gate.start(time.monotonic()+90);gate.arm(automatic=True)
        return gate

    def test_four_adapters_follow_actual_login_even_if_route_estimate_disagrees(self):
        rows=[adapter(id='ethernet-other',addresses=['192.168.11.55/24'],route=True),
              adapter(id='actual',route=False),
              adapter(id='wifi1',addresses=['10.0.0.23/24'],route=False),
              adapter(id='wifi2',addresses=['172.16.0.23/24'],route=False)]
        before=deepcopy(rows);result,read=self.inventory(rows);gate=self.gate(result)
        self.assertEqual(gate.take_automatic(),'actual')
        self.assertEqual(gate.plan['selection_source'],'ssh_socket')
        self.assertEqual(rows,before)
        self.assertIsNone(read.call_args.args[0])

    def test_same_source_address_on_two_adapters_is_ambiguous_and_never_automatic(self):
        result,_=self.inventory([adapter(id='one'),adapter(id='two',mac='00:00:00:00:00:02')])
        gate=self.gate(result)
        self.assertTrue(gate.plan['selection_required'])
        self.assertFalse(gate.plan['automatic_allowed'])
        self.assertIsNone(gate.take_automatic())

    def test_unreadable_login_socket_never_trusts_estimated_route_for_automatic_renewal(self):
        result,_=self.inventory([adapter()],source_error=OSError('socket closed'))
        gate=self.gate(result)
        self.assertEqual(gate.plan['selection_source'],'system_route')
        self.assertFalse(gate.plan['automatic_allowed'])
        self.assertIsNone(gate.take_automatic())
        self.assertIsNotNone(gate.claim(adapter()['id']))

    def test_proxy_socket_without_local_endpoint_requires_manual_recovery(self):
        result,_=self.inventory([adapter()],source_error=NotImplementedError('socket-like proxy'))
        self.assertIsNone(self.gate(result).take_automatic())

    def test_missing_source_owner_does_not_fall_back_to_another_adapter(self):
        result,_=self.inventory([adapter(addresses=['192.168.11.55/24'],route=True)])
        self.assertIsNone(self.gate(result).take_automatic())

    def test_invalid_or_ipv6_source_does_not_claim_ipv4_socket_proof(self):
        for source in ('::1','invalid'):
            with self.subTest(source=source):
                result,_=self.inventory([adapter()],source=source)
                gate=self.gate(result)
                self.assertEqual(gate.plan['selection_source'],'system_route')
                self.assertIsNone(gate.take_automatic())

    def test_binding_diagnostics_retain_only_source_kind_and_counts(self):
        journal=OperationDiagnostics()
        journal.record('configuration',operation='configuration_apply',stage='network_plan',
                       selection_source='ssh_socket',route_candidates=2,automatic_allowed=False,
                       local_address='192.168.11.123',adapter_name='private adapter')
        result=sanitized_operation_evidence(journal.snapshot(),masker_for([]))
        row=result['configuration'][0]
        self.assertEqual(row['selection_source'],'ssh_socket');self.assertEqual(row['route_candidates'],2)
        self.assertNotIn('192.168.11.123',json.dumps(result));self.assertNotIn('private adapter',json.dumps(result))

if __name__=='__main__':unittest.main()
