"""Three web-source reviews using real stdlib wire parsing and route contracts."""
import io
import time
from http.client import HTTPResponse,IncompleteRead
from threading import Event
import unittest
from unittest.mock import Mock
from modules.capture_services.openwrt.signal_package_feed import _read_body
from modules.capture_services.openwrt.package_manager import PackageManager
from modules.capture_services.openwrt.package_space import parse_opkg_sizes,parse_apk_sizes
from modules.capture_services.openwrt.installation_sources import install_with_sources
from modules.capture_services.openwrt.signal_manager import SignalError
from modules.capture_services.openwrt.offline_cleanup import cleanup_remote


class SocketBytes:
    def __init__(self,data):self.stream=io.BytesIO(data)
    def makefile(self,*args,**kwargs):return self.stream


class NativeOutputContracts(unittest.TestCase):
    def test_pinned_opkg_and_apk_information_formats(self):
        self.assertEqual(parse_opkg_sizes("Package: tmux\nVersion: 3.1c-1\nInstalled-Size: 212719\n",{"tmux":"3.1c-1"}),(212719,))
        self.assertEqual(parse_apk_sizes("tmux-3.5a-r1 installed size:\n212 KiB\n\n",{"tmux":"3.5a-r1"}),(212*1024,))
        PackageManager("opkg").verify_plan("Package tmux is already installed on root.",("tmux",))
        PackageManager("apk").verify_plan("OK: 10 MiB in 20 packages",("tmux",))


class HttpWireContracts(unittest.TestCase):
    def read_response(self,wire):
        response=HTTPResponse(SocketBytes(wire))
        response.begin()
        with response:
            return _read_body(response,1024,None,time.monotonic()+5)

    def test_content_length_body(self):
        self.assertEqual(self.read_response(b"HTTP/1.1 200 OK\r\nContent-Length: 9\r\n\r\nWikipedia"),b"Wikipedia")

    def test_chunked_body_without_content_length(self):
        wire=b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n4\r\nWiki\r\n5\r\npedia\r\n0\r\n\r\n"
        self.assertEqual(self.read_response(wire),b"Wikipedia")

    def test_close_delimited_body_without_content_length(self):
        self.assertEqual(self.read_response(b"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\nWikipedia"),b"Wikipedia")

    def test_truncated_declared_body_is_not_a_complete_index(self):
        with self.assertRaises(IncompleteRead):
            self.read_response(b"HTTP/1.1 200 OK\r\nContent-Length: 9\r\n\r\nWiki")


class FailureAndCleanupContracts(unittest.TestCase):
    def test_only_explicit_network_retry_uses_computer_route(self):
        for code in ("environment","cancelled","dependencies","transport"):
            with self.subTest(code=code):
                computer=Mock()
                original=SignalError(code,"signature/space/conflict/cancellation")
                with self.assertRaises(SignalError) as caught:
                    install_with_sources(lambda:False,Mock(side_effect=original),computer,Mock())
                self.assertIs(caught.exception,original)
                computer.assert_not_called()
        computer=Mock()
        source=install_with_sources(lambda:False,Mock(side_effect=SignalError("offline_retry","network")),computer,Mock())
        computer.assert_called_once()
        self.assertTrue(source)

    def test_cleanup_uses_independent_event_after_operation_cancelled(self):
        operation_cancel=Event();operation_cancel.set()
        execute=Mock(return_value="")
        self.assertTrue(cleanup_remote(execute,"cleanup","rm -rf /tmp/owned",Mock()))
        self.assertFalse(execute.call_args.args[3].is_set())
        self.assertTrue(operation_cancel.is_set())
