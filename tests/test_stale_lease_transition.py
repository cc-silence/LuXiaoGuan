"""A completed old lease needs a bounded new request, not passive waiting."""
import unittest
from unittest.mock import patch
from modules.platform_services import lan_network as facade,lan_network_windows as windows
from modules.platform_services.lease_transition import retained_old_lease
from modules.platform_services.network_diagnostics import observing
from tests.network_readiness_fixture import fast_address_wait
from tests.test_lan_client_recovery import adapter


class StaleLeaseTests(unittest.TestCase):
    def before(self,**changes):
        row=adapter(dhcp_server='192.168.11.1',gateways=['192.168.11.1'],
                    expected_addresses=['192.168.22.1/24'])
        row.update(changes)
        return row

    def run_case(self,*,stays_old=False,error=None,budget=30):
        before=self.before();records=[]
        ready=self.before(addresses=['192.168.22.23/24'],gateways=['192.168.22.1'],dhcp_server='192.168.22.1')
        with fast_address_wait() as clock, patch.object(facade,'current_os',return_value=facade.WINDOWS), patch.object(windows,'renew',side_effect=error) as request:
            def read(**kwargs):
                return dict(adapters=[before if stays_old or request.call_count<2 else ready],error='')
            with patch.object(facade,'inspect',side_effect=read),observing(records.append):
                try:result=facade.renew(before,budget)
                except Exception as exc:result=exc
        return result,request,records,clock[0]-100

    def test_completed_old_lease_reacquires_then_verifies_target(self):
        result,request,records,elapsed=self.run_case()
        self.assertIsInstance(result,dict)
        self.assertEqual(result['dhcp_server'],'192.168.22.1')
        self.assertEqual(request.call_count,2)
        self.assertEqual(sum(r['reason']=='old_lease_retained' for r in records),1)
        self.assertEqual(records[-1]['reason'],'address_ready')
        self.assertEqual(elapsed,0)

    def test_persistent_old_server_does_not_loop_or_reset_deadline(self):
        result,request,_,elapsed=self.run_case(stays_old=True)
        self.assertIsInstance(result,TimeoutError)
        self.assertEqual(request.call_count,2)
        self.assertEqual(elapsed,30)

    def test_failed_request_is_observed_without_replay(self):
        error=RuntimeError('Pending request');error.network_retryable=True
        result,request,_,elapsed=self.run_case(stays_old=True,error=error)
        self.assertIs(result,error);self.assertEqual(request.call_count,1);self.assertEqual(elapsed,30)

    def test_too_little_time_does_not_start_correction(self):
        result,request,_,_=self.run_case(stays_old=True,budget=8)
        self.assertIsInstance(result,TimeoutError);self.assertEqual(request.call_count,1)

    def test_incomplete_or_changed_evidence_cannot_trigger_correction(self):
        before=self.before()
        for changes in (dict(dhcp_server=''),dict(dhcp_server='192.168.11.2'),dict(dhcp=False),
                        dict(connected=False),dict(mac='other'),dict(id='other'),
                        dict(addresses=[]),dict(addresses=['169.254.1.1/16']),
                        dict(addresses=['192.168.22.23/24']),dict(gateways=[])):
            self.assertFalse(retained_old_lease(before,dict(before,**changes),before['expected_addresses']),changes)
        self.assertTrue(retained_old_lease(before,before,before['expected_addresses']))
        self.assertFalse(retained_old_lease(before,before,[]))

    def test_second_request_cancellation_is_not_swallowed(self):
        result,request,_,_=self.run_case(error=[None,InterruptedError('Cancelled')])
        self.assertIsInstance(result,InterruptedError);self.assertEqual(request.call_count,2)
