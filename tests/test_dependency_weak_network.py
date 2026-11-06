"""Real wire regressions for progress-aware dependency downloads."""
import time
import unittest
import urllib.request
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
from unittest.mock import patch
from modules.capture_services.openwrt import dependency_http, signal_package_feed as feed


@contextmanager
def slow_server(delay, chunks=8, chunked=False):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            if chunked:
                self.send_header("Transfer-Encoding", "chunked")
            else:
                self.send_header("Content-Length", str(chunks))
            self.end_headers()
            for _ in range(chunks):
                try:
                    self.wfile.write(b"1\r\nx\r\n" if chunked else b"x")
                    self.wfile.flush()
                except (ConnectionError, OSError):
                    break
                time.sleep(delay)
            if chunked:
                try:
                    self.wfile.write(b"0\r\n\r\n")
                    self.wfile.flush()
                except (ConnectionError, OSError):
                    pass
        def log_message(self, *args):
            pass
    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        worker = Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        worker.start()
        try:
            yield "http://127.0.0.1:%d/package" % server.server_port
        finally:
            server.shutdown()
            worker.join(2)


class WeakNetworkTests(unittest.TestCase):
    def setUp(self):
        routes = [("direct", urllib.request.ProxyHandler({}))]
        patcher = patch.object(feed, "_attempt_plans", return_value=routes)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_continuous_body_outlives_connection_budget(self):
        with slow_server(.04) as url:
            started = time.monotonic()
            self.assertEqual(feed.fetch_bytes(url, timeout=.15), b"x" * 8)
            self.assertGreater(time.monotonic() - started, .25)

    def test_idle_polls_do_not_poison_plain_or_chunked_streams(self):
        for chunked in (False, True):
            with self.subTest(chunked=chunked), slow_server(.08, chunked=chunked) as url, \
                 patch.object(dependency_http, "CANCEL_CHECK_INTERVAL", .02), \
                 patch.object(dependency_http, "BODY_IDLE_TIMEOUT", .4):
                self.assertEqual(feed.fetch_bytes(url, timeout=.15), b"x" * 8)

    def test_idle_body_is_rejected_and_never_accepted_as_partial(self):
        with slow_server(.15) as url, patch.object(dependency_http, "BODY_IDLE_TIMEOUT", .04):
            with self.assertRaises(RuntimeError):
                feed.fetch_bytes(url, timeout=1)

    def test_cancel_during_active_download(self):
        cancel = Event()
        original = feed._check_cancel
        checks = [0]
        def check(event):
            checks[0] += 1
            if checks[0] >= 10:
                cancel.set()
            original(event)
        with slow_server(.02, chunks=30) as url, patch.object(feed, "_check_cancel", side_effect=check):
            with self.assertRaises(RuntimeError):
                feed.fetch_bytes(url, timeout=.15, cancel_event=cancel)
        self.assertTrue(cancel.is_set())

    def test_cancel_interrupts_idle_body_without_observer_threads(self):
        import threading
        cancel = Event()
        received = Event()
        blocked_read = Event()
        release = Event()
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"x")
                self.wfile.flush()
                received.set()
                release.wait(2)
            def log_message(self, *args):
                pass
        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            server_thread = Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
            server_thread.start()
            outcome = []
            original_read_body = feed._read_body
            def read_body(response, *args):
                original_read = response.read1
                reads = [0]
                def read(*options):
                    reads[0] += 1
                    if reads[0] == 2:
                        blocked_read.set()
                    return original_read(*options)
                response.read1 = read
                return original_read_body(response, *args)
            def download():
                try:
                    with patch.object(feed, "_read_body", side_effect=read_body):
                        feed.fetch_bytes("http://127.0.0.1:%d/package" % server.server_port,
                                         timeout=1, cancel_event=cancel)
                except RuntimeError:
                    outcome.append("cancelled")
            worker = Thread(target=download, daemon=True)
            worker.start()
            try:
                self.assertTrue(received.wait(1))
                self.assertTrue(blocked_read.wait(1))
                started = time.monotonic()
                cancel.set()
                worker.join(.8)
                self.assertFalse(worker.is_alive(), "Cancellation waited for the idle timeout")
                self.assertLess(time.monotonic() - started, .8)
                self.assertEqual(outcome, ["cancelled"])
                self.assertFalse(any(t.name == "Dependency-download-cancel" for t in threading.enumerate()))
            finally:
                cancel.set()
                release.set()
                server.shutdown()
                server_thread.join(2)
                worker.join(2)

    def test_success_with_cancel_event_uses_no_observer_threads(self):
        import threading
        with slow_server(.01) as url:
            self.assertEqual(feed.fetch_bytes(url, timeout=1, cancel_event=Event()), b"x" * 8)
        self.assertFalse(any(t.name == "Dependency-download-cancel" for t in threading.enumerate()))

    def test_cancel_during_final_body_after_skipped_redirect_body(self):
        received = Event()
        final_response_started = Event()
        blocked_read = Event()
        release = Event()
        cancel = Event()
        destinations = []
        readers = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                destinations.append(self.path)
                if self.path == "/start":
                    self.send_response(302)
                    self.send_header("Location", "/final")
                    self.send_header("Content-Length", "1000000000")
                    self.end_headers()
                    release.wait(3)
                    return
                final_response_started.set()
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"x")
                self.wfile.flush()
                received.set()
                release.wait(3)
            def log_message(self, *args):
                pass
        original_readinto = dependency_http._StreamingReader.readinto
        def readinto(reader, buffer):
            if reader not in readers:
                readers.append(reader)
            if reader.streaming and final_response_started.is_set():
                blocked_read.set()
            return original_readinto(reader, buffer)
        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            server_thread = Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
            server_thread.start()
            outcome = []
            def download():
                try:
                    feed.fetch_bytes("http://127.0.0.1:%d/start" % server.server_port,
                                     timeout=1, cancel_event=cancel)
                except RuntimeError:
                    outcome.append("cancelled")
            with patch.object(dependency_http._StreamingReader, "readinto", readinto):
                worker = Thread(target=download, daemon=True)
                worker.start()
                try:
                    self.assertTrue(received.wait(1))
                    self.assertTrue(blocked_read.wait(1))
                    cancel.set()
                    worker.join(.8)
                    self.assertFalse(worker.is_alive(), "Final response after redirect ignored cancellation")
                    self.assertEqual(outcome, ["cancelled"])
                    self.assertEqual(destinations, ["/start", "/final"])
                    self.assertEqual(len(readers), 2)
                    for reader in readers:
                        self.assertTrue(reader.closed)
                        self.assertTrue(reader.owner.closed)
                        self.assertEqual(reader.connection_socket.fileno(), -1)
                finally:
                    cancel.set()
                    release.set()
                    worker.join(3)
                    server.shutdown()
                    server_thread.join(2)

    def test_cancel_context_is_isolated_and_restored(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        barrier = Barrier(2)
        def context(event):
            with dependency_http.download_cancellation(event):
                barrier.wait(timeout=2)
                self.assertIs(dependency_http._request_cancel_event.get(), event)
            self.assertIsNone(dependency_http._request_cancel_event.get())
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(context, Event()) for _ in range(2)]
            for future in futures:
                future.result(timeout=3)
        self.assertIsNone(dependency_http._request_cancel_event.get())


class CompressedIndexTests(unittest.TestCase):
    def fetch(self, fetch, limit=1024):
        from modules.capture_services.openwrt.package_index_transport import fetch_package_index
        return fetch_package_index("https://fixture/Packages", fetch, limit=limit)

    def test_gzip_is_preferred_and_decoded(self):
        import gzip
        from unittest.mock import Mock
        fetch = Mock(return_value=gzip.compress(b"Package: tool\n"))
        self.assertEqual(self.fetch(fetch), b"Package: tool\n")
        self.assertEqual(fetch.call_args.args[0], "https://fixture/Packages.gz")
        self.assertEqual(fetch.call_count, 1)

    def test_missing_compressed_index_can_use_plain_index(self):
        from unittest.mock import Mock
        for status in (404, 410):
            fetch = Mock(side_effect=[dependency_http.DownloadHTTPError(status), b"plain"])
            self.assertEqual(self.fetch(fetch), b"plain")
            self.assertEqual(fetch.call_args.args[0], "https://fixture/Packages")

    def test_transport_failure_is_not_silently_reinterpreted_as_missing_format(self):
        from unittest.mock import Mock
        fetch = Mock(side_effect=RuntimeError("connection failed"))
        with self.assertRaises(RuntimeError):
            self.fetch(fetch)
        self.assertEqual(fetch.call_count, 1)

    def test_corrupt_truncated_trailing_and_oversized_gzip_are_rejected(self):
        import gzip
        from unittest.mock import Mock
        good = gzip.compress(b"x" * 100)
        for raw in (good[:-1], good + b"trailing", good[:-8] + b"bad-data", b"plain"):
            with self.subTest(raw=raw), self.assertRaises(RuntimeError):
                self.fetch(Mock(return_value=raw))
        with self.assertRaises(RuntimeError):
            self.fetch(Mock(return_value=good), limit=99)
        self.assertEqual(self.fetch(Mock(return_value=good), limit=100), b"x" * 100)

    def test_active_failed_body_does_not_consume_next_route_connection_budget(self):
        from unittest.mock import MagicMock, Mock
        clock = [0.0]
        first = MagicMock(status=200, headers={})
        first.__enter__.return_value = first
        def slow_body(*args):
            clock[0] += 1000
            raise TimeoutError("idle")
        first.read1.side_effect = slow_body
        second = MagicMock(status=200, headers={})
        second.__enter__.return_value = second
        second.read1.side_effect = [b"ok", b""]
        opener = Mock()
        opener.open.side_effect = [first, second]
        with patch.object(feed, "_attempt_plans", return_value=[("proxy", object()), ("direct", object())]), \
             patch.object(feed.urllib.request, "build_opener", return_value=opener), \
             patch.object(feed.time, "monotonic", side_effect=lambda: clock[0]):
            self.assertEqual(feed.fetch_bytes("https://fixture/package", timeout=5), b"ok")
        first.__exit__.assert_called_once()
        self.assertEqual(opener.open.call_count, 2)
