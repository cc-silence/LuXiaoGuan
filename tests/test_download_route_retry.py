"""Bounded route retry contracts."""
import errno
import io
import ssl
import unittest
from contextlib import ExitStack
from threading import Event
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from modules.capture_services.openwrt import signal_package_feed as feed

class DownloadRetryTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.routes = ["http://user:secret@localhost:1081", "http://localhost:1082", "direct"]
        self.calls, self.messages = [], []
        self.stack.enter_context(feed.download_session(lambda text, *args: self.messages.append(text)))
        self.stack.enter_context(patch.object(feed, "_attempt_plans", return_value=[(r, r) for r in self.routes]))
        factory = self.stack.enter_context(patch.object(feed.urllib.request, "build_opener"))
        factory.side_effect = self.opener

    def opener(self, route):
        def open_request(*args, **kwargs):
            self.calls.append(route)
            outcome = next(self.outcomes)
            if callable(outcome):
                outcome = outcome()
            if isinstance(outcome, Exception):
                raise outcome
            response = Mock(status=200, headers={})
            response.read1.side_effect = [outcome, b""]
            context = Mock()
            context.__enter__ = Mock(return_value=response)
            context.__exit__ = Mock(return_value=False)
            return context
        return Mock(open=Mock(side_effect=open_request))

    def fetch(self, outcomes, **kwargs):
        self.outcomes = iter(outcomes)
        return feed.fetch_bytes("https://fixture/package", **kwargs)

    def test_all_routes_before_retry(self):
        self.assertEqual(self.fetch([TimeoutError()] * 3 + [b"ok"]), b"ok")
        self.assertEqual(self.calls, self.routes + self.routes[:1])
        self.assertTrue(any("2/2" in m for m in self.messages))
        self.assertNotIn("secret", "".join(self.messages))

    def test_exactly_two_rounds(self):
        with self.assertRaises(RuntimeError):
            self.fetch([TimeoutError()] * 6)
        self.assertEqual(self.calls, self.routes * 2)

    def test_first_round_success(self):
        self.assertEqual(self.fetch([TimeoutError(), b"ok"]), b"ok")
        self.assertEqual(self.calls, self.routes[:2])

    def test_refused_can_recover(self):
        refused = URLError(ConnectionRefusedError(errno.ECONNREFUSED, "refused"))
        self.assertEqual(self.fetch([refused] * 3 + [b"ok"]), b"ok")
        self.assertEqual(self.calls, self.routes + self.routes[:1])

    def test_temporary_http_retry_closes_errors(self):
        bodies = [io.BytesIO() for _ in range(3)]
        errors = [HTTPError("https://fixture", 503, "busy", {}, b) for b in bodies]
        self.assertEqual(self.fetch(errors + [b"ok"]), b"ok")
        self.assertTrue(all(b.closed for b in bodies))
        self.assertEqual(self.calls, self.routes + self.routes[:1])

    def test_certificate_and_permission_not_retried(self):
        cert = URLError(ssl.SSLCertVerificationError(1, "invalid"))
        denied = URLError(PermissionError(errno.EACCES, "denied"))
        self.assertEqual(self.fetch([cert, denied, TimeoutError(), b"ok"]), b"ok")
        self.assertEqual(self.calls, self.routes + self.routes[2:])

    def test_missing_file_not_retried(self):
        with self.assertRaisesRegex(RuntimeError, "HTTP 404"):
            self.fetch([HTTPError("https://fixture", 404, "missing", {}, io.BytesIO())])
        self.assertEqual(self.calls, self.routes[:1])

    def test_cancel_before_second_round(self):
        cancel = Event()
        def cancel_last():
            cancel.set()
            return TimeoutError()
        with self.assertRaises(RuntimeError):
            self.fetch([TimeoutError(), TimeoutError(), cancel_last], cancel_event=cancel)
        self.assertEqual(self.calls, self.routes)

    def test_total_deadline_shared(self):
        now = [0.0]
        def slow_failure():
            now[0] += 2.0
            return TimeoutError()
        with patch.object(feed.time, "monotonic", side_effect=lambda: now[0]):
            with self.assertRaises(RuntimeError):
                self.fetch([slow_failure] * 3, timeout=6)
        self.assertEqual(self.calls, self.routes)
        self.assertFalse(any("2/2" in m for m in self.messages))

    def test_size_limit_not_retried(self):
        with self.assertRaises(RuntimeError):
            self.fetch([b"too large"], limit=1)
        self.assertEqual(self.calls, self.routes[:1])


    def test_explicit_server_wait_before_next_route(self):
        for status in (429, 503):
            with self.subTest(status=status):
                feed._download_state.get()["preferred"] = None
                self.calls.clear()
                now = [10.0]
                def advance(delay):
                    now[0] += delay
                error = HTTPError("https://fixture", status, "busy", {"Retry-After": "3"}, io.BytesIO())
                with patch.object(feed.time, "monotonic", side_effect=lambda: now[0]), \
                     patch.object(feed, "Event") as event:
                    event.return_value.wait.side_effect = advance
                    self.assertEqual(self.fetch([error, b"ok"]), b"ok")
                    event.return_value.wait.assert_called_once_with(3.0)
                self.assertEqual(self.calls, self.routes[:2])
                self.assertTrue(error.fp.closed)

    def test_cancel_during_server_wait_stops_all_routes(self):
        cancel = Event()
        error = HTTPError("https://fixture", 503, "busy", {"Retry-After": "3"}, io.BytesIO())
        with patch.object(cancel, "wait", side_effect=lambda delay: cancel.set()) as wait:
            with self.assertRaises(RuntimeError):
                self.fetch([error], cancel_event=cancel)
            wait.assert_called_once()
        self.assertEqual(self.calls, self.routes[:1])

    def test_server_wait_exceeding_budget_does_not_sleep_or_retry(self):
        error = HTTPError("https://fixture", 503, "busy", {"Retry-After": "300"}, io.BytesIO())
        with patch.object(feed, "Event") as event:
            with self.assertRaises(RuntimeError):
                self.fetch([error], timeout=5)
            event.assert_not_called()
        self.assertEqual(self.calls, self.routes[:1])

    def test_invalid_wait_header_does_not_add_delay(self):
        error = HTTPError("https://fixture", 503, "busy", {"Retry-After": "invalid"}, io.BytesIO())
        with patch.object(feed, "Event") as event:
            self.assertEqual(self.fetch([error, b"ok"]), b"ok")
            event.assert_not_called()

    def test_wait_does_not_extend_original_deadline(self):
        now = [10.0]
        def overrun(delay):
            now[0] = 100.0
        error = HTTPError("https://fixture", 503, "busy", {"Retry-After": "3"}, io.BytesIO())
        with patch.object(feed.time, "monotonic", side_effect=lambda: now[0]), \
             patch.object(feed, "Event") as event:
            event.return_value.wait.side_effect = overrun
            with self.assertRaises(RuntimeError):
                self.fetch([error], timeout=5)
        self.assertEqual(self.calls, self.routes[:1])

    def test_wait_preserves_maximum_two_rounds(self):
        now = [10.0]
        def advance(delay):
            now[0] += delay
        errors = [HTTPError("https://fixture", 503, "busy", {"Retry-After": "1"}, io.BytesIO()) for _ in range(2)]
        with patch.object(feed, "_attempt_plans", return_value=[(self.routes[0], self.routes[0])]), \
             patch.object(feed.time, "monotonic", side_effect=lambda: now[0]), \
             patch.object(feed, "Event") as event:
            event.return_value.wait.side_effect = advance
            with self.assertRaises(RuntimeError):
                self.fetch(errors)
            event.return_value.wait.assert_called_once_with(1.0)
        self.assertEqual(self.calls, self.routes[:1] * 2)


    def test_proxy_auth_error_does_not_abort_other_routes(self):
        body = io.BytesIO()
        error = HTTPError("https://fixture", 407, "Proxy Authentication Required", {}, body)
        self.assertEqual(self.fetch([error, b"ok"]), b"ok")
        self.assertEqual(self.calls, self.routes[:2])
        self.assertTrue(body.closed)

    def test_auth_failed_routes_are_not_in_second_round(self):
        error = HTTPError("https://fixture", 407, "Proxy Authentication Required", {}, io.BytesIO())
        tunnel = URLError(OSError("Tunnel connection failed: 407 Proxy Authentication Required"))
        self.assertEqual(self.fetch([error, tunnel, TimeoutError(), b"ok"]), b"ok")
        self.assertEqual(self.calls, self.routes + self.routes[2:])

    def test_proxy_auth_error_cached_for_session_only(self):
        error = HTTPError("https://fixture", 407, "Proxy Authentication Required", {}, io.BytesIO())
        with self.assertRaises(RuntimeError):
            self.fetch([error, TimeoutError(), TimeoutError(), TimeoutError(), TimeoutError()])
        self.assertEqual(self.calls, self.routes + self.routes[1:])
        self.calls.clear()
        self.assertEqual(self.fetch([b"ok"]), b"ok")
        self.assertEqual(self.calls, self.routes[1:2])


class RetryAfterParsingTests(unittest.TestCase):
    def test_seconds_missing_invalid_and_extreme_values(self):
        for value, expected in [("0", 0), (" 12 ", 12), ("-1", 0), ("1.5", 0), ("invalid", 0), (None, 0), ("9" * 1000, float("inf"))]:
            with self.subTest(value=str(value)[:30]):
                self.assertEqual(feed._retry_after_seconds({"Retry-After": value}), expected)
        self.assertEqual(feed._retry_after_seconds({}), 0)
        self.assertEqual(feed._retry_after_seconds(None), 0)

    def test_http_date_formats_are_utc(self):
        from datetime import datetime, timezone
        target = datetime(1994, 11, 6, 8, 49, 37, tzinfo=timezone.utc).timestamp()
        dates = ["Sun, 06 Nov 1994 08:49:37 GMT", "Sunday, 06-Nov-94 08:49:37 GMT", "Sun Nov  6 08:49:37 1994"]
        with patch.object(feed.time, "time", return_value=target - 15):
            for value in dates:
                self.assertEqual(feed._retry_after_seconds({"Retry-After": value}), 15)
        with patch.object(feed.time, "time", return_value=target + 15):
            self.assertEqual(feed._retry_after_seconds({"Retry-After": dates[0]}), 0)


class RequestIsolationTests(unittest.TestCase):
    def test_real_proxy_handlers_do_not_contaminate_next_route(self):
        for scheme in ("http", "https"):
            with self.subTest(scheme=scheme):
                self.check_routes(scheme)

    def check_routes(self, scheme):
        seen = []
        labels = ["http://user:secret@localhost:1081", "http://localhost:1082", "\u76f4\u8fde"]
        plans = [(label, feed.urllib.request.ProxyHandler({scheme: label} if i < 2 else {}))
                 for i, label in enumerate(labels)]

        def transport(request):
            seen.append((request.host, request._tunnel_host, request.selector,
                         request.has_header("Proxy-authorization")))
            if len(seen) < 4:
                raise URLError(TimeoutError())
            response = feed.urllib.request.addinfourl(io.BytesIO(b"ok"),
                         {"Content-Length": "2"}, request.full_url, 200)
            response.msg = "OK"
            return response

        class Transport(feed.urllib.request.HTTPSHandler, feed.urllib.request.HTTPHandler):
            def http_open(self, request):
                return transport(request)
            def https_open(self, request):
                return transport(request)

        build = feed.urllib.request.build_opener
        def build_with_transport(handler):
            return build(handler, Transport())

        with patch.object(feed, "_attempt_plans", return_value=plans), \
             patch.object(feed.urllib.request, "proxy_bypass", return_value=False), \
             patch.object(feed.urllib.request, "build_opener", side_effect=build_with_transport):
            self.assertEqual(feed.fetch_bytes(scheme + "://fixture/package"), b"ok")
        selector = "/package" if scheme == "https" else scheme + "://fixture/package"
        tunnel = "fixture" if scheme == "https" else None
        self.assertEqual(seen, [
            ("localhost:1081", tunnel, selector, True),
            ("localhost:1082", tunnel, selector, False),
            ("fixture", None, "/package", False),
            ("localhost:1081", tunnel, selector, True),
        ])


class LocalHttpRetryTests(unittest.TestCase):
    def check_wire_download(self, outcomes, expected=None):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from threading import Thread
        calls = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                calls.append(self.command)
                outcome = outcomes[len(calls) - 1]
                code, declared, body = outcome[:3]
                self.send_response(code)
                self.send_header("Content-Length", str(declared))
                for key, value in (outcome[3] if len(outcome) > 3 else {}).items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        worker.start()
        try:
            plans = [("\u76f4\u8fde", feed.urllib.request.ProxyHandler({}))]
            with patch.object(feed, "_attempt_plans", return_value=plans):
                url = "http://127.0.0.1:%d/package" % server.server_port
                if expected is None:
                    with self.assertRaises(RuntimeError):
                        feed.fetch_bytes(url, timeout=5)
                else:
                    self.assertEqual(feed.fetch_bytes(url, timeout=5), expected)
        finally:
            server.shutdown()
            server.server_close()
            worker.join(2)
            self.assertFalse(worker.is_alive())
        self.assertEqual(calls, ["GET"] * len(outcomes))

    def test_real_http_503_then_second_round_success(self):
        self.check_wire_download([(503, 0, b""), (200, 2, b"ok")], b"ok")

    def test_real_truncated_response_then_second_round_success(self):
        self.check_wire_download([(200, 9, b"bad"), (200, 2, b"ok")], b"ok")

    def test_real_http_all_fail_only_twice(self):
        self.check_wire_download([(503, 0, b""), (503, 0, b"")])

    def test_real_http_explicit_retry_after_wait(self):
        start = feed.time.monotonic()
        self.check_wire_download([(503, 0, b"", {"Retry-After": "1"}), (200, 2, b"ok")], b"ok")
        self.assertGreaterEqual(feed.time.monotonic() - start, 0.9)

    def test_real_http_redirect_still_downloads(self):
        self.check_wire_download([(302, 0, b"", {"Location": "/new"}), (200, 2, b"ok")], b"ok")

    def test_real_http_redirect_loop_stops(self):
        self.check_wire_download([(302, 0, b"", {"Location": "/package"})] * 5)


class NativeProxyAndBodyTests(unittest.TestCase):
    def test_real_cpython_tunnel_auth_error_classification(self):
        from http.client import HTTPConnection
        for code, expected in ((407, True), (503, False)):
            connection = HTTPConnection("proxy.invalid")
            socket = Mock()
            socket.makefile.return_value = io.BytesIO(("HTTP/1.1 %d Proxy Error\r\n\r\n" % code).encode("ascii"))
            connection.set_tunnel("fixture", 443)
            connection.sock = socket
            with self.assertRaises(OSError) as caught:
                connection._tunnel()
            self.assertEqual(feed._proxy_auth_error(URLError(caught.exception)), expected)
            socket.close.assert_called_once()

    def test_real_incomplete_chunked_bodies_are_closed_and_rejected(self):
        from http.client import HTTPResponse, IncompleteRead
        for body in (b"4\r\nWi", b"4\r\nWiki\r\n", b"4\r\nWiki\r\ninvalid\r\n"):
            stream = io.BytesIO(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n" + body)
            socket = Mock()
            socket.makefile.return_value = stream
            response = HTTPResponse(socket)
            response.begin()
            with self.assertRaises(IncompleteRead):
                with response:
                    feed._read_body(response, 1024, None, feed.time.monotonic() + 5)
            self.assertTrue(stream.closed)

    def test_real_auth_failed_proxy_falls_back_to_origin(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from threading import Thread
        seen = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append((self.server.role, self.path))
                self.send_response(407 if self.server.role == "proxy" else 200)
                if self.server.role == "proxy":
                    self.send_header("Proxy-Authenticate", 'Basic realm="test"')
                self.send_header("Content-Length", "0" if self.server.role == "proxy" else "2")
                self.end_headers()
                if self.server.role == "origin":
                    self.wfile.write(b"ok")
            def log_message(self, *args):
                pass
        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as proxy, \
             ThreadingHTTPServer(("127.0.0.1", 0), Handler) as origin:
            proxy.role, origin.role = "proxy", "origin"
            workers = [Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True) for server in (proxy, origin)]
            for worker in workers:
                worker.start()
            try:
                proxy_url = "http://127.0.0.1:%d" % proxy.server_port
                url = "http://127.0.0.1:%d/package" % origin.server_port
                plans = [(proxy_url, feed.urllib.request.ProxyHandler({"http": proxy_url})),
                         ("\u76f4\u8fde", feed.urllib.request.ProxyHandler({}))]
                with patch.object(feed, "_attempt_plans", return_value=plans), \
                     patch.object(feed.urllib.request, "proxy_bypass", return_value=False):
                    self.assertEqual(feed.fetch_bytes(url, timeout=5), b"ok")
                self.assertEqual(seen, [("proxy", url), ("origin", "/package")])
            finally:
                for server in (proxy, origin):
                    server.shutdown()
                for worker in workers:
                    worker.join(2)
                    self.assertFalse(worker.is_alive())
