"""No-external-network acceptance for the actual portable executable."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time

from modules.network_policy import NetworkChannel
from .transport import ROOT, probe


def main():
    from modules.app_update_services.manifest import verify_manifest
    from modules.app_update_services.trust import PUBLIC_KEY
    from modules.app_update_services.catalog import parse_catalog
    from modules.capture_services.openwrt.common_bundle_manifest import verify_bundle_manifest
    from PySide6.QtNetwork import QSslSocket
    assert QSslSocket.supportsSsl(), "Frozen Qt TLS backend unavailable"
    seed = ROOT / "probe-data"
    release = verify_manifest((seed/"app_manifest.json").read_bytes(), PUBLIC_KEY)
    parse_catalog((seed/"app_catalog.json").read_bytes(), release)
    for kind in ("common", "capture"):
        verify_bundle_manifest((seed/(kind+"_manifest.json")).read_bytes(), kind=kind)
    data = b"portable-probe-smoke"*8192

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            try:
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                if self.path == "/stall":
                    self.wfile.write(data[:65536])
                    self.wfile.flush()
                    time.sleep(2)
                    self.wfile.write(data[65536:])
                else:
                    self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    route = NetworkChannel("direct", "Direct")
    url = "http://127.0.0.1:" + str(server.server_port)
    try:
        for engine in ("qt", "requests"):
            record, body = probe(url+"/ok", route,
                dict(name="smoke", connect=3, idle=3, total=15),
                limit=len(data), engine=engine, allow_loopback=True)
            assert record["outcome"] == "success", record
            assert body == data
            record, _ = probe(url+"/stall", route,
                dict(name="smoke", connect=3, idle=0.3, total=15),
                engine=engine, allow_loopback=True)
            assert record["reason"] == "transfer_interrupted", record
        expected = dict(size=len(data), sha256=hashlib.sha256(data).hexdigest())
        record, _ = probe(url+"/ok", route,
            dict(name="smoke", connect=3, idle=3, total=15),
            mode="full", limit=len(data), expected=expected, allow_loopback=True)
        assert record["verified"] is True, record
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    print(json.dumps(dict(success=True, signature_snapshots=4, qt=True, requests=True,
                          interrupted_stream=True, full_hash=True, tls_backend=QSslSocket.sslLibraryVersionString())))
    return 0
