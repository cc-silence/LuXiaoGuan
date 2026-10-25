"""Alpha publication verifies immutable bytes before switching the protected channel."""
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from scripts.publishing import application as app

class AlphaPublicationTests(unittest.TestCase):
    def run_publication(self, *, corrupt=False, version="1.0.3_alpha"):
        with tempfile.TemporaryDirectory() as folder, patch("scripts.publishing.storage.ROOT", Path(folder)):
            archive=Path(folder)/"release.zip"; archive.write_bytes(b"archive")
            manifest=Path(folder)/"manifest.json"; manifest.write_bytes(b"signed")
            digest=app.sha256_file(archive)
            payload=dict(version=version,sha256=digest,size=7,url="https://example.invalid/archive")
            release=SimpleNamespace(sha256=digest,size=7,url=payload["url"],version=version,catalog_url="catalog",catalog_size=2)
            events=[]
            reads=0
            def receive(url,destination,*args,**kwargs):
                nonlocal reads
                if url.endswith("/manifest"):
                    reads+=1
                    if reads<3: raise ValueError("Download failed: HTTP 204")
                    return b"signed"
                if destination:
                    events.append("verify-zip")
                    destination.write_bytes(b"corrupt" if corrupt else b"archive")
                    return None
                return b"{}"
            def upload(path,key,kind): events.append(key)
            with patch.object(app,"cleanup_publishing") as local_cleanup, patch.object(app,"cleanup_retention") as cleanup, patch.object(app,"ensure_space"), patch("modules.platform_services.app_update_access.read_code",return_value="private"), patch.object(app,"_receive",side_effect=receive), patch.object(app,"prepare",return_value=(archive,manifest,payload,{})), patch.object(app,"upload",side_effect=upload), patch.object(app,"verify_manifest",return_value=release), patch.object(app,"parse_catalog",return_value={}):
                if corrupt or version=="1.0.3":
                    with self.assertRaises(ValueError): app.publish_alpha(archive)
                    self.assertNotIn("channel/manifest.json",events)
                    cleanup.assert_not_called()
                    local_cleanup.assert_not_called()
                else:
                    result=app.publish_alpha(archive)
                    self.assertEqual(result["status"],"published-and-client-verified")
                    cleanup.assert_called_once()
                    local_cleanup.assert_called_once()
                    self.assertLess(events.index("verify-zip"),events.index("channel/manifest.json"))
                    self.assertNotIn("channel/stable.json",events)
    def test_first_alpha_uses_protected_channel(self): self.run_publication()
    def test_corrupt_public_archive_never_switches(self): self.run_publication(corrupt=True)
    def test_stable_version_is_rejected(self): self.run_publication(version="1.0.3")


class StablePublicationTests(unittest.TestCase):
    def test_public_zip_checked_before_formal_channel_switch(self):
        for corrupt in [False, True]:
            with self.subTest(corrupt=corrupt), tempfile.TemporaryDirectory() as folder, patch("scripts.publishing.storage.ROOT", Path(folder)):
                archive=Path(folder)/'release.zip';archive.write_bytes(b'archive')
                manifest=Path(folder)/'manifest.json';manifest.write_bytes(b'signed')
                digest=app.sha256_file(archive)
                payload=dict(version='1.0.11',sha256=digest,size=7,url='https://example.invalid/archive')
                release=SimpleNamespace(version='1.0.11',sha256=digest,catalog_url='catalog',catalog_size=2)
                events=[]
                def receive(url,destination,*args,**kwargs):
                    if destination:
                        events.append('verify-zip');destination.write_bytes(b'corrupt' if corrupt else b'archive');return
                    return b'signed' if url.endswith('/manifest') else b'{}'
                with patch.object(app,'cleanup_publishing') as local_cleanup, patch.object(app,'ensure_space'), patch.object(app,'cleanup_retention') as cleanup, patch.object(app,'prepare',return_value=(archive,manifest,payload,{})), patch.object(app,'upload',side_effect=lambda p,k,t:events.append(k)), patch.object(app,'_receive',side_effect=receive), patch.object(app,'verify_manifest',return_value=release), patch.object(app,'parse_catalog',return_value={}):
                    if corrupt:
                        with self.assertRaises(ValueError):app.publish(archive,'notes')
                        self.assertNotIn('channel/stable.json',events);cleanup.assert_not_called();local_cleanup.assert_not_called()
                    else:
                        app.publish(archive,'notes')
                        self.assertLess(events.index('verify-zip'),events.index('channel/stable.json'))
                        cleanup.assert_called_once()
                        local_cleanup.assert_called_once()


class AlphaQuotaPublicationTests(unittest.TestCase):
    def scenario(self, *, resumed=False, failure=None):
        from contextlib import ExitStack
        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
            root = Path(folder)
            archive, manifest = root / "release.zip", root / "manifest.json"
            archive.write_bytes(b"archive"); manifest.write_bytes(b"signed")
            digest = app.sha256_file(archive)
            payload = dict(version="1.0.3_alpha", sha256=digest, size=7,
                           url="https://example.invalid/archive")
            prepared = (archive, manifest, payload, {})
            release = SimpleNamespace(sha256=digest, size=7, url=payload["url"],
                version=payload["version"], catalog_url="catalog", catalog_size=2)
            record = {"previous_manifest": "b2xk"}
            events = []
            def receive(url, destination, *args, **kwargs):
                if destination:
                    events.append("verify-zip"); destination.write_bytes(b"archive"); return
                if url.endswith("/manifest"):
                    if "channel/manifest.json" in events: return b"signed"
                    if resumed: self.fail("A resumed publication queried the maintenance endpoint")
                    return b"old"
                return b"{}"
            def mocked(name, **kwargs):
                return stack.enter_context(patch.object(app, name, **kwargs))
            stack.enter_context(patch("scripts.publishing.storage.ROOT", root))
            stack.enter_context(patch.object(app, "ROOT", root))
            stack.enter_context(patch("modules.platform_services.app_update_access.read_code", return_value="synthetic"))
            mocked("_receive", side_effect=receive)
            mocked("upload", side_effect=lambda path,key,kind: events.append(key))
            mocked("verify_manifest", return_value=release)
            mocked("parse_catalog", return_value={})
            mocked("cleanup_publishing"); cleanup = mocked("cleanup_retention")
            prepare = mocked("prepare", return_value=prepared)
            ensure = mocked("ensure_space", side_effect=failure or app.StorageQuotaError("synthetic quota"))
            resume = mocked("resume_alpha_space", return_value=record if resumed else None)
            saved = mocked("prepared_alpha", return_value=prepared)
            reserve = mocked("reserve_alpha_space", return_value=record)
            assertion = mocked("assert_alpha_channel", side_effect=lambda rec: events.append("check-channel"))
            complete = mocked("complete_alpha_space", side_effect=lambda *a,**k: events.append("complete"))
            if failure:
                with self.assertRaisesRegex(ValueError, "synthetic validation"):
                    app.publish_alpha(archive)
                reserve.assert_not_called(); complete.assert_not_called(); cleanup.assert_not_called()
                self.assertEqual(events, [])
                return
            result = app.publish_alpha(archive)
            self.assertEqual(result["status"], "published-and-client-verified")
            resume.assert_called_once()
            assertion.assert_called_once_with(record)
            complete.assert_called_once_with(record, root=root)
            self.assertLess(events.index("verify-zip"), events.index("check-channel"))
            self.assertLess(events.index("check-channel"), events.index("channel/manifest.json"))
            self.assertLess(events.index("channel/manifest.json"), events.index("complete"))
            if resumed:
                prepare.assert_not_called(); ensure.assert_not_called(); reserve.assert_not_called()
                saved.assert_called_once_with(record)
            else:
                prepare.assert_called_once(); ensure.assert_called_once(); reserve.assert_called_once()
                self.assertEqual(reserve.call_args.args[-1], b"old")
                saved.assert_not_called()

    def test_quota_exception_uses_retirement_and_verifies_before_switch(self):
        self.scenario()

    def test_resume_uses_original_signed_preparation_without_regeneration(self):
        self.scenario(resumed=True)

    def test_validation_error_never_invokes_quota_retirement(self):
        self.scenario(failure=ValueError("synthetic validation"))
