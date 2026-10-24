"""Additional installation audits: route fallback, bounded cache, cancellation."""
import hashlib
import io
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from modules.capture_services.openwrt import signal_package_feed as feed
from modules.capture_services.openwrt import apk_package_feed as apk


class DownloadRouteTests(unittest.TestCase):
    def test_temporary_proxy_http_error_retries_direct_and_closes_response(self):
        body = io.BytesIO(b"proxy error")
        error = HTTPError("https://fixture/package", 502, "Bad Gateway", {}, body)
        response = Mock(status=200, read1=Mock(side_effect=[b"verified bytes", b""]))
        opener = Mock()
        opener.open.side_effect = [error, unittest.mock.MagicMock(__enter__=Mock(return_value=response))]
        with patch.object(feed, "_attempt_plans", return_value=[("http://localhost:7890", object()), ("\u76f4\u8fde", object())]), patch.object(feed.urllib.request, "build_opener", return_value=opener):
            self.assertEqual(feed.fetch_bytes("https://fixture/package"), b"verified bytes")
        self.assertEqual(opener.open.call_count, 2)
        self.assertTrue(body.closed)

    def test_missing_package_is_not_retried_as_connection_failure(self):
        opener = Mock()
        opener.open.side_effect = HTTPError("https://fixture/package", 404, "Not Found", {}, io.BytesIO())
        with patch.object(feed, "_attempt_plans", return_value=[("proxy", object()), ("direct", object())]), patch.object(feed.urllib.request, "build_opener", return_value=opener):
            with self.assertRaisesRegex(RuntimeError, "HTTP 404"):
                feed.fetch_bytes("https://fixture/package")
        self.assertEqual(opener.open.call_count, 1)


class ApkCacheTests(unittest.TestCase):
    def package(self, name="tool"):
        return apk.ApkPackage(name, "1-r1", "https://fixture/", "base")

    def test_cached_packages_count_towards_total_limit(self):
        packages = [self.package("one"), self.package("two")]
        with TemporaryDirectory() as directory:
            for package in packages:
                (Path(directory) / package.filename).write_bytes(b"1234")
            hashes = {p.filename: hashlib.sha256(b"1234").hexdigest() for p in packages}
            fetch = Mock()
            with patch.object(apk, "_MAX_TOTAL_BYTES", 7):
                with self.assertRaises(RuntimeError):
                    apk.download_apk_packages(packages, hashes, directory, fetch=fetch)
            fetch.assert_not_called()

    def test_oversized_cache_is_not_read_into_memory(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "tool.apk"
            path.write_bytes(b"12345")
            with patch.object(apk, "_MAX_PACKAGE_BYTES", 4), patch.object(Path, "read_bytes", side_effect=AssertionError("unbounded read")):
                self.assertFalse(apk._valid_cached(path, hashlib.sha256(b"12345").hexdigest()))

    def test_existing_package_path_is_checked_before_write(self):
        from modules.platform_services import owned_cache
        package = self.package()
        with TemporaryDirectory() as directory:
            target = Path(directory) / package.filename
            target.write_bytes(b"keep")
            original = owned_cache.checked_path
            def reject_target(root, path):
                if Path(path) == target:
                    raise ValueError("simulated reparse point")
                return original(root, path)
            fetch = Mock(return_value=b"replacement")
            with patch.object(owned_cache, "checked_path", side_effect=reject_target):
                with self.assertRaises(ValueError):
                    apk.download_apk_packages([package], {package.filename: hashlib.sha256(b"replacement").hexdigest()}, directory, fetch=fetch)
            self.assertEqual(target.read_bytes(), b"keep")
            fetch.assert_not_called()


class SignalSpaceTests(unittest.TestCase):
    def test_online_signal_install_checks_complete_plan_before_writing(self):
        from modules.capture_services.openwrt.signal_manager import RouterSignalManager, SignalError
        manager = RouterSignalManager(Mock())
        manager.probe_dependencies = Mock(return_value=dict(lua=False,uci=True,ubus=True,jsonc=True,root=True,opkg=True,apk=False,overlay_kb=2048))
        manager._execute_stage = Mock(return_value="Installing lua (1) to root...")
        with patch("modules.capture_services.openwrt.package_space.check_plan_space", side_effect=SignalError("environment", "not enough space")) as check:
            with self.assertRaises(SignalError):
                manager.install_dependencies(Event())
        check.assert_called_once()
        self.assertFalse(any(c.args[0] == "router_signal_install" for c in manager._execute_stage.call_args_list))

    def test_signal_ipk_install_checks_real_installed_size(self):
        from modules.capture_services.openwrt.signal_manager import RouterSignalManager, SignalError
        from modules.capture_services.openwrt.dependency_manager import OpenWrtDependencyManager
        manager = RouterSignalManager(Mock())
        manager._execute_stage = Mock(return_value="Installing lua (1) to root...")
        manager._execute = Mock(return_value="")
        with patch("modules.capture_services.openwrt.package_space.ipk_package_versions", return_value={"lua":"1"}), patch("modules.capture_services.openwrt.package_space.ipk_installed_size", return_value=8*1024*1024), patch.object(OpenWrtDependencyManager, "_preflight", side_effect=SignalError("environment", "not enough space")) as check:
            with self.assertRaises(SignalError):
                manager._install_ipk_paths([Path("lua.ipk")], Event(), uploader=Mock())
        self.assertGreater(check.call_args.args[0], 8192)
        self.assertFalse(any(c.args[0] == "router_signal_offline_install" for c in manager._execute_stage.call_args_list))

    def test_signal_apk_installer_receives_shared_space_check(self):
        from modules.capture_services.openwrt.signal_manager import RouterSignalManager
        from modules.capture_services.openwrt.apk_offline import ApkOfflineInstaller
        manager = RouterSignalManager(Mock())
        manager.probe_dependencies = Mock(return_value=dict(lua=True,uci=True,ubus=True,jsonc=True))
        def inspect(installer, *args):
            self.assertTrue(callable(installer.space_check))
            with patch("modules.capture_services.openwrt.package_space.check_plan_space") as check:
                installer.space_check("(1/1) Installing lua (1-r1)")
                check.assert_called_once()
                self.assertEqual(check.call_args.kwargs["repositories"], "/tmp/winbox-apk/repos.list")
        with patch.object(ApkOfflineInstaller, "install", autospec=True, side_effect=inspect):
            manager._install_offline_apk(Mock(), None, Event(), uploader=Mock())


class DownloadBoundaryTests(unittest.TestCase):
    def test_cancel_during_response_stops_route_switch_and_closes_response(self):
        cancel = Event()
        def read(_size):
            cancel.set()
            return b"partial"
        response = unittest.mock.MagicMock(status=200)
        response.__enter__.return_value = response
        response.read1.side_effect = read
        opener = Mock()
        opener.open.return_value = response
        with patch.object(feed, "_attempt_plans", return_value=[("proxy", object()), ("direct", object())]), patch.object(feed.urllib.request, "build_opener", return_value=opener):
            with self.assertRaises(RuntimeError):
                feed.fetch_bytes("https://fixture/package", cancel_event=cancel)
        self.assertEqual(opener.open.call_count, 1)
        response.__exit__.assert_called_once()

    def test_apk_cancel_after_fetch_does_not_replace_cache(self):
        package = apk.ApkPackage("tool", "1-r1", "https://fixture/", "base")
        cancel = Event()
        def fetch(*args, **kwargs):
            cancel.set()
            return b"valid"
        with TemporaryDirectory() as directory:
            path = Path(directory) / package.filename
            path.write_bytes(b"previous")
            with self.assertRaises(RuntimeError):
                apk.download_apk_packages([package], {package.filename:hashlib.sha256(b"valid").hexdigest()}, directory, fetch=fetch, cancel_event=cancel)
            self.assertEqual(path.read_bytes(), b"previous")

    def test_apk_bad_hash_does_not_overwrite_previous_cache(self):
        package = apk.ApkPackage("tool", "1-r1", "https://fixture/", "base")
        with TemporaryDirectory() as directory:
            path = Path(directory) / package.filename
            path.write_bytes(b"previous")
            with self.assertRaises(RuntimeError):
                apk.download_apk_packages([package], {package.filename:hashlib.sha256(b"valid").hexdigest()}, directory, fetch=Mock(return_value=b"corrupt"))
            self.assertEqual(path.read_bytes(), b"previous")

    def test_apk_native_index_path_is_checked_before_upload(self):
        from modules.capture_services.openwrt.apk_offline import ApkOfflineInstaller
        from modules.platform_services import owned_cache
        upload = Mock()
        installer = ApkOfflineInstaller(Mock(), uploader=upload)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "indexes" / "base" / "packages.adb"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"previous")
            original = owned_cache.checked_path
            def reject_target(root, path):
                if Path(path) == target:
                    raise ValueError("simulated reparse point")
                return original(root, path)
            with patch.object(owned_cache, "checked_path", side_effect=reject_target):
                with self.assertRaises(ValueError):
                    installer._upload_indexes({"base":b"new"}, root)
            self.assertEqual(target.read_bytes(), b"previous")
        upload.assert_not_called()
