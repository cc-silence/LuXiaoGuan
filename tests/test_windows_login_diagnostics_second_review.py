"""Second official-source review: binary counters, native ABI and bounded results."""
import ctypes
import struct
import threading
import unittest
from unittest.mock import patch, Mock
from scripts.windows_login_diagnostics import policy_evidence as evidence
from scripts.windows_login_diagnostics import network_inventory as network
from tests.test_ipsec_snapshot_verification import filter_data

class FilterCounterTests(unittest.TestCase):
    def test_conflicting_overriding_count_is_not_recognized(self):
        raw=filter_data(True,True);offset=raw.index(evidence.FILTER_V2)
        raw=raw[:offset+20]+struct.pack('<I',2)+raw[offset+24:]
        result=evidence.binary_evidence('ipsecFilter{fixture}',raw)
        self.assertEqual(result['status'],'omitted')
        self.assertEqual(result['layout_error'],'unsupported_filter_count_override')
        self.assertNotIn('base64',result)
    def test_zero_or_matching_override_remains_readable(self):
        raw=filter_data(True,True);offset=raw.index(evidence.FILTER_V2)
        for count in (0,1):
            changed=raw[:offset+20]+struct.pack('<I',count)+raw[offset+24:]
            self.assertEqual(evidence.binary_evidence('ipsecFilter{fixture}',changed)['status'],'sanitized')

class NativeTableTests(unittest.TestCase):
    def api(self, payload, *, grow=False, returned_size=None):
        calls=[]
        def function(buffer,size,ordered):
            pointer=ctypes.cast(size,ctypes.POINTER(network.w.ULONG))
            calls.append(buffer is None)
            if buffer is None:
                pointer.contents.value=4 if grow else len(payload);return 122
            if grow and len(calls)==2:
                pointer.contents.value=len(payload);return 122
            ctypes.memmove(buffer,payload,len(payload))
            pointer.contents.value=len(payload) if returned_size is None else returned_size
            return 0
        return Mock(GetIpAddrTable=Mock(side_effect=function)),calls
    def payload(self):
        row=network.AddressRow(0x0101a8c0,14,0x00ffffff,0,65535,0,1)
        return bytes(network.DWORD(1))+bytes(row)
    def test_documented_row_layout_and_values(self):
        self.assertEqual(ctypes.sizeof(network.AddressRow),24)
        self.assertEqual(ctypes.sizeof(network.RouteRow),56)
        api,calls=self.api(self.payload())
        with patch.object(network.ctypes,'WinDLL',return_value=api,create=True):
            rows=network._table('GetIpAddrTable',network.AddressRow)
        self.assertEqual(rows,[(0x0101a8c0,14,0x00ffffff,0,65535,0,1)])
        self.assertEqual(network._address(rows[0][0]),'192.168.1.1')
    def test_interface_growth_has_one_bounded_resize(self):
        api,calls=self.api(self.payload(),grow=True)
        with patch.object(network.ctypes,'WinDLL',return_value=api,create=True):
            self.assertEqual(len(network._table('GetIpAddrTable',network.AddressRow)),1)
        self.assertEqual(len(calls),3)
    def test_truncated_count_and_impossible_returned_sizes_are_rejected(self):
        for payload,size in [(bytes(network.DWORD(2))+self.payload()[4:],None),(self.payload(),2),(self.payload(),1000)]:
            api,_=self.api(payload,returned_size=size)
            with patch.object(network.ctypes,'WinDLL',return_value=api,create=True),self.assertRaises(ValueError):
                network._table('GetIpAddrTable',network.AddressRow)


import base64,json,subprocess
from scripts.windows_login_diagnostics import windows_host as host,collector
from tests.test_windows_login_diagnostics import complete_report

class QueryLimitTests(unittest.TestCase):
    def run_script(self,queries):
        with patch.dict(host.QUERIES,queries,clear=True):script=host.optional_query_script()
        result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-EncodedCommand',base64.b64encode(script.encode('utf-16-le')).decode('ascii')],
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=15,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        self.assertEqual(result.returncode,0,result.stderr)
        return json.loads(result.stdout.decode('utf-8-sig'))
    def test_exact_limit_is_complete_and_over_limit_is_marked(self):
        for count,status,truncated in [(64,'ok',False),(65,'truncated',True)]:
            data=self.run_script({'adapters':'@(1..'+str(count)+" | ForEach-Object { [pscustomobject]@{ifIndex=$_} })"})['adapters']
            self.assertEqual(data['status'],status);self.assertEqual(data['truncated'],truncated)
            self.assertEqual(len(data['data']),64)
    def test_nested_security_product_limits_are_marked(self):
        row=self.run_script({'security_products':"@{antivirus=@(1..17);firewall=@(1..2)}"})['security_products']
        self.assertEqual(row['status'],'truncated');self.assertEqual(len(row['data']['antivirus']),16)
        self.assertEqual(len(row['data']['firewall']),2)
    def test_truncated_rows_appear_in_report_missing_supplement(self):
        report=complete_report();report['queries']['adapters']=dict(status='truncated',data=[],limit=64)
        summary=collector.conclusions(report)
        self.assertIn('adapters',summary['optional_failures'])
    def test_unsupported_query_never_executes(self):
        with patch.object(host,'capture_process') as capture,self.assertRaises(ValueError):
            host.run_optional_queries(threading.Event(),names=("bad';whoami",))
        capture.assert_not_called()

if __name__=='__main__':unittest.main()
