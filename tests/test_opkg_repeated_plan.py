"""Feedback 20261024_130059: opkg noaction may repeat one identity."""
from pathlib import Path
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
from modules.capture_services.openwrt.package_space import planned_packages
from modules.capture_services.openwrt.local_ipk_transaction import verify_local_plan
from modules.capture_services.openwrt.package_manager import PackageManager
from modules.capture_services.openwrt.dependency_manager import OpenWrtDependencyManager
from modules.capture_services.openwrt.signal_manager import SignalError

# Reconstructed source-contract fixture, not raw output from the feedback ZIP.
PLAN=("Installing libevent2-7 (2.1.12-1) to root...\n"
      "Installing tmux (3.1c-1) to root...\n"
      "Installing libevent2-7 (2.1.12-1) to root...\n")
EXPECTED={"libevent2-7":"2.1.12-1","tmux":"3.1c-1"}


class RepeatedOpkgPlanTests(unittest.TestCase):
    def test_same_identity_is_counted_once_and_closed_plan_accepts_it(self):
        self.assertEqual(planned_packages("opkg",PLAN),EXPECTED)
        verify_local_plan(PLAN,EXPECTED)

    def test_conflicting_identity_is_still_rejected(self):
        with self.assertRaises(SignalError):
            planned_packages("opkg",PLAN+"Installing libevent2-7 (wrong) to root...\n")

    def test_repeated_foreign_package_does_not_bypass_closed_plan(self):
        with self.assertRaises(ValueError):
            verify_local_plan("Installing foreign (1) to root...\n"*2,EXPECTED)

    def install(self,plan):
        manager=OpenWrtDependencyManager(Mock())
        manager._preflight=Mock(return_value=PackageManager("opkg"))
        manager._execute=Mock(return_value="")
        manager._execute_stage=Mock(side_effect=lambda name,*_:plan if name=="dependency_local_plan" else "")
        device=SimpleNamespace(package_manager="opkg")
        with patch("modules.capture_services.openwrt.package_space.ipk_installed_size",return_value=100):
            manager._install_offline(("tmux",),device,None,False,Event(),Mock(),uploader=Mock(),local_paths=[Path("event.ipk"),Path("tmux.ipk")],local_packages=EXPECTED)
        return manager

    def test_same_version_repeat_reaches_installation(self):
        manager=self.install(PLAN)
        self.assertTrue(any(c.args[0]=="dependency_offline_install" for c in manager._execute_stage.call_args_list))

    def test_validation_error_retains_current_plan_for_log_export(self):
        bad=PLAN+"Installing libevent2-7 (wrong) to root...\n"
        with self.assertRaises(SignalError) as caught:
            self.install(bad)
        self.assertIn("dependency_local_plan",getattr(caught.exception,"package_diagnostic",""))
        self.assertIn("Installing libevent2-7",caught.exception.package_diagnostic)


class OpkgAccountingLimitTests(unittest.TestCase):
    def test_repeated_messages_do_not_consume_unique_package_limit(self):
        self.assertEqual(planned_packages("opkg","Installing tool (1) to root...\n"*80),{"tool":"1"})

    def test_unique_package_limit_is_preserved(self):
        plan="".join(f"Installing tool{n} (1) to root...\n" for n in range(65))
        with self.assertRaises(SignalError):planned_packages("opkg",plan)

    def test_online_space_failure_keeps_plan_in_diagnostic(self):
        manager=OpenWrtDependencyManager(Mock())
        manager._execute_stage=Mock(return_value=PLAN)
        original=SignalError("environment","space cannot be verified")
        with patch("modules.capture_services.openwrt.package_space.check_plan_space",side_effect=original):
            with self.assertRaises(SignalError) as caught:
                manager._package_change(PackageManager("opkg"),("tmux",),Event())
        self.assertIs(caught.exception,original)
        self.assertIn("Installing tmux",original.package_diagnostic)


class NativeAlreadyInstalledTests(unittest.TestCase):
    def test_opkg_already_installed_status_is_success(self):
        PackageManager("opkg").verify_plan("Package tmux is already installed on root.\n",("tmux",))

    def test_opkg_unknown_text_remains_an_error(self):
        with self.assertRaises(ValueError):
            PackageManager("opkg").verify_plan("No useful output",("tmux",))

    def test_apk_computer_route_accepts_native_no_change_success(self):
        from modules.capture_services.openwrt.apk_offline import ApkOfflineInstaller
        installer=ApkOfflineInstaller(lambda *_:"OK: 10 MiB in 20 packages")
        self.assertEqual(installer._simulate(("tmux",)),[])

    def test_apk_no_change_route_skips_package_download_and_install_but_cleans(self):
        from modules.capture_services.openwrt.apk_offline import ApkOfflineInstaller
        from modules.capture_services.openwrt import apk_package_feed as feed
        from tempfile import TemporaryDirectory
        device=SimpleNamespace(version="25.12.0",package_architecture="mipsel_24kc",package_manager="apk",target="ramips/mt7621")
        stage=Mock(side_effect=lambda name,*_:"OK: 10 MiB in 20 packages" if name=="apk_simulate_plan" else "")
        cleanup=Mock(return_value="")
        installer=ApkOfflineInstaller(stage,uploader=Mock(),cleanup_execute=cleanup)
        with TemporaryDirectory() as folder,patch.object(feed,"download_adb_files",return_value={"base":b"index"}),patch.object(feed,"merge_index") as merge:
            self.assertEqual(installer.install(("tmux",),device,folder,Event()),[])
        merge.assert_not_called()
        self.assertFalse(any(c.args[0]=="apk_install" for c in stage.call_args_list))
        cleanup.assert_called_once()
