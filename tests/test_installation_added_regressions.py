"""Audit regressions introduced by recent installation refactors."""
import hashlib
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock,patch
from modules.capture_services.openwrt.package_cache import prepare_cache_directory
from modules.capture_services.openwrt.apk_package_feed import ApkPackage,download_apk_packages
from modules.capture_services.openwrt.apk_offline import ApkOfflineInstaller


class AddedCacheRegressionTests(unittest.TestCase):
    def test_unavailable_volume_stops_at_root_instead_of_looping(self):
        calls=[]
        def missing(path):
            calls.append(path)
            if len(calls)>12:
                raise AssertionError("ancestor search repeated an inaccessible root")
            return False
        with patch.object(Path,"exists",missing),patch.object(Path,"is_symlink",return_value=False):
            with self.assertRaises(OSError):
                prepare_cache_directory(Path("Z:/unavailable/cache"))
        self.assertEqual(len(calls),len(set(calls)))

    def test_previously_supported_relative_apk_cache_is_usable(self):
        package=ApkPackage("tool","1-r1","https://fixture/","base")
        raw=b"fixture package"
        with TemporaryDirectory() as folder:
            cwd=Path.cwd()
            try:
                os.chdir(folder)
                paths=download_apk_packages([package],{package.filename:hashlib.sha256(raw).hexdigest()},"cache",fetch=Mock(return_value=raw))
                self.assertEqual(paths[0].read_bytes(),raw)
                self.assertTrue(paths[0].is_relative_to(Path(folder)))
            finally:os.chdir(cwd)

    def test_relative_apk_index_directory_is_usable(self):
        with TemporaryDirectory() as folder:
            cwd=Path.cwd()
            try:
                os.chdir(folder)
                uploader=Mock()
                installer=ApkOfflineInstaller(Mock(),uploader=uploader)
                installer._upload_indexes({"base":b"index"},Path("cache"))
                self.assertEqual(Path(uploader.call_args.args[0]).read_bytes(),b"index")
            finally:os.chdir(cwd)


class SharedCacheBoundaryTests(unittest.TestCase):
    def test_ipk_receipt_uses_the_same_absolute_boundary_as_the_download(self):
        from modules.capture_services.openwrt import signal_package_feed as feed
        raw=b"fixture package"
        package=feed.FeedPackage("tool","1","tool_1_all.ipk",hashlib.sha256(raw).hexdigest(),len(raw),"https://fixture/tool",())
        with TemporaryDirectory() as folder:
            cwd=Path.cwd()
            try:
                os.chdir(folder)
                receipts=[]
                with patch.object(feed,"fetch_index",return_value={"tool":package}),patch.object(feed._cache_transaction,"receipts",receipts,create=True):
                    paths=feed.download_profile_packages(feed.DeviceProfile("21.02.1","mips_24kc"),set(),"cache",fetch=Mock(return_value=raw),wanted=("tool",))
                self.assertEqual(paths[0].read_bytes(),raw)
                self.assertEqual(receipts[0][0],Path(folder)/"cache")
                self.assertTrue(receipts[0][1].is_absolute())
            finally:os.chdir(cwd)


    def test_successful_relative_ipk_transaction_cleans_only_its_download(self):
        from modules.capture_services.openwrt import signal_package_feed as feed
        raw=b"fixture package"
        package=feed.FeedPackage("tool","1","tool_1_all.ipk",hashlib.sha256(raw).hexdigest(),len(raw),"https://fixture/tool",())
        @feed.offline_cache_transaction(lambda result:bool(result))
        def install(message=None):
            return feed.download_profile_packages(feed.DeviceProfile("21.02.1","mips_24kc"),set(),"cache",fetch=Mock(return_value=raw),wanted=("tool",))
        with TemporaryDirectory() as folder:
            cwd=Path.cwd()
            try:
                os.chdir(folder)
                Path("keep.txt").write_text("user file",encoding="utf-8")
                with patch.object(feed,"fetch_index",return_value={"tool":package}):paths=install()
                self.assertFalse(paths[0].exists())
                self.assertEqual(Path("keep.txt").read_text(encoding="utf-8"),"user file")
            finally:os.chdir(cwd)
