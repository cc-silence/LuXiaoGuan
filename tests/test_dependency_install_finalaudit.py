"""Third installation audit: cancellation, native plans and retry boundaries."""
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import Mock, patch
from modules.capture_services.openwrt.apk_offline import ApkOfflineInstaller
from modules.capture_services.openwrt.apk_package_feed import ApkPackage
from modules.capture_services.openwrt.signal_manager import SignalError


class ApkTransferCancellationTests(unittest.TestCase):
    def test_cancel_after_package_upload_stops_same_feed_and_install(self):
        cancel = Event()
        upload = Mock(side_effect=lambda *_: cancel.set())
        installer = ApkOfflineInstaller(Mock(), uploader=upload)
        packages = [ApkPackage(name, "1-r1", "https://fixture/", "base") for name in ("one", "two")]
        with self.assertRaises(SignalError) as caught:
            installer._upload_packages(packages, [Path(p.filename) for p in packages], cancel_event=cancel)
        self.assertEqual(caught.exception.code, "cancelled")
        self.assertEqual(upload.call_count, 1)

    def test_cancel_after_index_upload_stops_following_indexes(self):
        cancel = Event()
        upload = Mock(side_effect=lambda *_: cancel.set())
        installer = ApkOfflineInstaller(Mock(), uploader=upload)
        with TemporaryDirectory() as folder:
            with self.assertRaises(SignalError):
                installer._upload_indexes({"base":b"index", "packages":b"index"}, Path(folder), cancel_event=cancel)
            self.assertFalse((Path(folder)/"indexes"/"packages").exists())
        self.assertEqual(upload.call_count, 1)

    def test_cancellation_from_progress_callback_prevents_upload(self):
        cancel = Event()
        installer = ApkOfflineInstaller(Mock(), tell=lambda *_:cancel.set(), uploader=Mock())
        package = ApkPackage("tool", "1-r1", "https://fixture/", "base")
        with self.assertRaises(SignalError):
            installer._upload_packages([package], [Path(package.filename)], cancel_event=cancel)
        installer.uploader.assert_not_called()


class NativePlanTests(unittest.TestCase):
    def test_online_and_offline_reject_official_pinning_change(self):
        from modules.capture_services.openwrt.package_manager import PackageManager
        from modules.capture_services.openwrt.apk_package_feed import parse_simulate_plan
        text = "(1/2) Installing tool (1-r1)\n(2/2) Updating pinning libc@edge (1-r1)\nOK: 10 MiB"
        for verify in (lambda:PackageManager("apk").verify_plan(text, ("tool",)), lambda:parse_simulate_plan(text)):
            with self.subTest(verify=verify), self.assertRaises(ValueError):
                verify()

    def test_local_noop_must_not_hide_unrecognized_numbered_action(self):
        installer = ApkOfflineInstaller(lambda *_:"(1/1) Updating pinning libc@edge (1-r1)\nOK: 10 MiB")
        installer.local_only = True
        with self.assertRaises(ValueError):
            installer._simulate(("tool",))

    def test_space_accounting_accepts_the_same_whitespace_as_plan_validation(self):
        from modules.capture_services.openwrt.package_space import planned_packages
        text = "( 1/12)   Installing tool (1-r1)"
        self.assertEqual(planned_packages("apk", text), {"tool":"1-r1"})

    def test_duplicate_package_versions_are_not_collapsed(self):
        from modules.capture_services.openwrt.package_space import planned_packages
        text = "Installing tool (wrong) to root...\nInstalling tool (correct) to root..."
        with self.assertRaises(SignalError):
            planned_packages("opkg", text)


class CacheCreationBoundaryTests(unittest.TestCase):
    def test_parent_traversal_is_rejected_before_creating_any_directory(self):
        from modules.capture_services.openwrt.package_cache import prepare_cache_directory
        with TemporaryDirectory() as folder:
            root = Path(folder)
            with self.assertRaises(ValueError):
                prepare_cache_directory(root/"new"/".."/"escaped")
            self.assertFalse((root/"new").exists())
            self.assertFalse((root/"escaped").exists())

    def test_cache_target_traversal_is_rejected_before_creating_parent(self):
        from modules.capture_services.openwrt.package_cache import checked_cache_target
        with TemporaryDirectory() as folder:
            root = Path(folder)/"cache"
            root.mkdir()
            with self.assertRaises(ValueError):
                checked_cache_target(root, root/".."/"escaped"/"file.apk")
            self.assertFalse((Path(folder)/"escaped").exists())


class TruncatedDownloadTests(unittest.TestCase):
    def test_short_http_response_switches_route_instead_of_accepting_partial_index(self):
        from modules.capture_services.openwrt import signal_package_feed as feed
        from unittest.mock import MagicMock
        first = MagicMock(status=200, headers={"Content-Length":"8"})
        first.__enter__.return_value=first
        first.read1.side_effect=[b"half",b""]
        second = MagicMock(status=200, headers={"Content-Length":"8"})
        second.__enter__.return_value=second
        second.read1.side_effect=[b"complete",b""]
        opener=Mock()
        opener.open.side_effect=[first,second]
        with patch.object(feed,"_attempt_plans",return_value=[("proxy",object()),("direct",object())]), patch.object(feed.urllib.request,"build_opener",return_value=opener):
            self.assertEqual(feed.fetch_bytes("https://fixture/Packages"), b"complete")
        self.assertEqual(opener.open.call_count,2)
        first.__exit__.assert_called_once()

    def test_http_incomplete_read_can_use_next_route(self):
        from http.client import IncompleteRead
        from modules.capture_services.openwrt import signal_package_feed as feed
        from unittest.mock import MagicMock
        first=MagicMock(status=200,headers={})
        first.__enter__.return_value=first
        first.read1.side_effect=IncompleteRead(b"partial",8)
        second=MagicMock(status=200,headers={})
        second.__enter__.return_value=second
        second.read1.side_effect=[b"complete",b""]
        opener=Mock()
        opener.open.side_effect=[first,second]
        with patch.object(feed,"_attempt_plans",return_value=[("proxy",object()),("direct",object())]), patch.object(feed.urllib.request,"build_opener",return_value=opener):
            self.assertEqual(feed.fetch_bytes("https://fixture/package"),b"complete")

    def test_oversized_declared_response_is_rejected_before_reading(self):
        from modules.capture_services.openwrt import signal_package_feed as feed
        from unittest.mock import MagicMock
        response=MagicMock(headers={"Content-Length":"100"})
        response.read1.side_effect=[b"tiny",b""]
        with self.assertRaises(RuntimeError):
            feed._read_body(response,16,None,float("inf"))
        response.read1.assert_not_called()


class CommandUploadMemoryTests(unittest.TestCase):
    def test_command_upload_streams_and_preserves_hash_and_old_router_chunk_limit(self):
        import base64,hashlib,shlex
        from modules.capture_services.openwrt.dependency_manager import OpenWrtDependencyManager
        manager=OpenWrtDependencyManager(Mock())
        transmitted=[]
        sizes=[]
        def execute(name,command,*args):
            sizes.append(len(command))
            if name=="dependency_push_part":
                transmitted.append(shlex.split(command)[2])
            if name=="dependency_push_verify":
                blob=base64.b64decode("".join(transmitted),validate=True)
                return hashlib.sha256(blob).hexdigest()+"  /tmp/package.ipk"
            return ""
        manager._execute=Mock(side_effect=execute)
        payload=bytes(range(256))*1025
        with TemporaryDirectory() as folder:
            path=Path(folder)/"package.ipk"
            path.write_bytes(payload)
            with patch.object(Path,"read_bytes",side_effect=AssertionError("whole package read")):
                manager._exec_push(path,"/tmp/package.ipk",Event())
        self.assertEqual(base64.b64decode("".join(transmitted),validate=True),payload)
        self.assertLess(max(sizes),9*1024)

    def test_failed_cache_transaction_does_not_hold_lock_or_route_state(self):
        from modules.capture_services.openwrt import signal_package_feed as feed
        @feed.offline_cache_transaction(lambda value:bool(value))
        def failing(message=None):
            feed._download_state.get()["preferred"]="old-route"
            raise SignalError("transport","disconnected")
        with self.assertRaises(SignalError):
            failing()
        self.assertIsNone(feed._download_state.get())
        @feed.offline_cache_transaction(lambda value:bool(value))
        def retry(message=None):
            self.assertIsNone(feed._download_state.get()["preferred"])
            return True
        self.assertTrue(retry())


class FinalWorkflowTests(unittest.TestCase):
    def test_both_apk_sources_stop_before_install_and_cleanup_after_upload_cancel(self):
        from types import SimpleNamespace
        from modules.capture_services.openwrt import apk_package_feed as feed
        device=SimpleNamespace(version="25.12.0",package_architecture="mipsel_24kc",package_manager="apk",target="ramips/mt7621")
        packages=[ApkPackage(name,"1-r1","https://fixture/","base") for name in ("one","two")]
        plan="(1/2) Installing one (1-r1)\n(2/2) Installing two (1-r1)"
        for route in ("computer","local"):
            with self.subTest(route=route),TemporaryDirectory() as folder:
                root=Path(folder)
                cancel=Event()
                def upload(local,remote):
                    if remote.endswith(".apk"):
                        cancel.set()
                cleanup=Mock(return_value="")
                stage=Mock(side_effect=lambda name,*_:plan if name=="apk_simulate_plan" else "")
                uploader=Mock(side_effect=upload)
                installer=ApkOfflineInstaller(stage,uploader=uploader,cleanup_execute=cleanup)
                with self.assertRaises(SignalError) as caught:
                    if route=="computer":
                        with patch.object(feed,"download_adb_files",return_value={"base":b"index"}),patch.object(feed,"merge_index",return_value={p.name:p for p in packages}),patch.object(feed,"fetch_sha256sums",return_value={}),patch.object(feed,"download_apk_packages",return_value=[root/p.filename for p in packages]):
                            installer.install(("one",),device,root,cancel)
                    else:
                        index=root/"index.adb"
                        index.write_bytes(b"index")
                        repo=Mock()
                        repo.object_path.return_value=index
                        repo.materialize.side_effect=lambda row,*_:root/(row["name"]+".apk")
                        profile={"indexes":[{"feed":"base"}],"packages":[dict(name=p.name,version=p.version,feed=p.feed) for p in packages]}
                        installer.install_local(("one",),profile,repo,root,cancel)
                self.assertEqual(caught.exception.code,"cancelled")
                self.assertEqual(sum(c.args[1].endswith(".apk") for c in uploader.call_args_list),1)
                self.assertFalse(any(c.args[0]=="apk_install" for c in stage.call_args_list))
                cleanup.assert_called_once()
                self.assertFalse(cleanup.call_args.args[3].is_set())
                self.assertFalse(getattr(installer,"local_only",False))

    def test_native_parser_accepts_valid_old_and_new_outputs_and_rejects_unknown_actions(self):
        from modules.capture_services.openwrt.package_manager import PackageManager
        from modules.capture_services.openwrt.package_space import planned_packages
        from modules.capture_services.openwrt.apk_package_feed import parse_simulate_plan
        for series in ("19.07","21.02","22.03","23.05","24.10"):
            plan="Installing wpa-cli (1.0-1) to root..."
            with self.subTest(series=series):
                PackageManager("opkg").verify_plan(plan,("wpa-cli",))
                self.assertEqual(planned_packages("opkg",plan),{"wpa-cli":"1.0-1"})
        for spacing in (" ","  ","\t"):
            plan="( 1/12)"+spacing+"Installing wpa-cli (1.0-r1)"
            PackageManager("apk").verify_plan(plan,("wpa-cli",))
            self.assertEqual(parse_simulate_plan(plan),[("wpa-cli","1.0-r1")])
            self.assertEqual(planned_packages("apk",plan),{"wpa-cli":"1.0-r1"})
        for unknown in ("Updating pinning libc@edge (1-r1)","[APK unavailable, skipped] Reinstalling libc (1-r1)","Unknown action libc (1-r1)"):
            with self.subTest(action=unknown), self.assertRaises(ValueError):
                PackageManager("apk").verify_plan("(1/1) "+unknown+"\nOK: 10 MiB",("wpa-cli",))
