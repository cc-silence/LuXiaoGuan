"""Real loopback slow-response regression tests; no external network required."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from modules.network_policy import NetworkChannel
from scripts.slow_network_probe.transport import probe
from scripts.slow_network_probe.runner import Runner

DATA = b"diagnostic-test-data" * 8192


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        self.server.started.set()
        try:
            if self.path.startswith("/headers"):
                time.sleep(0.65)
            if self.path.startswith("/denied"):
                self.send_response(403)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(DATA)))
            self.end_headers()
            if self.path.startswith("/stall"):
                self.wfile.write(DATA[:128])
                self.wfile.flush()
                time.sleep(2)
                self.wfile.write(DATA[128:])
            else:
                self.wfile.write(DATA)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


class SlowNetworkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server.daemon_threads = True
        cls.server.started = threading.Event()
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = "http://127.0.0.1:" + str(cls.server.server_port)
        cls.route = NetworkChannel("direct", "Direct")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def call(self, path="/ok", idle=2, total=8, engine="requests", **kwargs):
        return probe(self.base+path, self.route,
                     dict(name="test", connect=2, idle=idle, total=total),
                     engine=engine, allow_loopback=True, **kwargs)

    def test_slow_headers_baseline_fails_longer_wait_succeeds(self):
        for engine in ("requests", "qt"):
            with self.subTest(engine=engine):
                failed, _ = self.call("/headers", idle=0.15, engine=engine)
                self.assertEqual(failed["reason"], "response_timeout")
                success, body = self.call("/headers", idle=2, engine=engine, limit=len(DATA))
                self.assertEqual(success["outcome"], "success")
                self.assertEqual(body, DATA)

    def test_interrupted_stream_preserves_received_bytes(self):
        for engine in ("requests", "qt"):
            with self.subTest(engine=engine):
                record, _ = self.call("/stall", idle=0.2, engine=engine)
                self.assertEqual(record["reason"], "transfer_interrupted")
                self.assertEqual(record["received_bytes"], 128)

    def test_sample_and_full_hash_and_bad_hash(self):
        expected = dict(size=len(DATA), sha256=hashlib.sha256(DATA).hexdigest())
        record, _ = self.call(mode="sample", limit=65536, expected=expected)
        self.assertEqual(record["received_bytes"], 65536)
        self.assertEqual(record["outcome"], "success")
        self.assertFalse(record.get("verified", False))
        record, _ = self.call(mode="full", limit=len(DATA), expected=expected)
        self.assertTrue(record["verified"])
        record, _ = self.call(mode="full", limit=len(DATA),
                              expected={**expected, "sha256": "0"*64})
        self.assertEqual(record["reason"], "hash_or_size_mismatch")

    def test_size_limit_and_metadata_privacy(self):
        for engine in ("requests", "qt"):
            with self.subTest(engine=engine):
                record, body = self.call(limit=1024, engine=engine,
                                         headers={"X-RouterManager-Test-Code": "secret-test-code"})
                self.assertEqual(record["reason"], "size_limit")
                self.assertNotIn("secret-test-code", json.dumps(record))
                self.assertNotIn(DATA[:20].decode(), json.dumps(record))

    def test_deadline_cancel_cleanup_and_next_request(self):
        import scripts.slow_network_probe.transport as transport
        original = tempfile.TemporaryDirectory
        for engine in ("requests", "qt"):
            for stop in ("deadline", "cancel"):
                with self.subTest(engine=engine, stop=stop):
                    directories = []
                    def make_directory(**kwargs):
                        directory = original(**kwargs)
                        directories.append(Path(directory.name))
                        return directory
                    cancel = threading.Event()
                    self.server.started.clear()
                    cancelled_at = []
                    def trigger():
                        self.server.started.wait(10)
                        cancelled_at.append(time.monotonic())
                        cancel.set()
                    thread = None
                    if stop == "cancel":
                        thread = threading.Thread(target=trigger, daemon=True)
                        thread.start()
                    start = time.monotonic()
                    with patch.object(transport.tempfile, "TemporaryDirectory", make_directory):
                        record, _ = self.call("/stall", idle=60,
                            total=0.8 if stop == "deadline" else 15,
                            cancel=cancel, engine=engine)
                    if stop == "deadline":
                        self.assertLess(time.monotonic()-start, 3)
                    else:
                        self.assertTrue(cancelled_at)
                        self.assertLess(time.monotonic()-cancelled_at[0], 2)
                    self.assertEqual(record["reason"], "total_deadline" if stop == "deadline" else "cancelled")
                    self.assertTrue(all(not path.exists() for path in directories))
                    if thread:
                        thread.join(1)
                    record, _ = self.call(limit=len(DATA), engine=engine)
                    self.assertEqual(record["outcome"], "success")

    def test_http_errors_are_not_network_errors(self):
        for engine in ("requests", "qt"):
            with self.subTest(engine=engine):
                record, _ = self.call("/denied", engine=engine)
                self.assertEqual(record["reason"], "http_status")
                self.assertEqual(record["status"], 403)

    def test_proxy_password_not_exported(self):
        route = NetworkChannel("http_proxy", "Proxy", "http://probe-user:private-password@127.0.0.1:1")
        record, _ = probe(self.base, route, dict(name="test", connect=0.2, idle=0.2, total=20),
                          headers={"X-RouterManager-Test-Code": "private-code"}, allow_loopback=True)
        report = json.dumps(record)
        for secret in ("probe-user", "private-password", "private-code"):
            self.assertNotIn(secret, report)
        self.assertEqual(record["reason"], "proxy_failed")

    def test_https_required(self):
        with self.assertRaises(ValueError):
            probe(self.base, self.route, dict(name="test", connect=1, idle=1, total=2))

    def test_no_full_download_before_reachable_sample(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = Runner(Path(directory)/"report.json", threading.Event())
            item = dict(url="https://example.test/archive", size=100000, sha256="0"*64)
            with patch.object(runner, "request", return_value=({"outcome": "failed"}, None)) as call:
                runner.artifact("archive", item, self.route, "offline_download")
            self.assertEqual(call.call_count, 2)


if __name__ == "__main__":
    unittest.main()
