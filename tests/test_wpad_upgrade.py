"""wpad 升级流程的定向回归；全部使用假命令执行器，不接触真机。"""

import hashlib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from modules.capture_services.openwrt.command_runner import RemoteCommandResult
from modules.capture_services.openwrt.package_manager import PackageManager
from modules.capture_services.openwrt.wpad_upgrade import (
    WpadUpgradeError,
    counterpart,
    installed_wpad,
    is_full,
    parse_installed,
    upgrade_wpad,
)

BASIC = "wpad-basic-mbedtls"
FULL = "wpad-mbedtls"


class _Runner:
    """按命令内容返回固定输出，并记录上传的文件映射。"""

    def __init__(self, manager):
        self.manager = manager

    def run(self, name, command, timeout, cancel_event=None, **_kwargs):
        if "list-installed" in command:
            return RemoteCommandResult(
                name, 0, stdout=f"{BASIC} - 2024.01.01-r1\n")
        if "print-architecture" in command:
            return RemoteCommandResult(
                name, 0, stdout="arch all 1\narch mipsel_24kc 10\n")
        if command.startswith("sha256sum"):
            return RemoteCommandResult(
                name, 0, stdout=self.manager.digest_for(command) + "  x\n")
        if "rm -rf" in command:
            return RemoteCommandResult(name, 0, stdout="")
        return RemoteCommandResult(name, 0, stdout="")


class _Manager:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.uploads = {}
        self.stages = []
        self.command_runner = _Runner(self)

    def _preflight(self, _kb, _cancel):
        return PackageManager("opkg")

    def _package_change(self, _pm, packages, _cancel, remove=False):
        assert remove
        return "opkg remove " + packages[0]

    def _execute_stage(self, name, command, _timeout, _cancel, _stage):
        self.stages.append((name, command))

    def _package_uploader(self, _sftp, _cancel, _tell):
        def upload(local, remote):
            self.uploads[remote] = Path(local)
        return upload

    def digest_for(self, command):
        for remote, local in self.uploads.items():
            if remote in command:
                return hashlib.sha256(local.read_bytes()).hexdigest()
        if "wireless.bak" in command or "/etc/config/wireless" in command:
            return hashlib.sha256(b"wireless-config").hexdigest()
        return hashlib.sha256(b"").hexdigest()


class WpadUpgradeTests(unittest.TestCase):
    def test_counterpart_and_full_detection(self):
        self.assertEqual(counterpart("wpad-basic-mbedtls"), "wpad-mbedtls")
        self.assertEqual(counterpart("wpad-basic-wolfssl"), "wpad-wolfssl")
        self.assertEqual(counterpart("wpad-basic"), "wpad")
        self.assertEqual(counterpart("wpad-mini"), "wpad")
        self.assertEqual(counterpart("wpad-mbedtls"), "")
        self.assertTrue(is_full("wpad"))
        self.assertTrue(is_full("wpad-mbedtls"))
        self.assertFalse(is_full("wpad-basic-mbedtls"))

    def test_parse_installed_opkg_and_apk(self):
        opkg = parse_installed("wpad-basic-mbedtls - 2024.01.01-r1 - full\n", "opkg")
        self.assertEqual(opkg[BASIC], "2024.01.01-r1")
        apk = parse_installed("wpad-basic-mbedtls 2024.01.01-r1\n", "apk")
        self.assertEqual(apk[BASIC], "2024.01.01-r1")

    def test_installed_wpad_reports_basic_and_full(self):
        basic, basic_version, full, full_version = installed_wpad(
            {BASIC: "r1", FULL: "r2"})
        self.assertEqual((basic, basic_version, full, full_version),
                         (BASIC, "r1", FULL, "r2"))

    def _upgrade(self, folder, probe_results, fetch=None):
        manager = _Manager(folder)
        device = SimpleNamespace(
            version="23.05.5", package_architecture="mipsel_24kc",
            sftp_available=True)
        full_file = Path(folder) / "full.ipk"
        basic_file = Path(folder) / "basic.ipk"
        full_file.write_bytes(b"full")
        basic_file.write_bytes(b"basic")
        packages = [(FULL, "2024.01.01-r1", full_file),
                    (BASIC, "2024.01.01-r1", basic_file)]
        messages = []
        with patch("modules.capture_services.openwrt.wpad_upgrade.read_management_link",
                   return_value=SimpleNamespace(status="wired")), \
             patch("modules.capture_services.openwrt.wpad_upgrade._download_opkg",
                   return_value=packages), \
             patch("modules.capture_services.openwrt.wpad_upgrade._probe",
                   side_effect=probe_results):
            result = upgrade_wpad(manager, device, folder,
                                  None, lambda text, level="info": messages.append(text),
                                  fetch=fetch)
        return manager, result, messages

    def test_wired_upgrade_installs_full_and_keeps_backup(self):
        with TemporaryDirectory() as folder:
            manager, result, messages = self._upgrade(folder, ["full"])
            self.assertTrue(result["changed"])
            self.assertEqual(result["package"], FULL)
            names = [name for name, _command in manager.stages]
            self.assertIn("wpad_remove_" + BASIC, names)
            self.assertIn("wpad_install_" + FULL, names)
            self.assertTrue(any("opkg --conf" in command and "--nodeps" in command
                                for _name, command in manager.stages))
            self.assertTrue(any("已换成完整版" in text for text in messages))

    def test_wireless_mode_is_refused_before_any_change(self):
        with TemporaryDirectory() as folder:
            manager = _Manager(folder)
            device = SimpleNamespace(version="23.05.5",
                                     package_architecture="mipsel_24kc",
                                     sftp_available=True)
            with patch("modules.capture_services.openwrt.wpad_upgrade.read_management_link",
                       return_value=SimpleNamespace(status="wireless")):
                with self.assertRaises(WpadUpgradeError) as raised:
                    upgrade_wpad(manager, device, folder, None,
                                 lambda *_args: None)
            self.assertEqual(raised.exception.stage, "management_link")
            self.assertEqual(manager.stages, [])

    def test_verification_failure_rolls_back_basic_package(self):
        with TemporaryDirectory() as folder:
            with self.assertRaises(WpadUpgradeError) as raised:
                self._upgrade(folder, ["basic", "basic"])
            self.assertTrue(raised.exception.rollback_ok)
            self.assertIn("已自动装回", str(raised.exception))

    def test_rollback_failure_keeps_packages_and_says_so(self):
        with TemporaryDirectory() as folder:
            manager = _Manager(folder)
            device = SimpleNamespace(version="23.05.5",
                                     package_architecture="mipsel_24kc",
                                     sftp_available=True)
            full_file = Path(folder) / "full.ipk"
            basic_file = Path(folder) / "basic.ipk"
            full_file.write_bytes(b"full")
            basic_file.write_bytes(b"basic")
            packages = [(FULL, "2024.01.01-r1", full_file),
                        (BASIC, "2024.01.01-r1", basic_file)]

            def fail_install(name, _command, _timeout, _cancel, _stage):
                manager.stages.append((name, _command))
                if name == "wpad_install_rollback":
                    raise RuntimeError("install failed")

            manager._execute_stage = fail_install
            with patch("modules.capture_services.openwrt.wpad_upgrade.read_management_link",
                       return_value=SimpleNamespace(status="wired")), \
                 patch("modules.capture_services.openwrt.wpad_upgrade._download_opkg",
                       return_value=packages), \
                 patch("modules.capture_services.openwrt.wpad_upgrade._probe",
                       return_value="basic"):
                with self.assertRaises(WpadUpgradeError) as raised:
                    upgrade_wpad(manager, device, folder, None, lambda *_args: None)
            self.assertFalse(raised.exception.rollback_ok)
            self.assertIn("临时包保留在", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
