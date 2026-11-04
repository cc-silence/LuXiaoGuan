"""Real nmcli output shape and shared stale-lease logic, using fake commands only."""
import json
import unittest
from unittest.mock import patch
from modules.platform_services import lan_network_linux as linux, lan_network as facade
from modules.platform_services.lease_transition import retained_old_lease
from tests.test_lan_client_recovery import adapter
from tests.network_readiness_fixture import fast_address_wait


class LinuxDhcpEvidenceTests(unittest.TestCase):
    def test_invalid_missing_and_ambiguous_server_options_stay_unknown(self):
        for value in ('', 'domain_name_servers = 192.0.2.1',
                      'dhcp_server_identifier = ::1', 'dhcp_server_identifier = 0.0.0.0',
                      'dhcp_server_identifier = 127.0.0.1', 'dhcp_server_identifier = 224.0.0.1',
                      'dhcp_server_identifier = not-an-address',
                      'dhcp_server_identifier = 192.0.2.1\ndhcp_server_identifier = 192.0.2.2',
                      'x' * 65537):
            with self.subTest(value=value[:80]):
                self.assertEqual(linux.dhcp_server_identifier(value), '')
        self.assertEqual(linux.dhcp_server_identifier('dhcp_server_identifier = 192.0.2.1\nrouters = 192.0.2.2'), '192.0.2.1')

    def snapshot(self, options, *, dhcp=True):
        def command(args, timeout):
            if args[:4] == ['ip', '-j', '-4', 'address']:
                return json.dumps([dict(ifindex=424242, ifname='fixture0', address='00:11:22:33:44:55', flags=['UP'], addr_info=[dict(family='inet', local='192.168.11.123', prefixlen=24)])])
            if args[:4] == ['ip', '-j', '-4', 'route']:
                return '[{"dev":"fixture0"}]'
            field = args[2]
            if field == 'DHCP4.OPTION':
                if isinstance(options, Exception):raise options
                return options
            return {'GENERAL.CON-UUID':'fixture-profile', 'ipv4.method':'auto' if dhcp else 'manual', 'IP4.GATEWAY':'192.168.11.1', 'IP4.DNS':'192.168.11.1'}[field]
        with patch.object(linux, 'command', side_effect=command) as execute, patch.object(linux.shutil, 'which', return_value='/fixture/nmcli'), patch('modules.platform_services.network_environment.linux', return_value={}):
            rows = linux.inspect('192.168.11.1')
        return rows[0], execute

    def test_inspection_supplies_evidence_for_actual_shared_lease_predicate(self):
        row, execute = self.snapshot('dhcp_lease_time = 3600\ndhcp_server_identifier = 192.168.11.1')
        self.assertEqual(row['dhcp_server'], '192.168.11.1')
        self.assertTrue(retained_old_lease(row, dict(row), ['192.168.22.1/24']))
        self.assertFalse(retained_old_lease(row, dict(row, dhcp_server='192.168.22.1'), ['192.168.22.1/24']))
        self.assertTrue(all('connection' not in call.args[0] or 'show' in call.args[0] for call in execute.call_args_list))

    def test_optional_metadata_failure_keeps_adapter_and_static_connections_do_not_read_it(self):
        row, _ = self.snapshot(RuntimeError('fixture missing optional field'))
        self.assertEqual(row['connection'], 'fixture-profile')
        self.assertEqual(row['dhcp_server'], '')
        row, execute = self.snapshot(AssertionError('must not read DHCP for static profile'), dhcp=False)
        self.assertFalse(row['dhcp'])
        self.assertFalse(any('DHCP4.OPTION' in call.args[0] for call in execute.call_args_list))

    def test_linux_recovery_reacquires_completed_stale_lease_once_then_verifies(self):
        old = adapter(dhcp_server='192.168.11.1', expected_addresses=['192.168.22.1/24'])
        ready = adapter(dhcp_server='192.168.22.1', addresses=['192.168.22.123/24'], gateways=['192.168.22.1'])
        with fast_address_wait(), patch.object(facade, 'current_os', return_value=facade.LINUX), patch.object(linux, 'inspect', return_value=[old]), patch.object(linux, 'command', return_value='') as request:
            def read(**kwargs):return dict(adapters=[ready if request.call_count == 2 else old], error='')
            with patch.object(facade, 'inspect', side_effect=read):
                result = facade.renew(old, 30)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(result['addresses'], ready['addresses'])


if __name__ == '__main__':unittest.main()
