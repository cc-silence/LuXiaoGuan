"""System request timeouts still use the remaining address-observation budget."""
from types import SimpleNamespace as NS
from unittest.mock import patch
import subprocess
import unittest
from modules.platform_services import lan_network as facade
from modules.platform_services import lan_network_windows as windows
from modules.platform_services import lan_network_linux as linux
from tests.network_readiness_fixture import fast_address_wait
from tests.test_lan_client_recovery import adapter


class RecoveryTimeoutTests(unittest.TestCase):
    def windows_timeout(self, ready_after=None):
        old=adapter(wireless=True,expected_addresses=['192.168.22.1/24'])
        waiting=adapter(wireless=True,connected=False,addresses=[])
        ready=adapter(wireless=True,addresses=['192.168.22.23/24'])
        with fast_address_wait() as clock:
            def expired(argv, **kwargs):
                clock[0]+=kwargs['timeout']
                raise subprocess.TimeoutExpired('fake-cim',kwargs['timeout'])
            def inspect(**kwargs):
                return dict(adapters=[ready if ready_after is not None and clock[0]>=100+ready_after else waiting],error='')
            with patch.object(facade,'current_os',return_value=facade.WINDOWS), patch.object(
                    windows.subprocess,'run',side_effect=expired) as execute, patch.object(facade,'inspect',side_effect=inspect) as read:
                try:result=facade.renew(old,60)
                except Exception as exc:result=exc
            return result,clock[0]-100,execute.call_count,read.call_count

    def test_windows_timeout_can_be_followed_by_success_in_reserved_budget(self):
        result,elapsed,calls,reads=self.windows_timeout(59)
        self.assertIsInstance(result,dict)
        self.assertEqual(elapsed,59)
        self.assertEqual(calls,1)
        self.assertGreater(reads,1)

    def test_windows_timeout_without_readiness_uses_whole_budget(self):
        result,elapsed,calls,_=self.windows_timeout()
        self.assertIsInstance(result,RuntimeError)
        self.assertEqual(elapsed,60)
        self.assertEqual(calls,1)
        self.assertTrue(result.network_retryable)
        self.assertEqual(result.network_reason,'lease_timeout')

    def test_linux_request_timeout_keeps_observing_same_adapter(self):
        for exception in (TimeoutError('pending'),subprocess.TimeoutExpired('fake-nmcli',57)):
            with self.subTest(exception=type(exception).__name__), fast_address_wait() as clock:
                old=adapter(wireless=True,expected_addresses=['192.168.22.1/24'])
                ready=adapter(wireless=True,addresses=['192.168.22.23/24'])
                def command(args,timeout):
                    clock[0]+=timeout
                    raise exception
                def inspect(**kwargs):
                    return dict(adapters=[ready if clock[0]>=159 else adapter(connected=False,addresses=[])],error='')
                with patch.object(facade,'current_os',return_value=facade.LINUX), patch.object(
                        linux,'inspect',return_value=[old]), patch.object(linux,'command',side_effect=command) as execute, patch.object(
                        facade,'inspect',side_effect=inspect):
                    result=facade.renew(old,60)
                self.assertEqual(result,ready)
                self.assertEqual(clock[0],159)
                execute.assert_called_once()

    def test_linux_activation_failure_does_not_wait_or_repeat(self):
        failure=RuntimeError('activation rejected');failure.system_exit_code=4
        with fast_address_wait() as clock, patch.object(facade,'current_os',return_value=facade.LINUX), patch.object(
                linux,'inspect',return_value=[adapter()]), patch.object(linux,'command',side_effect=failure) as execute, patch.object(
                facade,'inspect',return_value=dict(adapters=[adapter()],error='')):
            with self.assertRaises(RuntimeError) as raised:facade.renew(adapter(),60)
        self.assertIs(raised.exception,failure)
        self.assertEqual(clock[0],100)
        self.assertFalse(failure.network_retryable)
        execute.assert_called_once()
