"""Manual restoration and conflicting state exercised at the recovery boundary."""
import os
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch
from tests import test_password_recovery as fixtures
from modules.capture_services.openwrt.password_recovery import _owned_or_absent, _original_or_disabled


class ManualRecoveryCases(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shell = shutil.which("sh")
        if not cls.shell:
            candidate = Path("C:/Program Files/Git/bin/bash.exe")
            cls.shell = str(candidate) if candidate.is_file() else None
        if not cls.shell:
            raise unittest.SkipTest("POSIX shell unavailable")

    def run_guard(self, guard, mode):
        fixture = """uci() {
 case \"$*\" in
 'get wireless.owned')
   case \"$MODE\" in absent) printf 'uci: Entry not found'; return 1;; io) printf 'uci: I/O error'; return 1;; *) printf wifi-iface; return 0;; esac;;
 'get wireless.owned.routermanager_session')
   if [ \"$MODE\" = owned ]; then printf session1; else printf foreign; fi; return 0;;
 'get wireless.home.disabled')
   case \"$MODE\" in absent) printf 'uci: Entry not found'; return 1;; original) printf 0; return 0;; disabled) printf 1; return 0;; *) printf 2; return 0;; esac;;
 esac
 return 1
}
"""
        return subprocess.run([self.shell, "-c", fixture + guard], capture_output=True,
                              text=True, timeout=10, env=dict(os.environ, MODE=mode)).returncode

    def test_absent_owned_section_is_success_but_foreign_and_io_are_not(self):
        command = _owned_or_absent("wireless", "owned", "session1", "true")
        for mode, allowed in [("absent", True), ("owned", True), ("foreign", False), ("io", False)]:
            with self.subTest(mode=mode):
                self.assertEqual(self.run_guard(command, mode) == 0, allowed)

    def test_manual_original_state_is_accepted_but_new_value_is_not(self):
        command = _original_or_disabled("home", "0")
        for mode, allowed in [("original", True), ("disabled", True), ("changed", False), ("absent", False)]:
            with self.subTest(mode=mode):
                self.assertEqual(self.run_guard(command, mode) == 0, allowed)
        self.assertEqual(self.run_guard(_original_or_disabled("home", None), "absent"), 0)

    def test_confirmed_restored_configuration_clears_record(self):
        case = fixtures.RecoveryTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        runner = case.runner()
        case.journal.recover(runner, fixtures.FP)
        self.assertEqual(case.store.records(fixtures.FP), [])

    def test_changed_or_unreadable_state_keeps_record(self):
        for failed in ("password_recovery_changes", "password_recovery_apply", "password_restore_config_check", "password_restore_wireless_check"):
            with self.subTest(failed=failed):
                case = fixtures.RecoveryTests()
                case.setUp()
                try:
                    with patch("modules.capture_services.openwrt.password_recovery.SIGNAL_READY_SECONDS", 0):
                        with self.assertRaises(Exception):
                            case.journal.recover(case.runner(failed=failed), fixtures.FP)
                    self.assertEqual(len(case.store.records(fixtures.FP)), 1)
                finally:
                    case.doCleanups()


if __name__ == "__main__": unittest.main()
