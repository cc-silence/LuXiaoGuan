"""curl probe error classification and privacy regression."""
import unittest
from threading import Event
from unittest.mock import Mock
from types import SimpleNamespace as NS
from modules.capture_services.openwrt.curl_probe_diagnostics import curl_failure_detail
from modules.capture_services.openwrt.common_tool_dependencies import check_tools
from modules.capture_services.openwrt.command_runner import RemoteCommandResult


class CurlDiagnosticTests(unittest.TestCase):
    def test_loader_missing_library_only_reports_safe_name(self):
        output='BROKEN\nCODE:127\nError loading shared library libssl.so.3: No such file or directory (needed by /secret/user/curl)\n'
        detail=curl_failure_detail(output)
        self.assertIn('libssl.so.3',detail);self.assertIn('127',detail);self.assertNotIn('/secret',detail)

    def test_relocation_missing_symbol_and_https_are_distinct(self):
        detail=curl_failure_detail('BROKEN\nCODE:127\nError relocating /secret/curl: SSL_foo: symbol not found')
        self.assertIn('SSL_foo',detail);self.assertNotIn('/secret',detail)
        self.assertIn('HTTPS',curl_failure_detail('BROKEN\nCODE:0\ncurl 8.0\nProtocols: http\n'))

    def test_unknown_response_never_leaks_raw_text(self):
        self.assertNotIn('password',curl_failure_detail('BROKEN\nCODE:1\npassword=private'))

    def test_check_keeps_broken_unknown_with_safe_detail(self):
        runner=Mock();runner.ssh_client=NS(connected=True)
        runner.run.return_value=RemoteCommandResult('probe',0,'BROKEN\nCODE:127\nError loading shared library libssl.so.3: No such file or directory')
        row=check_tools(runner,('curl',),Event())[0]
        self.assertEqual(row.state.value,'unknown');self.assertFalse(row.missing_packages);self.assertIn('libssl.so.3',row.detail)
