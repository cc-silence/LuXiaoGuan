import unittest
from modules.capture_services.openwrt.installed_dependency_catalog import (
    parse_opkg, parse_apk, dependency_closure, restore_selection, allowed_payload,
    file_path, dependency_roots,
)


class CatalogTests(unittest.TestCase):
    def test_virtual_and_shared_dependencies(self):
        rows = parse_opkg("Package: curl\nVersion: 1\nStatus: install ok installed\nDepends: libc, ssl (>= 2)\n\n"
                          "Package: libc\nVersion: 1\nStatus: install ok installed\n\n"
                          "Package: libssl\nVersion: 3\nStatus: install ok installed\nProvides: ssl\n")
        self.assertEqual([p.name for p in dependency_closure(rows, {"curl"}, "opkg")],
                         ["curl", "libc", "libssl"])

    def test_apk_files_and_soname(self):
        rows = parse_apk("P:curl\nV:1-r0\nD:so:libc.so !other\nF:usr/bin\nR:curl\n\n"
                         "P:libc\nV:1-r0\np:so:libc.so=1\nF:lib\nR:libc.so\n")
        self.assertEqual(rows["curl"].files, ("usr/bin/curl",))
        self.assertEqual(len(dependency_closure(rows, {"curl"}, "apk")), 2)

    def test_missing_dependency_stops_backup(self):
        with self.assertRaisesRegex(ValueError, "missing"):
            dependency_closure(parse_apk("P:curl\nV:1\nD:absent\n"), {"curl"}, "apk")

    def test_existing_dependencies_skip(self):
        rows = [{"name":"libc", "version":"1", "builtin":True},
                {"name":"curl", "version":"2", "builtin":False}]
        self.assertEqual(restore_selection(rows, parse_apk("P:libc\nV:1\n")),
                         (("curl",), ("libc",)))
        self.assertEqual(restore_selection(rows, parse_apk("P:libc\nV:3\n")),
                         (("curl",), ("libc",)))

    def test_opkg_reinstallation_required_is_not_a_healthy_dependency(self):
        packages=parse_opkg("Package: curl\nVersion: 1\nStatus: install reinstreq installed\n")
        with self.assertRaisesRegex(ValueError,"Installed dependency is broken: curl"):
            dependency_closure(packages,{"curl"},"opkg")
        rows=[{"name":"curl","version":"1","builtin":False}]
        with self.assertRaisesRegex(ValueError,"Installed dependency is broken: curl"):
            restore_selection(rows,packages)
        healthy=parse_opkg("Package: curl\nVersion: 1\nStatus: install hold installed\n")
        self.assertEqual(restore_selection(rows,healthy),((),("curl",)))

    def test_dirty_opkg_database_cannot_start_private_restore(self):
        for status in ("unpacked","half-configured","half-installed","post-inst-failed","removal-failed"):
            text="Package: unrelated-service\nVersion: 1\nStatus: install ok "+status+"\n"
            with self.assertRaisesRegex(ValueError,"Pending package operation.*unrelated-service"):
                parse_opkg(text,require_clean=True)
        text="Package: removed\nVersion: 1\nStatus: deinstall ok config-files\n"
        self.assertEqual(parse_opkg(text,require_clean=True),{})

    def test_sensitive_and_unsafe_paths(self):
        for p in ("etc/shadow", "etc/config/wireless", "lib/apk/db/installed", "root/.ssh/key"):
            self.assertFalse(allowed_payload(p))
        self.assertTrue(allowed_payload("usr/bin/curl"))
        for p in ("../etc/passwd", "usr//bin/curl", "usr/bin/../curl"):
            with self.assertRaises(ValueError):
                file_path(p)

    def test_all_tool_groups(self):
        self.assertTrue({"curl", "zerotier", "hcxdumptool", "tmux", "openssh-sftp-server"} <= dependency_roots())

