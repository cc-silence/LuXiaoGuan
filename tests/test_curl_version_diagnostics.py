"""Read-only curl and libcurl installed version diagnostics."""
import unittest
from threading import Event
from unittest.mock import Mock
from types import SimpleNamespace as NS
from modules.capture_services.openwrt.curl_version_diagnostics import parse_versions, version_detail, query_versions
from modules.capture_services.openwrt.common_tool_dependencies import check_tools
from modules.capture_services.openwrt.command_runner import RemoteCommandResult


class CurlVersionTests(unittest.TestCase):
    def test_opkg_and_apk_formats_and_versions(self):
        for manager in ('OPKG','APK'):
            self.assertEqual(parse_versions('__'+manager+'__\ncurl\t8.22.0-r1\nlibcurl4\t8.12.1-r2\n__END__\n'),{'curl':'8.22.0-r1','libcurl4':'8.12.1-r2'})
            self.assertEqual(parse_versions('__'+manager+'__\n__END__\n'),{})

    def test_invalid_partial_duplicate_or_untrusted_versions_are_rejected(self):
        for text in ('__OPKG__\ncurl\t8.1\n', '__APK__\ncurl\tbad data\n__END__', '__APK__\ncurl\t8.1\ncurl\t8.2\n__END__', '__APK__\nsecret\t8.1\n__END__'):
            with self.subTest(text=text),self.assertRaises(ValueError):parse_versions(text)

    def test_old_version_is_suspicion_not_confirmation(self):
        detail=version_detail({'curl':'8.22.0-r1','libcurl4':'8.12.1-r2'},'curl_multi_get_offt')
        self.assertIn('8.22.0-r1',detail);self.assertIn('8.12.1-r2',detail)
        self.assertIn('\u767b\u8bb0\u7248\u672c\u4e0d\u540c',detail)
        self.assertIn('\u767b\u8bb0\u7248\u672c\u76f8\u540c',version_detail({'curl':'8.22.0-r1','libcurl4':'8.22.0-r1'},''))
        self.assertIn('\u53ef\u80fd\u4e0d\u5339\u914d',detail);self.assertIn('\u5b9e\u9645\u52a0\u8f7d',detail)
        self.assertNotIn('\u53ef\u80fd\u4e0d\u5339\u914d',version_detail({'libcurl4':'8.22.0-r1'},'curl_multi_get_offt'))
        self.assertNotIn('\u53ef\u80fd\u4e0d\u5339\u914d',version_detail({'libcurl4':'8.12.1-r2'},'other_symbol'))

    def test_broken_check_adds_versions_but_keeps_failure(self):
        runner=Mock();runner.ssh_client=NS(connected=True)
        runner.run.side_effect=[RemoteCommandResult('probe',0,'BROKEN\nCODE:127\nError relocating /usr/bin/curl: curl_multi_get_offt: symbol not found'),RemoteCommandResult('versions',0,'__OPKG__\ncurl\t8.22.0-r1\nlibcurl4\t8.12.1-r2\n__END__')]
        row=check_tools(runner,('curl',),Event())[0]
        self.assertEqual(row.state.value,'unknown');self.assertIn('curl_multi_get_offt',row.detail)
        self.assertIn('8.22.0-r1',row.detail);self.assertIn('8.12.1-r2',row.detail)

    def test_query_failure_does_not_hide_original_error(self):
        runner=Mock();runner.ssh_client=NS(connected=True)
        runner.run.side_effect=[RemoteCommandResult('probe',0,'BROKEN\nCODE:127\nError relocating /usr/bin/curl: curl_multi_get_offt: symbol not found'),RemoteCommandResult('versions',1,'')]
        row=check_tools(runner,('curl',),Event())[0]
        self.assertEqual(row.state.value,'unknown');self.assertIn('curl_multi_get_offt',row.detail)
        self.assertIn('\u67e5\u8be2\u5931\u8d25',row.detail)

    def test_cancellation_propagates(self):
        runner=Mock();runner.run.return_value=RemoteCommandResult('versions',0,cancelled=True)
        with self.assertRaises(InterruptedError):query_versions(runner,Event())


class CurlLoaderTests(unittest.TestCase):
    def test_loader_path_and_ambiguous_results(self):
        from modules.capture_services.openwrt.curl_version_diagnostics import parse_loaded_library
        self.assertEqual(parse_loaded_library("\tlibcurl.so.4 => /usr/lib/libcurl.so.4 (0x1234)\n"), "/usr/lib/libcurl.so.4")
        self.assertEqual(parse_loaded_library("libcurl.so.4 => /usr/lib/libcurl.so.4 (0x1)\nlibcurl.so.4 => /lib/libcurl.so.4 (0x2)"), "")
        self.assertEqual(parse_loaded_library("libcurl.so.4 => not found"), "")
        self.assertEqual(parse_loaded_library("libcurl.so.4 => /tmp/../libcurl.so.4 (0x1)"), "")

    def test_check_reports_loader_path_without_touching_other_tools(self):
        runner=Mock();runner.ssh_client=NS(connected=True)
        runner.run.side_effect=[RemoteCommandResult('probe',0,'BROKEN\nCODE:127\nError relocating /usr/bin/curl: curl_multi_get_offt: symbol not found'),RemoteCommandResult('versions',0,'__OPKG__\ncurl\t8.22.0-r1\nlibcurl4\t8.10.1-r1\n__END__'),RemoteCommandResult('loader',1,'libcurl.so.4 => /usr/lib/libcurl.so.4 (0x1234)')]
        row=check_tools(runner,('curl',),Event())[0]
        self.assertIn('/usr/lib/libcurl.so.4',row.detail)
        self.assertEqual(row.state.value,'unknown')

    def test_missing_ldd_reports_unknown_and_preserves_error(self):
        runner=Mock(); runner.ssh_client=NS(connected=True)
        runner.run.side_effect=[RemoteCommandResult('probe',0,'BROKEN\nCODE:127\nError relocating /usr/bin/curl: curl_multi_get_offt: symbol not found'), RemoteCommandResult('versions',0,'__OPKG__\n__END__'), RemoteCommandResult('loader',1,'')]
        row=check_tools(runner,('curl',),Event())[0]
        self.assertIn('curl_multi_get_offt',row.detail)
        self.assertIn('\u8fd0\u884c\u5e93\u8def\u5f84\u65e0\u6cd5\u786e\u8ba4',row.detail)
        self.assertEqual(row.state.value,'unknown')

    def test_other_tools_never_call_curl_loader(self):
        runner=Mock(); runner.run.return_value=RemoteCommandResult('probe',0,'READY')
        check_tools(runner,('iw',),Event())
        self.assertEqual([call.args[0] for call in runner.run.call_args_list], ['dependency_common_iw'])

    def test_loader_cancellation_propagates(self):
        from modules.capture_services.openwrt.curl_version_diagnostics import query_loaded_library
        runner=Mock();runner.run.return_value=RemoteCommandResult('loader',0,cancelled=True)
        with self.assertRaises(InterruptedError):query_loaded_library(runner,Event())
