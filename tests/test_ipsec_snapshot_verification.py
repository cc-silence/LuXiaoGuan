"""Protocol fixtures are independent of the command plan; no host policy writes."""
import base64
import copy
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import UUID
from modules.platform_services import legacy_ipsec as policy
from modules.platform_services.legacy_ipsec_snapshot_verification import verify_snapshot
from modules.platform_services.legacy_ipsec_store import PolicySnapshot, LOCAL
from modules.platform_services.ipsec_verification import IPsecVerificationError, safe_evidence
from tests.test_legacy_ipsec import PLAN, ID

FILTER = 'ipsecFilter{00000000-0000-0000-0000-000000000001}'
ACTION = 'ipsecNegotiationPolicy{00000000-0000-0000-0000-000000000002}'
RULE = 'ipsecNFA{00000000-0000-0000-0000-000000000003}'
FILTER_ID = UUID('00000000-0000-0000-0000-000000000004').bytes_le

def u32(*values):return struct.pack('<' + 'I' * len(values), *values)
def var(data=b'\x00\x00'):return u32(len(data)) + data
def blob(data):return (3, {'base64': base64.b64encode(data).decode('ascii')})
def ref(key):return LOCAL + '\\' + key

def filter_data(modern=False, both=False, port=22):
    # MS-GPIPSEC 2.2.1.5.1, independently constructed endpoint 192.168.1.1.
    common = var() * 3 + FILTER_ID + u32(1)
    legacy = common + bytes.fromhex('00000000 ffffffff c0a80101 ffffffff 00000000')
    legacy += u32(6) + struct.pack('<HHBBH', 0, port, 0, 0, 0)
    v1 = UUID('80dc20b5-2ec8-11d1-a89e-00a0248d3021').bytes_le + u32(len(legacy), 1) + legacy
    source = u32(8, 1) + bytes(32)
    destination = u32(1, 1) + bytes.fromhex('c0a80101') + bytes(28)
    entry = common + source + destination + struct.pack('<IHHIHHII', 0, 0, 0, 1, port, 0, 6, 0)
    v2 = UUID('35fecd3d-ae29-4373-8a6a-c5d8fab2fb08').bytes_le + u32(len(entry), int(both), 1) + entry
    return ((v1 if both else b'') + v2 if modern else v1) + b'\x00'

def rule_data(active=1, tunnel=0, interface=0xfffffffd, auth=1):
    body = u32(auth) + (u32(5) + var()) * auth + u32(interface) + var()
    body += bytes(4) + u32(tunnel, active) + var()
    return UUID('11bbac00-498d-11d1-8639-00a0248d3021').bytes_le + u32(len(body)) + body + b'\x00'

def fixture(modern=False, both=False):
    nodes = {
        '': {'activepolicy': (1, ref(ID))},
        ID: {'ipsecname': (1, PLAN.policy), 'ipsecnfareference': (7, [ref(RULE)])},
        FILTER: {'ipsecname': (1, PLAN.names[0]), 'ipsecownersreference': (7, [ref(RULE)]),
                 'ipsecdata': blob(filter_data(modern, both))},
        ACTION: {'ipsecname': (1, PLAN.names[1]), 'ipsecownersreference': (7, [ref(RULE)]),
                 'ipsecnegotiationpolicyaction': (1, '{8A171DD2-77E3-11D1-8659-A04F00000000}'),
                 'ipsecnegotiationpolicytype': (1, '{62F49E10-6C37-11D1-864C-14A300000000}')},
        RULE: {'ipsecname': (1, PLAN.names[2]), 'ipsecownersreference': (1, ref(ID)),
               'ipsecfilterreference': (1, ref(FILTER)),
               'ipsecnegotiationpolicyreference': (1, ref(ACTION)), 'ipsecdata': blob(rule_data())}}
    return PolicySnapshot(nodes, ID, PLAN.policy)

class StructuredStore:
    verify = policy.NativeStore.verify
    def __init__(self, directory, wrong_port=False):
        self.directory = directory
        self.full = fixture()
        self.nodes = {k: copy.deepcopy(v) for k, v in self.full.nodes.items() if k in ('', ID)}
        self.nodes[ID]['ipsecnfareference'] = (7, [])
        self.commands = []
        self.wrong_port = wrong_port
    def snapshot(self):return PolicySnapshot(copy.deepcopy(self.nodes), ID, PLAN.policy)
    def backup(self):return self.directory, 'fixture-only-hash'
    def mutate(self, args):
        self.commands.append(args)
        op, kind = args[:2]
        if kind == 'filter':
            self.nodes[FILTER]['ipsecdata'] = blob(filter_data(port=0 if self.wrong_port else 22))
            return
        key = {'filterlist': FILTER, 'filteraction': ACTION, 'rule': RULE}[kind]
        if op == 'add':
            self.nodes[key] = copy.deepcopy(self.full.nodes[key])
            if kind == 'rule':self.nodes[ID]['ipsecnfareference'] = (7, [ref(RULE)])
        else:
            self.nodes.pop(key, None)
            if kind == 'rule':self.nodes[ID]['ipsecnfareference'] = (7, [])

class SnapshotTests(unittest.TestCase):
    def test_v1_v2_and_dual_format_verify_without_decoding_text(self):
        for modern, both in [(False, False), (True, False), (True, True)]:
            with self.subTest(modern=modern, both=both):
                self.assertTrue(verify_snapshot(PLAN, fixture(modern, both)))
    def test_native_verifier_never_launches_dump(self):
        store = policy.NativeStore()
        with patch.object(store, 'snapshot', return_value=fixture()), patch.object(policy, 'capture_process') as capture:
            self.assertTrue(store.verify(PLAN))
        capture.assert_not_called()
    def test_first_and_repeat_login_do_not_depend_on_empty_dump(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StructuredStore(Path(directory))
            with patch.object(policy, 'capture_process', side_effect=AssertionError('No dump allowed')):
                first = policy.ensure_target(PLAN.address, 22, store)
                second = policy.ensure_target(PLAN.address, 22, store)
            self.assertEqual(first['status'], 'appended_verified')
            self.assertEqual(second['status'], 'existing_verified')
            self.assertEqual(len(store.commands), 4)
    def test_bad_saved_port_still_rolls_back_owned_objects(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StructuredStore(Path(directory), wrong_port=True)
            result = policy.ensure_target(PLAN.address, 22, store)
            self.assertFalse(result['success'])
            self.assertEqual(result['rollback'], 'verified')
            self.assertEqual(result['verification']['field'], 'dstport')
            self.assertEqual(set(store.nodes), {'', ID})
    def reject(self, snapshot, reason=None):
        with self.assertRaises(IPsecVerificationError) as error:verify_snapshot(PLAN, snapshot)
        if reason:self.assertEqual(error.exception.verification['reason'], reason)
        self.assertNotIn(PLAN.address, str(error.exception))
        self.assertNotIn(PLAN.policy, str(error.exception))
    def test_wrong_reference_stores_owners_and_assignment_rejected(self):
        cases = [(RULE, 'ipsecfilterreference', (1, ref(FILTER).replace('Local', 'Persistent'))),
                 (RULE, 'ipsecnegotiationpolicyreference', (1, ref(FILTER))),
                 (RULE, 'ipsecownersreference', (1, ref('ipsecPolicy{other}'))),
                 (ID, 'ipsecnfareference', (7, [])),
                 (FILTER, 'ipsecownersreference', (7, [ref(RULE), ref('other')])),
                 (ACTION, 'ipsecnegotiationpolicyaction', (1, '{3F91A819-7647-11D1-864D-D46A00000000}'))]
        for key, field, value in cases:
            with self.subTest(field=field):
                snap = fixture();snap.nodes[key][field] = value;self.reject(snap)
    def test_rules_must_be_active_transport_all_interfaces(self):
        for values in [dict(active=0), dict(tunnel=1), dict(interface=0xfffffffe)]:
            snap = fixture();snap.nodes[RULE]['ipsecdata'] = blob(rule_data(**values));self.reject(snap)
    def test_multiple_certificates_do_not_require_text_parsing(self):
        snap = fixture();snap.nodes[RULE]['ipsecdata'] = blob(rule_data(auth=2))
        self.assertTrue(verify_snapshot(PLAN, snap))
    def test_documented_rule_extensions_and_length_conventions(self):
        base = rule_data()[:-1]
        extension = bytes([1]) * 16 + u32(1) + u32(5) + var(b'\x00')
        extension += bytes([1]) * 15 + b'\x02' + u32(0, 0)
        extension += bytes([1]) * 15 + b'\x03' + bytes(16)
        for suffix in (b'', extension):
            for adjustment in (0, -1):
                data = base[:16] + u32(len(base) - 20 + adjustment) + base[20:] + suffix + b'\x00'
                snap = fixture();snap.nodes[RULE]['ipsecdata'] = blob(data)
                self.assertTrue(verify_snapshot(PLAN, snap))
        snap = fixture();snap.nodes[RULE]['ipsecdata'] = blob(base + bytes([1]) * 16 + u32(2))
        self.reject(snap, 'policy_data')
    def test_v1_broad_filters_and_unbounded_input_are_rejected(self):
        original = filter_data()
        fixed = 24 + 18 + 16
        for offset, data in [(fixed, u32(0)), (fixed + 8, bytes(4)),
                             (fixed + 12, bytes.fromhex('c0a80102')),
                             (fixed + 16, bytes.fromhex('ffffff00')),
                             (fixed + 24, u32(17)), (fixed + 30, struct.pack('<H', 0))]:
            snap = fixture()
            snap.nodes[FILTER]['ipsecdata'] = blob(original[:offset] + data + original[offset + len(data):])
            self.reject(snap)
        snap = fixture();snap.nodes[FILTER]['ipsecdata'] = blob(b'x' * (4 * 1024 * 1024 + 1))
        self.reject(snap, 'policy_data')
    def test_bad_lengths_unknown_versions_and_trailing_data_rejected(self):
        for key, original in [(FILTER, filter_data()), (FILTER, filter_data(True)), (RULE, rule_data())]:
            for data in [original[:10], original[:-3], original + b'private', b'?' * len(original)]:
                with self.subTest(key=key, size=len(data)):
                    snap = fixture();snap.nodes[key]['ipsecdata'] = blob(data);self.reject(snap, 'policy_data')
    def test_any_address_subnet_port_protocol_and_mirror_rejected(self):
        original = filter_data(True)
        # One modern entry starts after 28-byte header + strings + ID + mirror.
        source = 28 + 18 + 16 + 4
        destination = source + 40
        ports = destination + 40
        for offset, data in [(source, u32(0)), (destination, u32(4)),
                             (destination + 8, bytes.fromhex('c0a80102')),
                             (ports + 8, u32(0)), (ports + 16, u32(17)),
                             (source - 4, u32(0))]:
            snap = fixture(True)
            changed = original[:offset] + data + original[offset + len(data):]
            snap.nodes[FILTER]['ipsecdata'] = blob(changed);self.reject(snap)
    def test_case_insensitive_names_and_references(self):
        snap = fixture()
        for key in (FILTER, ACTION, RULE):
            snap.nodes[key]['ipsecname'] = (1, snap.nodes[key]['ipsecname'][1].upper())
        snap.nodes[RULE]['ipsecfilterreference'] = (1, ref(FILTER).upper())
        self.assertTrue(verify_snapshot(PLAN, snap))
    def test_duplicate_names_extra_filters_foreign_references_rejected(self):
        snap = fixture();snap.nodes['duplicate'] = copy.deepcopy(snap.nodes[FILTER]);self.reject(snap, 'filterlist_count')
        snap = fixture();snap.nodes['foreign'] = {'ipsecfilterreference': (1, ref(FILTER))};self.reject(snap, 'foreign_reference')
        snap = fixture();data = filter_data();data = data[:20] + u32(2) + data[24:]
        snap.nodes[FILTER]['ipsecdata'] = blob(data);self.reject(snap, 'policy_data')
    def test_malformed_registry_values_fail_with_sanitized_evidence(self):
        for value in [(1, 'secret'), (3, {'base64': '!!private!!'}), (3, {'base64': [], 'extra': 1})]:
            snap = fixture();snap.nodes[FILTER]['ipsecdata'] = value;self.reject(snap, 'policy_data')
        self.assertEqual(safe_evidence({'reason': 'policy_data', 'private': 'secret'}), {'reason': 'policy_data'})

if __name__ == '__main__':unittest.main()
