"""A failed TLS route may retry, with validation still mandatory everywhere."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
from threading import Event
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch
import requests
from PySide6.QtNetwork import QNetworkReply
from modules.capture_services.openwrt.common_bundle_download import _receive
from modules.app_update_services.checker import UpdateChecker


class RouteFailureTests(unittest.TestCase):
    def test_offline_tls_failure_moves_to_next_configured_proxy(self):
        session=MagicMock(); session.__enter__.return_value=session
        response=MagicMock(); response.__enter__.return_value=response
        response.status_code=200; response.iter_content.return_value=[b"verified bytes"]
        session.get.side_effect=[requests.exceptions.SSLError("handshake failed"),response]
        bridges=MagicMock(); bridges.__enter__.return_value=bridges
        bridges.http_proxy.side_effect=lambda url:url
        channels=[SimpleNamespace(proxy_url="http://first"),SimpleNamespace(proxy_url="http://second")]
        with patch("requests.Session",return_value=session),patch("modules.http_socks_bridge.ProxyBridgePool",return_value=bridges):
            result=_receive("https://example.com/bundle",None,20,Event(),lambda *_:None,float("inf"),channels)
        self.assertEqual(result,b"verified bytes")
        self.assertEqual(session.get.call_count,2)
        self.assertFalse(session.trust_env)
        for call in session.get.call_args_list:
            self.assertNotEqual(call.kwargs.get("verify"),False)
            self.assertFalse(call.kwargs["allow_redirects"])

    def test_all_tls_routes_fail_without_returning_unverified_bytes(self):
        session=MagicMock(); session.__enter__.return_value=session
        session.get.side_effect=requests.exceptions.SSLError("certificate invalid")
        bridges=MagicMock(); bridges.__enter__.return_value=bridges
        bridges.http_proxy.return_value="http://proxy"
        with patch("requests.Session",return_value=session),patch("modules.http_socks_bridge.ProxyBridgePool",return_value=bridges):
            with self.assertRaises(RuntimeError):
                _receive("https://example.com/bundle",None,20,Event(),lambda *_:None,float("inf"),[SimpleNamespace(proxy_url="a"),SimpleNamespace(proxy_url="b")])
        self.assertEqual(session.get.call_count,2)

    def test_async_update_tls_route_retries_but_http_denial_stops(self):
        for status,error,retry in [(None,QNetworkReply.SslHandshakeFailedError,True),(403,QNetworkReply.ContentAccessDenied,False)]:
            with self.subTest(status=status):
                reply=Mock(spec=QNetworkReply);reply.attribute.return_value=status;reply.error.return_value=error
                reply.rawHeader.return_value=b""
                checker=SimpleNamespace(_reply=reply,_read=Mock(),_stopped=False,_error="",_stage="manifest",_channels=[object()],_next_channel=Mock(),_finish=Mock())
                UpdateChecker._received(checker)
                self.assertEqual(checker._next_channel.called,retry)
                self.assertEqual(checker._finish.called,not retry)
                reply.deleteLater.assert_called_once()


if __name__=="__main__": unittest.main()
