"""Known script names must match their own lock; foreign hcx is not ownership."""
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest
from modules.capture_services.openwrt.boot_script_probe import BOOT_SCRIPT_RUNNING_PROBE

SHELL = shutil.which("sh") if os.name != "nt" else str(Path("C:/Program Files/Git/bin/bash.exe"))


@unittest.skipUnless(SHELL and Path(SHELL).is_file(), "POSIX test shell unavailable")
class BootScriptIdentityTests(unittest.TestCase):
    def probe(self, lock_name=None, argument="/root/hcx_autocap.sh", pid="42\n", alive=True):
        with TemporaryDirectory() as directory:
            root=Path(directory)
            if lock_name is not None:(root/lock_name).write_bytes(pid.encode("ascii"))
            script=BOOT_SCRIPT_RUNNING_PROBE.replace("/tmp/.",root.as_posix()+"/.")
            # Head receives its normal arguments; use fixed scene data instead.
            prelude='head() { printf "%s\\000" /bin/sh '+argument+'; };\n'
            prelude+='kill() { '+('return 0' if alive else 'return 1')+'; };\n'
            value=subprocess.run([SHELL,"-c",prelude+script+'\nprintf "%s" "$R_RUNNING"\n'],
                                 text=True,capture_output=True,check=True,timeout=5)
            return value.stdout

    def test_current_and_legacy_script_names_use_their_own_locks(self):
        for name in ("hcx_autocap","autocap"):
            with self.subTest(name=name):
                self.assertEqual(self.probe("."+name+".lock","/root/"+name+".sh"),"1")

    def test_wrong_lock_missing_lock_foreign_and_dead_pid_are_rejected(self):
        self.assertEqual(self.probe(".hcx_autocap.lock","/root/autocap.sh"),"0")
        self.assertEqual(self.probe(".autocap.lock","/root/hcx_autocap.sh"),"0")
        self.assertEqual(self.probe(),"0")
        self.assertEqual(self.probe(".hcx_autocap.lock","/usr/sbin/hcxdumptool"),"0")
        self.assertEqual(self.probe(".hcx_autocap.lock",alive=False),"0")
        self.assertEqual(self.probe(".hcx_autocap.lock",pid="bad;value\n"),"0")
        self.assertEqual(self.probe(".hcx_autocap.lock",pid="42"),"1")

if __name__ == "__main__": unittest.main()
