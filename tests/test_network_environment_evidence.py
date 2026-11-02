"""Evidence must survive anonymization without guessing VM networking modes."""
import json
import os
import unittest
from unittest.mock import patch
from modules.platform_services.network_environment import hints,linux,windows
from modules.platform_services.network_diagnostics import adapter_state,emit,observing
from modules.capture_services.management_diagnostics import failure_metadata
from modules.capture_services.openwrt.operation_diagnostics import OperationDiagnostics,sanitized_operation_evidence
from modules.capture_services.openwrt.upstream_firewall_zones import FirewallZoneConflict,upstream_zones
from modules.display_privacy import masker_for
from tests.test_lan_client_recovery import adapter


class EnvironmentEvidenceTests(unittest.TestCase):
    def test_vmware_detection_never_claims_nat_or_bridge(self):
        evidence=hints('VMware, Inc. VMware Virtual Platform')
        self.assertTrue(evidence['virtual_machine_observed'])
        self.assertTrue(evidence['vmware_observed'])
        self.assertFalse(evidence['host_network_config_visible'])
        self.assertFalse(evidence['virtual_network_mode_known'])
        self.assertIsNone(hints('Ordinary PC')['virtual_machine_observed'])

    def test_linux_unreadable_dmi_is_unknown(self):
        with patch('pathlib.Path.open',side_effect=PermissionError):
            self.assertIsNone(linux()['virtual_machine_observed'])

    @unittest.skipUnless(os.name=="nt","Windows registry evidence")
    def test_windows_missing_bios_is_unknown(self):
        with patch('winreg.OpenKey',side_effect=PermissionError):
            self.assertIsNone(windows()['virtual_machine_observed'])

    def test_changed_guest_lease_relationships_survive_export(self):
        old=adapter(dhcp_server='192.168.11.1',**hints('VMware'))
        current=dict(old,addresses=['192.168.22.88/24'],gateways=['192.168.22.1'],
                     dhcp_server='192.168.22.1',lease_expired=False)
        journal=OperationDiagnostics()
        with observing(lambda row:journal.record('configuration',operation='client_network_renew',**row)):
            emit('adapter_after',**adapter_state(current,['192.168.22.1/24'],baseline=old))
        exported=sanitized_operation_evidence(journal.snapshot(),masker_for([]))
        row=exported['configuration'][0]
        for key in ('addresses_changed','gateways_changed','dhcp_server_changed',
                    'dhcp_server_matches_target','dhcp_server_on_link','gateway_on_link','vmware_observed'):
            self.assertTrue(row[key],key)
        self.assertFalse(row['virtual_network_mode_known'])
        self.assertFalse(row['lease_expired'])
        text=json.dumps(exported)
        for secret in ('192.168.22.88','192.168.22.1',old['id'],old['mac']):self.assertNotIn(secret,text)

    def test_wrong_subnet_and_unknown_lease_are_distinct(self):
        row=adapter_state(adapter(dhcp_server='172.16.0.1'),['192.168.22.1/24'])
        self.assertFalse(row['target_on_link'])
        self.assertFalse(row['dhcp_server_on_link'])
        self.assertFalse(row['dhcp_server_matches_target'])
        self.assertIsNone(row['lease_expired'])
        self.assertIsNone(adapter_state(adapter())['dhcp_server_known'])
        self.assertFalse(adapter_state(adapter(dhcp_server='0.0.0.0'))['dhcp_server_known'])

    def test_address_loss_and_order_only_changes(self):
        old=adapter(addresses=['192.168.11.88/24','192.168.11.89/24'])
        self.assertFalse(adapter_state(dict(old,addresses=list(reversed(old['addresses']))),baseline=old)['addresses_changed'])
        state=adapter_state(dict(old,addresses=[]),baseline=old)
        self.assertTrue(state['adapter_found']);self.assertEqual(state['address_count'],0)
        self.assertTrue(state['addresses_changed'])

    def test_shared_zone_error_contains_private_free_membership_evidence(self):
        snapshot=dict(capabilities=dict(firewall4=True,firewall3=False),configs=dict(
            network=dict(wan={'.type':'interface'},lan={'.type':'interface'}),
            firewall=dict(zone={'.type':'zone','name':'private_zone','network':['wan','lan']})))
        try:upstream_zones(snapshot)
        except FirewallZoneConflict as exc:fields=failure_metadata(exc)
        else:self.fail('Shared local zone must remain blocked')
        self.assertEqual(fields['reason'],'firewall_shared_local')
        journal=OperationDiagnostics();journal.record('management',operation='policy_preview',status='failed',**fields)
        row=sanitized_operation_evidence(journal.snapshot(),masker_for([]))['management'][0]
        self.assertEqual(row['zone_member_count'],2)
        self.assertTrue(row['zone_has_lan']);self.assertTrue(row['upstream_is_wan'])
        self.assertFalse(row['zone_named_lan']);self.assertNotIn('private_zone',json.dumps(row))


if __name__=='__main__':unittest.main()
