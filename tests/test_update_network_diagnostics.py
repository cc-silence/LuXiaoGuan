"""Update diagnostics must preserve cause without exposing credentials."""
import unittest
import requests
from PySide6.QtNetwork import QNetworkReply
from modules.app_update_services.network_errors import route_failure, request_failure_reason, qt_failure_reason

class UpdateNetworkDiagnosticsTests(unittest.TestCase):
    def test_credentials_and_code_do_not_reach_visible_diagnostics(self):
        message=route_failure('https://user:secret@updates.example/manifest?code=private',
                              'http://proxy-user:proxy-secret@localhost:1081', 'tls')
        self.assertIn('updates.example', message)
        self.assertIn('HTTPS', message)
        for secret in ('user', 'secret', 'private', 'localhost', '1081'):
            self.assertNotIn(secret, message)

    def test_both_transports_classify_tls_and_timeout(self):
        self.assertEqual(request_failure_reason(requests.exceptions.SSLError('private')), 'tls')
        self.assertEqual(qt_failure_reason(QNetworkReply.SslHandshakeFailedError), 'tls')
        self.assertEqual(request_failure_reason(requests.exceptions.ReadTimeout()), 'timeout')
        self.assertEqual(qt_failure_reason(QNetworkReply.OperationCanceledError), 'timeout')

    def test_streaming_read_timeout_is_not_misreported_as_generic_connection(self):
        from urllib3.exceptions import ReadTimeoutError
        wrapped=requests.exceptions.ConnectionError(ReadTimeoutError(None, '/blob', 'timed out'))
        self.assertEqual(request_failure_reason(wrapped), 'timeout')

    def test_proxy_failure_and_http_status_are_distinct(self):
        self.assertEqual(request_failure_reason(requests.exceptions.ProxyError()), 'proxy')
        self.assertIn('HTTP 407', route_failure('https://updates.example', 'http://proxy', '', 407))

if __name__ == '__main__':
    unittest.main()
