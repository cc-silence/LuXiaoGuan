"""Customer missing-helper replay: shared controls and safe source fallback."""
import copy
from dataclasses import replace
from pathlib import Path
from threading import Event
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import unittest

from tests import test_common_offline_bundle as bundle_tests
from tests import test_signal_actions_ui as action_tests
APP = action_tests.APP
from tests.test_wireless_ui import environment
from modules.capture_services.openwrt.common_bundle_installer import install_from_local
from modules.capture_services.openwrt.installation_sources import install_with_sources
from modules.capture_services.openwrt.signal_manager import SignalError
from modules.capture_services.openwrt.wireless_setup_models import WirelessState


class SourceFallbackTests(unittest.TestCase):
    setUp = bundle_tests.CommonOfflineTests.setUp
    tearDown = bundle_tests.CommonOfflineTests.tearDown

    def test_no_snapshot_candidate_continues_to_device_owned_feeds(self):
        for field, value in (("distribution", "Kwrt"), ("version", "22.03-SNAPSHOT"),
                             ("target", "ath79/generic"), ("package_architecture", "mips_24kc")):
            with self.subTest(field=field):
                device = copy.copy(self.f.device)
                device.version = "24.10-SNAPSHOT"
                setattr(device, field, value)
                manager = NS(command_runner=Mock(), _execute_stage=Mock(), _install_offline=Mock())
                online, computer, tell = Mock(), Mock(), Mock()
                install_with_sources(lambda: install_from_local(manager, ("wpa-cli",), device,
                    self.root / "work", True, Event(), tell, repository=self.repo), online, computer, tell)
                online.assert_called_once_with()
                computer.assert_not_called()
                manager._install_offline.assert_not_called()
                manager._execute_stage.assert_not_called()
                self.assertIn(device.version, str(tell.call_args_list))
                self.assertIn(device.distribution, str(tell.call_args_list))

    def test_incompatible_snapshot_candidate_still_tries_router_feeds(self):
        device = copy.copy(self.f.device)
        device.version = "24.10-SNAPSHOT"
        profile = self.repo.profile(self.f.device)
        row = next(row for row in profile["packages"] if row["name"] == "wpa-cli")
        row["depends"] = ["libc (>= 2)"]
        manager = NS(command_runner=Mock(), _execute_stage=Mock(return_value="libc - 1\n"),
                     _install_offline=Mock())
        online, computer = Mock(), Mock()
        with patch("modules.capture_services.openwrt.common_bundle_installer.compare_opkg_versions", return_value=False):
            install_with_sources(lambda: install_from_local(manager, ("wpa-cli",), device,
                self.root / "work", True, Event(), Mock(), repository=self.repo), online, computer, Mock())
        online.assert_called_once_with()
        computer.assert_not_called()
        manager._install_offline.assert_not_called()

    def test_unconfirmed_base_library_never_falls_back_to_network(self):
        device = copy.copy(self.f.device)
        device.version = "24.10-SNAPSHOT"
        manager = NS(command_runner=Mock(), _execute_stage=Mock(return_value="busybox - 1\n"),
                     _install_offline=Mock())
        online, computer = Mock(), Mock()
        with self.assertRaises(SignalError):
            install_with_sources(lambda: install_from_local(manager, ("wpa-cli",), device,
                self.root / "work", True, Event(), Mock(), repository=self.repo), online, computer, Mock())
        online.assert_not_called()
        computer.assert_not_called()
        manager._install_offline.assert_not_called()

    def test_cancelled_local_match_never_starts_another_route(self):
        cancel = Event()
        cancel.set()
        manager = NS(command_runner=Mock(), _execute_stage=Mock(), _install_offline=Mock())
        online, computer = Mock(), Mock()
        with self.assertRaises(SignalError) as result:
            install_with_sources(lambda: install_from_local(manager, ("wpa-cli",), self.f.device,
                self.root / "work", True, cancel, Mock(), repository=self.repo), online, computer, Mock())
        self.assertEqual(result.exception.code, "cancelled")
        online.assert_not_called()
        computer.assert_not_called()
        manager._execute_stage.assert_not_called()

    def test_invalid_signed_metadata_never_becomes_a_route_mismatch(self):
        self.repo.catalog = None
        manager = NS(command_runner=Mock(), _execute_stage=Mock(), _install_offline=Mock())
        online, computer = Mock(), Mock()
        with patch.object(self.repo, "_load", side_effect=ValueError("Invalid signature")):
            with self.assertRaises(SignalError):
                install_with_sources(lambda: install_from_local(manager, ("wpa-cli",), self.f.device,
                    self.root / "work", True, Event(), Mock(), repository=self.repo), online, computer, Mock())
        online.assert_not_called()
        computer.assert_not_called()
        manager._execute_stage.assert_not_called()

    def test_bad_matching_object_stops_before_any_network_fallback(self):
        profile = self.repo.profile(self.f.device)
        row = next(row for row in profile["packages"] if row["name"] == "wpa-cli")
        (self.repo.content_root / row["blob"]).write_bytes(b"corrupt")
        manager = NS(command_runner=Mock(), _execute_stage=Mock(return_value="libc - 1\n"),
                     _install_offline=Mock())
        online, computer = Mock(), Mock()
        with self.assertRaises(SignalError):
            install_with_sources(lambda: install_from_local(manager, ("wpa-cli",), self.f.device,
                self.root / "work", True, Event(), Mock(), repository=self.repo), online, computer, Mock())
        online.assert_not_called()
        computer.assert_not_called()
        manager._install_offline.assert_not_called()


class MissingHelperUiTests(unittest.TestCase):
    create_page = action_tests.SignalActionTests.create_page

    def test_missing_helper_action_is_visible_in_every_signal_subpage(self):
        for light in (False, True):
            setup = action_tests.PendingSetup()
            setup.install = Mock(return_value=True)
            setup.snapshot = WirelessState(environment=replace(environment(), missing=("wpa-cli",)),
                                           detail="Missing: wpa-cli")
            page, service = self.create_page(light, setup)
            panels = page.setup_panels
            button = panels.check_button
            for index in (0, 1, 2):
                page.stack.setCurrentIndex(index)
                APP.processEvents()
                self.assertTrue(button.isVisible())
                self.assertTrue(button.isEnabled())
                self.assertIn("wpa-cli", panels.environment_hint.text())
                with patch("modules.common_widgets.common_dialogs.ConfirmDialog.exec", return_value=1):
                    button.click()
                setup.install.assert_called_with(("wpa-cli",))
                self.assertIs(button, panels.check_button)
            service.snapshot = replace(service.snapshot, busy=True)
            service.snapshotChanged.emit(service.snapshot)
            self.assertFalse(button.isEnabled())
            service.snapshot = replace(service.snapshot, busy=False)
            setup.snapshot = replace(setup.snapshot, environment=environment(), detail="Ready")
            setup.snapshotChanged.emit(setup.snapshot)
            self.assertFalse(button.isVisible())
            page.stack.setCurrentIndex(1)
            APP.processEvents()
            self.assertTrue(button.isVisible())
            service.snapshot = replace(service.snapshot, connected=False)
            service.snapshotChanged.emit(service.snapshot)
            self.assertFalse(button.isVisible())

    def test_optional_relayd_install_remains_cancellable_without_missing_helpers(self):
        setup = action_tests.PendingSetup()
        setup.install, setup.cancel = Mock(), Mock(return_value=True)
        setup.snapshot = WirelessState(environment=environment(), detail="Installing relayd",
                                       action="install", busy=True)
        setup.active = True
        page, service = self.create_page(setup=setup)
        service.snapshot = replace(service.snapshot, busy=True)
        page.stack.setCurrentIndex(2)
        service.snapshotChanged.emit(service.snapshot)
        APP.processEvents()
        button = page.setup_panels.check_button
        self.assertTrue(button.isVisible())
        self.assertTrue(button.isEnabled())
        button.click()
        setup.cancel.assert_called_once_with()
        setup.install.assert_not_called()
        setup.active = False
        setup.snapshot = replace(setup.snapshot, busy=False, action="")
        service.snapshot = replace(service.snapshot, busy=False)
        setup.snapshotChanged.emit(setup.snapshot)
        service.snapshotChanged.emit(service.snapshot)
        APP.processEvents()
        self.assertFalse(button.isVisible())

    def test_relay_install_has_a_working_cancel_action(self):
        setup = action_tests.PendingSetup()
        setup.install, setup.cancel = Mock(), Mock(return_value=True)
        setup.snapshot = WirelessState(environment=replace(environment(), missing=("wpa-cli",)),
                                       detail="Installing", action="install", busy=True)
        setup.active = True
        page, service = self.create_page(setup=setup)
        service.snapshot = replace(service.snapshot, busy=True)
        service.snapshotChanged.emit(service.snapshot)
        page.stack.setCurrentIndex(2)
        APP.processEvents()
        button = page.setup_panels.check_button
        self.assertTrue(button.isVisible())
        self.assertTrue(button.isEnabled())
        button.click()
        setup.cancel.assert_called_once_with()
        setup.install.assert_not_called()
        self.assertGreaterEqual(button.width(), button.fontMetrics().horizontalAdvance(button.text()))
        setup.active = False
        setup.snapshot = replace(setup.snapshot, busy=False, detail="Installation cancelled")
        service.snapshot = replace(service.snapshot, busy=False)
        setup.snapshotChanged.emit(setup.snapshot)
        service.snapshotChanged.emit(service.snapshot)
        APP.processEvents()
        self.assertTrue(button.isVisible())
        self.assertTrue(button.isEnabled())
        with patch("modules.common_widgets.common_dialogs.ConfirmDialog.exec", return_value=1):
            button.click()
        setup.install.assert_called_once_with(("wpa-cli",))



class RouterFeedSafetyTests(unittest.TestCase):
    def manager(self, plan):
        from modules.capture_services.openwrt.dependency_manager import OpenWrtDependencyManager
        from modules.capture_services.openwrt.package_manager import PackageManager
        manager = OpenWrtDependencyManager(Mock())
        manager._preflight = Mock(return_value=PackageManager("opkg"))
        manager._execute_stage = Mock(side_effect=lambda name, *_:
            plan if name == "dependency_package_plan" else "")
        return manager

    def test_online_wpa_cli_plan_cannot_replace_existing_packages(self):
        for verb in ("Upgrading", "Downgrading", "Reinstalling", "Replacing"):
            manager = self.manager("Installing wpa-cli (1) to root...\n" + verb + " wpad-basic from 1 to 2...")
            computer = Mock()
            with self.assertRaises(SignalError):
                install_with_sources(lambda: False,
                    lambda: manager._install_online(("wpa-cli",), Event(), Mock()), computer, Mock())
            names = [call.args[0] for call in manager._execute_stage.call_args_list]
            self.assertEqual(names, ["dependency_update", "dependency_package_plan"])
            computer.assert_not_called()

    def test_online_add_only_plan_uses_existing_feeds_and_no_force_options(self):
        manager = self.manager("Installing wpa-cli (1) to root...")
        with patch("modules.capture_services.openwrt.package_space.check_plan_space"):
            manager._install_online(("wpa-cli",), Event(), Mock())
        commands = [call.args[1] for call in manager._execute_stage.call_args_list]
        self.assertEqual(commands[0], "opkg update")
        self.assertEqual(sum(command == "opkg install wpa-cli" for command in commands), 1)
        self.assertTrue(all(command.startswith("opkg ") for command in commands))
        self.assertTrue(all("--force" not in command and "distfeeds" not in command for command in commands))
