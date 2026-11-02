"""Space checks exercise the upstream single-selector opkg info contract."""
import os
import shlex
import shutil
from pathlib import Path
import subprocess
import unittest
from threading import Event
from unittest.mock import Mock
from modules.capture_services.openwrt.dependency_manager import OpenWrtDependencyManager
from modules.capture_services.openwrt.package_manager import PackageManager
from modules.capture_services.openwrt.package_space import check_plan_space, required_kb
from modules.capture_services.openwrt.signal_manager import SignalError

GIT_BASH = Path("C:/Program Files/Git/bin/bash.exe")
SHELL = str(GIT_BASH) if os.name == "nt" and GIT_BASH.is_file() else shutil.which("sh")

PACKAGES = ("liblua5.1.5", "libubus-lua", "libuci-lua", "lua", "luci-lib-jsonc")
PLAN = "".join("Installing %s (1) to root...\n" % name for name in PACKAGES)
METADATA = "\n\n".join("Package: %s\nVersion: 1\nInstalled-Size: 1024" % name for name in PACKAGES)


class SpaceQueryTests(unittest.TestCase):
    def manager(self, output=METADATA):
        manager = OpenWrtDependencyManager(Mock())
        manager._execute_stage = Mock(return_value=output)
        manager._preflight = Mock(return_value=PackageManager("opkg"))
        return manager

    def test_multi_package_query_uses_one_connection_and_one_selector_per_invocation(self):
        manager = self.manager()
        check_plan_space(manager, PackageManager("opkg"), PLAN, Event())
        manager._execute_stage.assert_called_once()
        command = manager._execute_stage.call_args.args[1]
        self.assertIn('opkg info "$package"', command)
        self.assertIn('for package in ', command)
        self.assertIn('|| exit "$?"', command)
        manager._preflight.assert_called_once()
        self.assertEqual(manager._preflight.call_args.args[0], required_kb([1024] * 5))

    def test_incomplete_or_wrong_version_metadata_still_stops(self):
        for text in (METADATA.split("\n\n")[0], METADATA.replace("Version: 1", "Version: 2"), ""):
            with self.subTest(text=text):
                manager = self.manager(text)
                with self.assertRaises(SignalError) as caught:
                    check_plan_space(manager, PackageManager("opkg"), PLAN, Event())
                manager._preflight.assert_not_called()
                self.assertIn("dependency_install_sizes", caught.exception.package_diagnostic)
                self.assertIn("Installing", caught.exception.package_diagnostic)
                self.assertLess(len(caught.exception.package_diagnostic), 8192)

    def test_actual_space_failure_is_not_bypassed(self):
        manager = self.manager()
        original = SignalError("environment", "not enough space")
        manager._preflight.side_effect = original
        with self.assertRaises(SignalError) as caught:
            check_plan_space(manager, PackageManager("opkg"), PLAN, Event())
        self.assertIs(caught.exception, original)
        self.assertIn("Installed-Size", original.package_diagnostic)

    def test_repeated_plan_entries_are_counted_once(self):
        manager = self.manager()
        check_plan_space(manager, PackageManager("opkg"), PLAN * 3, Event())
        self.assertEqual(manager._preflight.call_args.args[0], required_kb([1024] * 5))

    def test_empty_plan_does_not_query(self):
        manager = self.manager()
        check_plan_space(manager, PackageManager("opkg"), "Package lua is already installed on root.", Event())
        manager._execute_stage.assert_not_called()
        manager._preflight.assert_not_called()

    def test_apk_route_retains_native_multi_package_query_and_repository(self):
        manager = self.manager("\n\n".join(n + "-1 installed size:\n1 KiB" for n in PACKAGES))
        plan = "\n".join("(1/5) Installing %s (1)" % n for n in PACKAGES)
        check_plan_space(manager, PackageManager("apk"), plan, Event(), repositories="/tmp/repo file")
        command = shlex.split(manager._execute_stage.call_args.args[1])
        self.assertEqual(command, ["apk", "info", "--size", "--repositories-file", "/tmp/repo file", *PACKAGES])
        self.assertEqual(manager._preflight.call_args.args[0], required_kb([1024] * 5))


@unittest.skipUnless(SHELL, "POSIX test shell unavailable")
class NativeShellQueryTests(unittest.TestCase):
    def execute(self, command, fail=None):
        cases = []
        for name in PACKAGES:
            row = "Package: %s\nVersion: 1\nInstalled-Size: 1024" % name
            action = "return 7" if name == fail else "printf '%s\\n' " + shlex.quote(row)
            cases.append(name + ") " + action + ";;")
        function = "opkg() { [ \"$1\" = info ] || return 8; case \"$2\" in " + " ".join(cases) + " esac; }\n"
        return subprocess.run([SHELL, "-c", function + command], capture_output=True, text=True, timeout=3)

    def test_upstream_first_selector_behavior_reproduces_old_failure(self):
        from modules.capture_services.openwrt.package_space import parse_opkg_sizes
        result = self.execute("opkg info " + " ".join(PACKAGES))
        self.assertEqual(result.returncode, 0)
        with self.assertRaises(SignalError):
            parse_opkg_sizes(result.stdout, dict.fromkeys(PACKAGES, "1"))

    def test_batch_command_reads_all_five_real_shell_results(self):
        manager = OpenWrtDependencyManager(Mock())
        def stage(name, command, *args):
            result = self.execute(command)
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout
        manager._execute_stage = Mock(side_effect=stage)
        manager._preflight = Mock()
        check_plan_space(manager, PackageManager("opkg"), PLAN, Event())
        self.assertEqual(manager._preflight.call_args.args[0], required_kb([1024] * 5))
        manager._execute_stage.assert_called_once()

    def test_failed_query_stops_before_remaining_packages(self):
        manager = SpaceQueryTests().manager()
        check_plan_space(manager, PackageManager("opkg"), PLAN, Event())
        command = manager._execute_stage.call_args.args[1]
        result = self.execute(command, fail=PACKAGES[1])
        self.assertEqual(result.returncode, 7)
        self.assertNotIn("Package: lua\n", result.stdout)
