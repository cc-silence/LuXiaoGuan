"""Cancellation and cleanup contracts across all offline installation entrypoints."""
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
from modules.capture_services.openwrt.apk_offline import ApkOfflineInstaller
from modules.capture_services.openwrt.common_bundle_installer import install_from_local
from modules.capture_services.openwrt.dependency_manager import OpenWrtDependencyManager
from modules.capture_services.openwrt.offline_cleanup import cleanup_remote
from modules.capture_services.openwrt.package_manager import PackageManager
from modules.capture_services.openwrt.signal_manager import RouterSignalManager, SignalError


class OfflineCleanupTests(unittest.TestCase):
    def test_cleanup_failure_is_visible_and_does_not_escape(self):
        tell=Mock();execute=Mock(side_effect=SignalError("transport","disconnected"))
        self.assertFalse(cleanup_remote(execute,"cleanup","rm -rf /tmp/owned",tell))
        self.assertEqual(execute.call_args.args[2],30)
        self.assertFalse(execute.call_args.args[3].is_set())
        tell.assert_called_once()
        self.assertEqual(tell.call_args.args[1],"warning")

    def test_signal_cleanup_failure_does_not_replace_cancellation(self):
        manager=RouterSignalManager(Mock());cancel=Event();tell=Mock()
        manager._execute_stage=Mock(return_value="")
        manager._execute=Mock(side_effect=SignalError("transport","cleanup failed"))
        with self.assertRaises(SignalError) as result:
            manager._install_ipk_paths([Path("lua_1_all.ipk")],cancel,tell,
                                      uploader=lambda *_:cancel.set())
        self.assertEqual(result.exception.code,"cancelled")
        self.assertTrue(any(call.args[1]=="warning" for call in tell.call_args_list))

    def test_all_apk_entrypoints_cleanup_independently_and_preserve_cancellation(self):
        device=NS(package_manager="apk",version="25.12.0",distribution="OpenWrt",
                  target="ramips/mt7621",package_architecture="mipsel_24kc")
        for route in ("signal","dependency","local"):
            with self.subTest(route=route),TemporaryDirectory() as folder:
                cancel=Event();error=SignalError("cancelled","cancelled")
                signal=RouterSignalManager(Mock())
                manager=signal if route=="signal" else OpenWrtDependencyManager(Mock())
                manager._execute=Mock(return_value="")
                manager._execute_stage=Mock(return_value="")
                def interrupted(installer,*args,**kwargs):
                    cancel.set()
                    installer._cleanup()
                    raise error
                with patch.object(ApkOfflineInstaller,"install",autospec=True,side_effect=interrupted),patch.object(ApkOfflineInstaller,"install_local",autospec=True,side_effect=interrupted):
                    with self.assertRaises(SignalError) as result:
                        if route=="signal":
                            signal._install_offline_apk(device,folder,cancel,uploader=Mock())
                        elif route=="dependency":
                            manager._install_offline_apk(("tmux",),device,folder,False,cancel,Mock(),Mock())
                        else:
                            manager._preflight=Mock(return_value=PackageManager("apk"))
                            repo=NS(root=Path(folder)/"resource",kind="common",profile=Mock(return_value={"package_manager":"apk"}))
                            install_from_local(manager,("tmux",),device,folder,False,cancel,Mock(),Mock(),repo)
                self.assertIs(result.exception,error)
                name,command,timeout,cleanup_cancel=manager._execute.call_args.args
                self.assertEqual(name,"apk_cleanup")
                self.assertEqual(timeout,30)
                self.assertFalse(cleanup_cancel.is_set())
                self.assertTrue(cancel.is_set())


if __name__=="__main__":unittest.main()
