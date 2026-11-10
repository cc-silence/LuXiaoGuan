"""Independent regressions for the official-source customer package review."""
import ctypes
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest
from unittest.mock import patch
from scripts.windows_login_diagnostics import collector, windows_host as host
from tests.test_windows_login_diagnostics import complete_report

class EvidenceCompletenessTests(unittest.TestCase):
    def test_all_missing_policy_binary_reasons_are_incomplete(self):
        for reason in ('invalid_base64','invalid_binary_wrapper','size_limit','unknown_or_truncated_layout'):
            with self.subTest(reason=reason):
                report=complete_report()
                report['policies']['local']['nodes']={'ipsecNFA{fixture}':{'ipsecdata':{'status':'omitted','reason':reason}}}
                self.assertIn('policy_binary_layout',collector.conclusions(report)['missing_required'])
        for row in ({},{'ipsecdata':{'status':'omitted'}},{'ipsecdata':{'status':'wrong'}}):
            report=complete_report();report['policies']['local']['nodes']={'ipsecFilter{fixture}':row}
            self.assertFalse(collector.conclusions(report)['collection_complete'])
    def test_intentionally_omitted_non_filter_binary_is_not_missing(self):
        report=complete_report()
        report['policies']['local']['nodes']={'ipsecISAKMPPolicy{fixture}':{'ipsecdata':{'status':'omitted','reason':'not_needed_for_login'}}}
        self.assertTrue(collector.conclusions(report)['collection_complete'])
    def test_probe_execution_failure_differs_from_connection_refusal(self):
        report=complete_report();report['tcp']={'status':'probe_error'}
        self.assertIn('port_probe',collector.conclusions(report)['missing_required'])
        report['tcp']={'status':'connect_failed','errno':10061}
        self.assertNotIn('port_probe',collector.conclusions(report)['missing_required'])
    def test_optional_failures_are_visible_in_customer_summary(self):
        report=complete_report();report['queries']['modern_ipsec_rules']={'status':'failed','native_error':5}
        report['summary']=collector.conclusions(report)
        self.assertIn('modern_ipsec_rules',collector.summary_text(report))
    def test_native_error_number_is_preserved_for_ip_helper_errors(self):
        self.assertEqual(host.error_record(OSError(5,'private'))['native_error'],5)
        self.assertNotIn('private',json.dumps(host.error_record(OSError(5,'private'))))

class AtomicReportTests(unittest.TestCase):
    def report(self):
        report=complete_report();report['summary']=collector.conclusions(report);return report
    def test_serialization_failure_leaves_no_final_zip_or_temporary_file(self):
        report=self.report();report['invalid']=object()
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(TypeError):collector.write_report(report,folder)
            self.assertEqual(list(Path(folder).iterdir()),[])
    def test_disk_flush_failure_leaves_no_false_successful_report(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(collector.os,'fsync',side_effect=OSError('fixture')):
            with self.assertRaises(OSError):collector.write_report(self.report(),folder)
            self.assertEqual(list(Path(folder).iterdir()),[])
    def test_integrity_failure_never_publishes_a_corrupt_zip(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(collector.zipfile.ZipFile,'testzip',return_value='bad'):
            with self.assertRaises(OSError):collector.write_report(self.report(),folder)
            self.assertEqual(list(Path(folder).iterdir()),[])
    def test_rename_failure_cleans_only_its_own_temporary_file(self):
        with tempfile.TemporaryDirectory() as folder:
            original=Path(folder)/'existing.zip';original.write_bytes(b'keep')
            with patch.object(Path,'rename',side_effect=PermissionError('fixture')):
                with self.assertRaises(PermissionError):collector.write_report(self.report(),folder)
            self.assertEqual(list(Path(folder).iterdir()),[original]);self.assertEqual(original.read_bytes(),b'keep')

class ProtocolBannerTests(unittest.TestCase):
    class Socket:
        def __init__(self,chunks):self.chunks=iter(chunks);self.closed=False;self.timeouts=[]
        def settimeout(self,value):self.timeouts.append(value)
        def connect(self,target):pass
        def getsockname(self):return ('127.0.0.1',10000)
        def recv(self,count):return next(self.chunks,b'')
        def close(self):self.closed=True
    def probe(self,chunks):
        sock=self.Socket(chunks)
        with patch.object(host.socket,'socket',return_value=sock):
            result=host.tcp_probe('127.0.0.1',22,threading.Event())
        self.assertTrue(sock.closed);return result
    def test_split_banner_after_preamble_is_not_accepted_as_partial(self):
        result=self.probe([b'notice SSH-port available\r\nSSH-2.0-part',b'ial comment\r\n'])
        self.assertEqual(result['banner'],'SSH-2.0-partial')
    def test_unterminated_or_wrong_protocol_line_is_not_ssh_success(self):
        for chunks in ([b'SSH-2.0-unfinished'],[b'SSH-9.9-unknown\r\n'],[b'notice SSH-2.0-fake\r\n']):
            with self.subTest(chunks=chunks):self.assertEqual(self.probe(chunks)['status'],'tcp_connected')
    def test_standard_length_limit_accepts_long_valid_identification(self):
        result=self.probe([b'SSH-2.0-'+b'a'*180+b'\r\n'])
        self.assertEqual(result['status'],'ssh_banner_received')
        result=self.probe([b'SSH-2.0-'+b'a'*247+b'\r\n'])
        self.assertEqual(result['status'],'tcp_connected')
    def test_socket_creation_failure_is_reported_as_missing_probe(self):
        with patch.object(host.socket,'socket',side_effect=OSError(10024,'fixture')):
            result=host.tcp_probe('127.0.0.1',22,threading.Event())
        self.assertEqual(result['status'],'probe_error')
    def test_each_receive_uses_remaining_budget(self):
        sock=self.Socket([b'notice\r\n',b''])
        with patch.object(host.socket,'socket',return_value=sock),patch.object(host.time,'monotonic',side_effect=[0,0,0,2.9,2.95,2.98,3.1]):
            host.tcp_probe('127.0.0.1',22,threading.Event())
        self.assertLess(sock.timeouts[1],0.11)

if __name__=='__main__':unittest.main()
