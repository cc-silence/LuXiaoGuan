"""Package preflight must fail closed and retain native APK verification."""
import unittest
from modules.capture_services.openwrt.package_manager import PackageManager
from modules.capture_services.openwrt.apk_offline import ApkOfflineInstaller


class PackageInstallGuards(unittest.TestCase):
    def test_whitespace_cannot_hide_existing_package_changes(self):
        prefix = "Installing relayd (2025-01-01) to root...\n"
        for verb in ("Upgrading", "Downgrading", "Reinstalling", "Replacing", "Removing"):
            for whitespace in ("", " ", "\t"):
                with self.subTest(verb=verb, whitespace=whitespace), self.assertRaises(ValueError):
                    PackageManager("opkg").verify_plan(prefix+whitespace+verb+" netifd (old) to new...", ("relayd",))
        PackageManager("opkg").verify_plan("  Installing relayd (2025-01-01) to root...", ("relayd",))

    def test_apk_keeps_signature_verification_for_simulation_and_install(self):
        commands = []
        def stage(name, command, *_):
            commands.append(command)
            return "(1/1) Installing relayd (2025.01-r1)\nOK: 1 packages"
        installer = ApkOfflineInstaller(stage)
        installer.local_only = True
        installer._simulate(("relayd",))
        installer._install(("relayd",))
        self.assertEqual(len(commands), 2)
        self.assertTrue(all("--no-network" in command for command in commands))
        self.assertTrue(all("--allow-untrusted" not in command for command in commands))
        self.assertTrue(all("--force" not in command for command in commands))

    def test_signature_error_does_not_retry_with_weaker_options(self):
        commands = []
        def stage(name, command, *_):
            commands.append(command)
            raise RuntimeError("UNTRUSTED signature")
        installer = ApkOfflineInstaller(stage)
        with self.assertRaises(RuntimeError): installer._simulate(("relayd",))
        self.assertEqual(len(commands), 1)
        self.assertNotIn("--allow-untrusted", commands[0])
