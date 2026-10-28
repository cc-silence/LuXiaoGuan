"""Real socket interruption and signed-file resume regression coverage."""
import hashlib
import socket
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import requests
from modules.resumable_download import PartialDownload


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "payload.part"
        self.data = bytes(range(256)) * 1200
        self.sha = hashlib.sha256(self.data).hexdigest()

    def partial(self, cancel=lambda: False):
        return PartialDownload(self.path, len(self.data), self.sha, cancel)

    def server(self, mode="interrupt"):
        data, hits = self.data, []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                value = self.headers.get("Range")
                hits.append(value)
                offset = int(value[6:-1]) if value else 0
                interrupt = mode == "interrupt" and len(hits) == 1
                if mode == "ignore": offset = 0
                self.send_response(206 if offset else 200)
                self.send_header("Content-Length", str(len(data) - offset))
                if offset: self.send_header("Content-Range", f"bytes {offset}-{len(data)-1}/{len(data)}")
                self.end_headers()
                self.wfile.write(data[offset:offset+131072] if interrupt else data[offset:])
                self.wfile.flush()
                if interrupt:
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        def close():
            server.shutdown(); server.server_close(); worker.join(2)
        self.addCleanup(close)
        return "http://127.0.0.1:" + str(server.server_port) + "/payload", hits

    def test_disconnect_then_fresh_downloader_requests_only_missing_bytes(self):
        url, hits = self.server()
        first = self.partial(); first.prepare()
        with requests.Session() as session:
            session.trust_env = False
            with self.assertRaises(requests.RequestException):
                with session.get(url, headers=first.headers(), stream=True) as response:
                    first.receive(response, lambda _: None)
            saved = self.path.stat().st_size
            self.assertEqual(saved, 131072)
            second = self.partial()
            self.assertFalse(second.prepare())
            with session.get(url, headers=second.headers(), stream=True) as response:
                second.receive(response, lambda _: None)
        self.assertEqual(hits, [None, "bytes=131072-"])
        self.assertEqual(self.path.read_bytes(), self.data)
        self.assertTrue(self.partial().prepare())

    def test_server_ignoring_range_restarts_without_appending_full_file(self):
        url, hits = self.server("ignore")
        self.path.write_bytes(self.data[:100])
        part = self.partial(); part.prepare()
        with requests.Session() as session:
            session.trust_env = False
            with session.get(url, headers=part.headers(), stream=True) as response:
                part.receive(response, lambda _: None)
        self.assertEqual(hits, ["bytes=100-"])
        self.assertEqual(self.path.read_bytes(), self.data)

    def response(self, status=206, **headers):
        return SimpleNamespace(status_code=status, headers=headers, iter_content=lambda _: [self.data[100:]])

    def test_bad_range_encoding_length_and_http_error_leave_partial_untouched(self):
        cases = [self.response(**{"Content-Range": "bytes 99-307199/307200"}),
                 self.response(**{"Content-Range": "bytes 100-307199/307201"}),
                 self.response(**{"Content-Range": "bytes 100-307199/307200", "Content-Length": "1"}),
                 self.response(**{"Content-Encoding": "gzip"}), self.response(status=416),
                 self.response(status=302), self.response()]
        for response in cases:
            with self.subTest(headers=response.headers, status=response.status_code):
                self.path.write_bytes(self.data[:100]); part=self.partial(); part.prepare()
                with self.assertRaises(ValueError): part.receive(response, lambda _: None)
                self.assertEqual(self.path.read_bytes(), self.data[:100])

    def test_corrupt_complete_cache_and_corrupt_response_are_discarded(self):
        self.path.write_bytes(b"x" * len(self.data))
        part=self.partial(); self.assertFalse(part.prepare()); self.assertEqual(part.offset, 0)
        response=SimpleNamespace(status_code=200, headers={}, iter_content=lambda _: [b"x"*len(self.data)])
        with self.assertRaises(ValueError): part.receive(response, lambda _: None)
        self.assertFalse(self.path.exists())

    def test_cancel_and_deadline_keep_written_bytes(self):
        for deadline in (False, True):
            with self.subTest(deadline=deadline):
                cancelled=[False]; part=self.partial(lambda:cancelled[0]); part.prepare()
                def blocks(_):
                    yield self.data[:65536]
                    if not deadline: cancelled[0]=True
                    yield self.data[65536:]
                response=SimpleNamespace(status_code=200, headers={}, iter_content=blocks)
                def check():
                    if deadline and part.offset: raise TimeoutError()
                with self.assertRaises((InterruptedError, TimeoutError)):
                    part.receive(response, lambda _: None, check)
                self.assertEqual(self.path.read_bytes(), self.data[:65536])
                self.path.unlink()

    def test_offline_route_failure_keeps_bytes_and_next_invocation_resumes(self):
        from modules.capture_services.openwrt.common_bundle_download import _receive
        url,hits=self.server()
        args=(url,self.path,len(self.data),threading.Event(),lambda _:None)
        channels=[SimpleNamespace(proxy_url=None)]
        with self.assertRaises(RuntimeError):
            _receive(*args,time.monotonic()+10,channels,None,self.sha)
        _receive(*args,time.monotonic()+10,channels,None,self.sha)
        self.assertEqual(hits,[None,"bytes=131072-"])
        self.assertEqual(self.path.read_bytes(),self.data)

    def test_route_switch_resumes_within_the_same_offline_task(self):
        from modules.capture_services.openwrt.common_bundle_download import _receive
        url,hits=self.server()
        bridge=SimpleNamespace(http_proxy=lambda _:None)
        from contextlib import nullcontext
        channels=[SimpleNamespace(proxy_url="http://proxy:1"),SimpleNamespace(proxy_url=None)]
        with patch("modules.http_socks_bridge.ProxyBridgePool",return_value=nullcontext(bridge)):
            _receive(url,self.path,len(self.data),threading.Event(),lambda _:None,time.monotonic()+10,channels,None,self.sha)
        self.assertEqual(hits,[None,"bytes=131072-"])
        self.assertEqual(self.path.read_bytes(),self.data)

    def test_oversized_response_is_rejected_without_promoting_partial(self):
        part=self.partial();part.prepare()
        response=SimpleNamespace(status_code=200,headers={},iter_content=lambda _:[self.data+b"extra"])
        with self.assertRaises(ValueError):part.receive(response,lambda _:None)
        self.assertFalse(self.path.exists())

    def test_updater_resumes_signed_blob_after_failed_invocation(self):
        import json, zlib
        from tests import test_app_updates as fixture
        from modules.app_update_services.manifest import canonical,verify_manifest
        from modules.app_update_services.transfer import UpdateTransfer
        from modules.app_update_services.locations import CACHE_DIRECTORY
        # Incompressible enough to cross streaming chunk boundaries.
        import random
        content=random.Random(7).randbytes(350000)
        self.data=zlib.compress(content);self.sha=hashlib.sha256(self.data).hexdigest()
        url,hits=self.server()
        row=dict(path="LuXiaoGuan.exe",size=len(content),sha256=hashlib.sha256(content).hexdigest(),blob_size=len(self.data),blob_sha256=self.sha)
        catalog=dict(schema=1,version="2.0.9_alpha",resource_revision=0,files=[row],retired=[],migrations=[],history=[dict(version="2.0.9_alpha",notes="",published_at=fixture.PAYLOAD["published_at"])])
        raw=canonical(catalog);identity=hashlib.sha256(raw).hexdigest()
        manifest=fixture.signed(dict(fixture.PAYLOAD,catalog_url="https://fixture/catalog/"+identity+".json",catalog_size=len(raw),catalog_sha256=identity))
        release=verify_manifest(manifest,fixture.PUBLIC)
        root=Path(self.folder.name);stage=root/CACHE_DIRECTORY/identity
        plan=dict(files=[row],download_bytes=len(self.data))
        original_get=requests.Session.get
        def local_get(session, address, **kwargs):
            return original_get(session,url,**kwargs)
        with patch.object(requests.Session,"get",local_get):
            first=UpdateTransfer(root,catalog,manifest,channels=[SimpleNamespace(proxy_url=None,name="direct")])
            with self.assertRaises(RuntimeError): first._download_locked(plan,release,stage)
            part=stage/(self.sha+".part")
            self.assertEqual(part.stat().st_size,131072)
            second=UpdateTransfer(root,catalog,manifest,channels=[SimpleNamespace(proxy_url=None,name="direct")])
            second._download_locked(plan,release,stage)
        self.assertEqual(hits,[None,"bytes=131072-"])
        self.assertEqual((stage/(row["sha256"]+".file")).read_bytes(),content)
        self.assertFalse(part.exists())


class ResumeCacheTests(unittest.TestCase):
    def test_new_revision_discards_only_owned_partial_for_the_same_bundle_kind(self):
        from tests.test_common_offline_bundle import fixture
        import json
        from modules.capture_services.openwrt.bundle_resume_cache import select_resume_revision
        from modules.capture_services.openwrt.common_bundle_manifest import verify_bundle_manifest
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);old=fixture(root/"source");cache=root/"cache"
            owned=cache/"archives"/old.payload["sha256"];owned.mkdir(parents=True)
            part=owned/(old.payload["filename"]+".part");part.write_bytes(b"interrupted")
            (owned/"resume.json").write_bytes(old.envelope)
            (owned/"user.txt").write_bytes(b"keep")
            unknown=cache/"archives"/("a"*64);unknown.mkdir()
            (unknown/"download.part").write_bytes(b"unknown")
            selected=dict(old.payload,sha256="b"*64)
            with patch("modules.capture_services.openwrt.bundle_resume_cache.verify_bundle_manifest",side_effect=lambda raw,kind:verify_bundle_manifest(raw,old.public,kind=kind)):
                selected_archive=select_resume_revision(cache,selected,json.dumps(dict(payload=selected)).encode(),threading.Event(),"common")
            self.assertFalse(part.exists())
            self.assertEqual((owned/"user.txt").read_bytes(),b"keep")
            self.assertEqual((unknown/"download.part").read_bytes(),b"unknown")
            self.assertTrue((selected_archive.parent/"resume.json").is_file())
            self.assertRegex(selected_archive.parent.name,r"^[0-9]{8}-[0-9]{2,4}$")

    def test_invalid_ownership_receipt_cannot_delete_partial(self):
        from modules.capture_services.openwrt.bundle_resume_cache import select_resume_revision
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);owned=root/"archives"/("a"*64);owned.mkdir(parents=True)
            (owned/"resume.json").write_bytes(b"invalid")
            part=owned/"unknown.zip.part";part.write_bytes(b"keep")
            select_resume_revision(root,dict(sha256="b"*64,filename="new.zip",size=10),b"manifest",threading.Event(),"common")
            self.assertEqual(part.read_bytes(),b"keep")
