"""Exercise the production maintenance probe with pinned official help contracts."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest
from modules.capture_services.openwrt.router_backup import PROBE

BASH = Path("C:/Program Files/Git/bin/bash.exe")
CONTRACTS = json.loads((Path(__file__).parent / "fixtures/official_maintenance_contracts.json").read_text(encoding="utf-8"))


@unittest.skipUnless(BASH.is_file(), "Git Bash required for POSIX shell contracts")
class MaintenanceVersionContracts(unittest.TestCase):
    def probe(self, contract, *, pending=False, uci_failure=False, overlay=True, helper=True):
        with tempfile.TemporaryDirectory(prefix="maintenance-contract-") as temporary:
            root = Path(temporary)
            for directory in ("sbin", "proc", "etc/config"):
                (root / directory).mkdir(parents=True)
            native = root.as_posix()
            posix = "/" + native[0].lower() + native[2:] if os.name == "nt" else native
            (root / "sbin/sysupgrade").write_bytes(("#!/bin/sh\nprintf '%s\\n' " +
                shlex.quote(contract["help_text"]) + "\nexit " + str(contract["help_exit"]) + "\n").encode())
            if helper:
                (root / "sbin" / contract["reset_helper"]).write_bytes(b"#!/bin/sh\nexit 99\n")
            (root / "proc/mounts").write_bytes(b"/dev/root /rom squashfs ro 0 0\n" +
                (b"overlayfs:/overlay / overlay rw 0 0\n" if overlay else b""))
            (root / "proc/mtd").write_bytes(b"")
            (root / "etc/config/network").write_bytes(b"config interface lan\n")
            command = PROBE
            for prefix in ("/sbin/", "/proc/", "/etc/config/", "/etc/shadow"):
                command = command.replace(prefix, posix + prefix)
            setup = ("chmod +x " + shlex.quote(posix) + "/sbin/*\n" +
                "id(){ printf '0\\n'; }\n" +
                "ubus(){ return 0; }\n" +
                ("uci(){ return 1; }\n" if uci_failure else
                 "uci(){ printf '%s' " + shlex.quote("network.lan.proto=dhcp" if pending else "") + "; }\n"))
            return subprocess.run([str(BASH), "--noprofile", "--norc"],
                input=setup + command, capture_output=True, text=True, encoding="utf-8",
                timeout=8, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0), posix

    def test_six_pinned_official_help_and_reset_contracts(self):
        self.assertEqual({c["series"] for c in CONTRACTS}, {"19.07", "21.02", "22.03", "23.05", "24.10", "25.12"})
        for contract in CONTRACTS:
            with self.subTest(series=contract["series"]):
                self.assertRegex(contract["commit"], r"^[a-f0-9]{40}$")
                for item in contract["sources"].values():
                    self.assertIn(contract["commit"], item["url"])
                    self.assertRegex(item["sha256"], r"^[a-f0-9]{64}$")
                result, posix = self.probe(contract)
                self.assertEqual(result.returncode, 0, result.stderr)
                lines = result.stdout.splitlines()
                self.assertEqual(lines[0], posix + "/sbin/" + contract["reset_helper"])
                self.assertRegex(lines[1], r"^[a-f0-9]{64} [ *].*/etc/config/network$")

    def test_pending_or_unreadable_configuration_blocks_maintenance(self):
        for contract in CONTRACTS:
            with self.subTest(series=contract["series"]):
                self.assertEqual(self.probe(contract, pending=True)[0].returncode, 42)
                self.assertEqual(self.probe(contract, uci_failure=True)[0].returncode, 43)

    def test_unproven_reset_layout_or_missing_helper_does_not_offer_reset(self):
        for contract in CONTRACTS:
            for options in (dict(overlay=False), dict(helper=False)):
                with self.subTest(series=contract["series"], options=options):
                    result, _ = self.probe(contract, **options)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout.splitlines()[0], "none")
