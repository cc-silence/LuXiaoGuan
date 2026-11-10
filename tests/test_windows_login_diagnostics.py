"""Customer collector regressions. Never alter real policy or authenticate to a router."""
import base64
import json
import unittest
from scripts.windows_login_diagnostics import policy_evidence as evidence
from tests.test_ipsec_snapshot_verification import filter_data, rule_data, u32, var
from modules.platform_services.legacy_ipsec_binary import rule

class SanitizerTests(unittest.TestCase):
    def test_filter_layouts_preserved_but_descriptions_removed(self):
        for modern, both in [(False, False), (True, False), (True, True)]:
            original = filter_data(modern, both)
            clean = evidence.binary_evidence('ipsecFilter{fixture}', original)
            self.assertEqual(clean['status'], 'sanitized')
            self.assertEqual(base64.b64decode(clean['base64']), original)
        # Replace the filter description with sensitive free text; offsets remain intact.
        original = filter_data()
        secret = 'PRIVATE-DESCRIPTION'.encode('utf-16-le')
        data = original[:36] + var(secret) + original[42:]
        clean = evidence.binary_evidence('ipsecFilter{fixture}', data)
        exported = base64.b64decode(clean['base64'])
        self.assertNotIn(secret, exported)
        self.assertEqual(len(exported), len(data))
    def test_both_authentication_arrays_are_erased_without_exporting_hashes(self):
        original = rule_data()[:-1]
        secret = 'PRIVATE-PSK'.encode('utf-16-le')
        # Original first Kerberos auth payload starts at byte 32, length=2.
        data = original[:24] + u32(1) + var(secret) + original[34:]
        data += evidence.AUTH1 + u32(1) + u32(1) + var(secret)
        data += evidence.AUTH2 + u32(0, 0) + b'\x00'
        exported = evidence.binary_evidence('ipsecNFA{fixture}', data)
        self.assertEqual(exported['status'], 'sanitized')
        clean = base64.b64decode(exported['base64'])
        self.assertEqual(len(clean), len(data))
        self.assertNotIn(secret, clean)
        self.assertNotIn('PRIVATE', json.dumps(exported))
        self.assertNotIn('sha256', exported)
    def test_unknown_truncated_and_oversized_data_fail_closed(self):
        for data in [b'PRIVATE-KEY', rule_data()[:-4], rule_data() + b'PRIVATE-TAIL', b'x' * (4 * 1024 * 1024 + 1)]:
            row = evidence.binary_evidence('ipsecNFA{fixture}', data)
            self.assertEqual(row['status'], 'omitted')
            self.assertNotIn('base64', row)
            self.assertNotIn('PRIVATE', json.dumps(row))
    def test_unneeded_binary_and_unknown_values_are_excluded(self):
        tree = {'ipsecISAKMPPolicy{fixture}': {'ipsecdata': (3, {'base64': base64.b64encode(b'private').decode()}),
            'psk': (1, 'private'), 'description': (1, 'private'), 'ipsecname': (1, 'Policy')},
            'ipsecNFA{fixture}': {'ipsecdata': (3, {'base64': 'bad!!'})}}
        clean = evidence.sanitize_tree(tree)
        self.assertNotIn('private', json.dumps(clean))
        self.assertEqual(clean['ipsecISAKMPPolicy{fixture}']['ipsecdata']['status'], 'omitted')
        self.assertEqual(clean['ipsecNFA{fixture}']['ipsecdata']['reason'], 'invalid_base64')


from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import socket
import tempfile
import threading
from unittest.mock import patch
from scripts.windows_login_diagnostics import collector, windows_host as host


def complete_report():
    return dict(schema_version=1, runtime={'administrator': True}, target={'address': '192.0.2.1', 'port': 22},
        services={name: dict(status='ok', state=4, start_type=2) for name in ('MpsSvc','BFE','PolicyAgent','domain_joined')},
        policies={name: dict(status='ok', nodes={}, node_count=0) for name in host.STORES},
        queries={name: dict(status='ok', data=[]) for name in (*host.QUERIES,'system','ipv4','routes','selected_route')},
        native_dump=dict(status='ok', output_bytes=2, whitespace_only=True),
        tcp=dict(status='ssh_banner_received', authenticated=False), consistency=dict(same_structure=True))

class CollectorTests(unittest.TestCase):
    def test_targets_reject_shell_injection_hostnames_and_invalid_ports(self):
        self.assertEqual(collector.validate_target('192.168.1.1', 22), ('192.168.1.1',22))
        for address,port in [('192.168.1.1;whoami',22),('router.test',22),('127.0.0.1',22),
                             ('::1',22),('0.0.0.0',22),('224.0.0.1',22),('192.168.1.1',True),('192.168.1.1',65536)]:
            with self.subTest(address=address,port=port),self.assertRaises(ValueError):collector.validate_target(address,port)
    def test_missing_admin_failed_stores_unknown_layout_and_changes_are_incomplete(self):
        report=complete_report();self.assertTrue(collector.conclusions(report)['collection_complete'])
        for change in ['admin','store','unknown','changed','dump']:
            report=complete_report()
            if change=='admin':report['runtime']['administrator']=False
            elif change=='store':report['policies']['local']=dict(status='failed',native_error=5)
            elif change=='unknown':report['policies']['local']['nodes']={'x':{'ipsecdata':{'reason':'unknown_or_truncated_layout'}}}
            elif change=='changed':report['consistency']['same_structure']=False
            else:report['native_dump']['status']='failed'
            with self.subTest(change=change):self.assertFalse(collector.conclusions(report)['collection_complete'])
    def test_unreachable_tcp_is_evidence_not_claim_of_policy_block(self):
        report=complete_report();report['tcp']=dict(status='connect_failed',errno=10060)
        summary=collector.conclusions(report)
        self.assertTrue(summary['collection_complete'])
        self.assertIn('tcp_not_established_cause_not_proven',summary['findings'])
    def test_archive_has_json_chinese_summary_and_no_raw_staging(self):
        report=complete_report();report['summary']=collector.conclusions(report)
        with tempfile.TemporaryDirectory() as folder:
            path=collector.write_report(report,folder)
            self.assertEqual(list(Path(folder).iterdir()),[path])
            import zipfile
            with zipfile.ZipFile(path) as archive:
                self.assertIsNone(archive.testzip())
                self.assertEqual(set(archive.namelist()),{'diagnostics.json','SUMMARY.txt'})
                self.assertEqual(json.loads(archive.read('diagnostics.json')),report)
                summary=archive.read('SUMMARY.txt').decode('utf-8-sig')
                self.assertIn('\u5fc5\u8981\u4fe1\u606f\u5b8c\u6574',summary)
                self.assertNotIn('\\u',summary)
    def test_one_query_failure_preserves_all_other_results(self):
        expected=complete_report()
        network={name:expected['queries'][name] for name in ('ipv4','routes','selected_route')}
        network['selected_route']=dict(status='failed',native_error=5)
        optional={name:expected['queries'][name] for name in host.QUERIES}
        with patch.object(host,'runtime',return_value=expected['runtime']),patch.object(host,'services',return_value=expected['services']),patch.object(host,'native_system',return_value=expected['queries']['system']),\
             patch.object(host,'policy_fingerprint',return_value=(expected['policies'],'same')),\
             patch.object(host,'native_network',return_value=network),patch.object(host,'run_optional_queries',return_value=optional),\
             patch.object(host,'native_dump',return_value=expected['native_dump']),patch.object(host,'tcp_probe',return_value=expected['tcp']):
            report=collector.collect('192.0.2.1',22)
        self.assertEqual(len(report['queries']),len(host.QUERIES)+4)
        self.assertFalse(report['summary']['collection_complete'])
        self.assertEqual(report['queries']['selected_route']['status'],'failed')
    def test_powershell_only_uses_fixed_read_queries_and_canonical_address(self):
        from subprocess import CompletedProcess
        with patch.object(host,'windows_directory',return_value=Path('system-fixture')),\
             patch.object(host,'capture_process',return_value=CompletedProcess([],0,b'[]')) as capture:
            self.assertEqual(host.run_query('selected_route','192.0.2.1',threading.Event())['status'],'ok')
            args=capture.call_args.args[0]
            script=base64.b64decode(args[-1]).decode('utf-16-le')
            self.assertIn("Find-NetRoute -RemoteIPAddress '192.0.2.1'",script)
            self.assertNotIn('ExecutionPolicy',args)
            self.assertEqual(capture.call_args.kwargs['timeout'],12)
            with self.assertRaises(ValueError):host.run_query('selected_route',"';whoami;#",threading.Event())
    def test_native_dump_reports_only_metadata_even_when_text_contains_a_key(self):
        from subprocess import CompletedProcess
        with patch.object(host,'windows_directory',return_value=Path('system-fixture')),\
             patch.object(host,'capture_process',return_value=CompletedProcess([],0,b'add rule psk="PRIVATE"')):
            value=host.native_dump(threading.Event())
        self.assertNotIn('PRIVATE',json.dumps(value));self.assertEqual(value['script_rows'],1)
    def test_command_timeout_is_recorded_without_exporting_arbitrary_exception_text(self):
        with patch.object(host,'windows_directory',return_value=Path('system-fixture')),\
             patch.object(host,'capture_process',side_effect=TimeoutError('PRIVATE')):
            value=host.native_dump(threading.Event())
        self.assertEqual(value['error_type'],'TimeoutError');self.assertNotIn('PRIVATE',json.dumps(value))

class SocketTests(unittest.TestCase):
    def test_receives_server_banner_but_never_sends_login_data(self):
        server=socket.socket();server.bind(('127.0.0.1',0));server.listen(1);server.settimeout(3)
        received=[]
        def serve():
            connection,_=server.accept()
            with connection:
                connection.settimeout(3)
                connection.sendall(b'SSH-2.0-fixture PRIVATE-COMMENT\r\n')
                received.append(connection.recv(128))
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(serve)
            try:
                value=host.tcp_probe('127.0.0.1',server.getsockname()[1],threading.Event())
                future.result(timeout=5)
            finally:server.close()
        self.assertEqual(value['status'],'ssh_banner_received')
        self.assertEqual(received,[b'']);self.assertFalse(value['authenticated'])
        self.assertEqual(value['banner'],'SSH-2.0-fixture')
        self.assertNotIn('PRIVATE',json.dumps(value))
    def test_cancel_before_probe_never_opens_socket(self):
        event=threading.Event();event.set()
        with patch.object(host.socket,'socket') as socket_factory:
            self.assertEqual(host.tcp_probe('127.0.0.1',22,event)['status'],'cancelled')
        socket_factory.assert_not_called()

if __name__ == '__main__':unittest.main()
