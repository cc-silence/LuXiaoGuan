"""Official virtual providers retain version checks and installed-library safety."""
from dataclasses import replace
import unittest
from unittest.mock import Mock
from modules.capture_services.openwrt.dependency_resolution import InstalledPackages, resolve_packages
from modules.capture_services.openwrt.signal_package_feed import FeedPackage, parse_packages_index
from scripts.openwrt_packages.opkg_version import compare, satisfies


def package(name, version="2022-12-15-8d158096-1", depends=(), provides=()):
    return FeedPackage(name, version, name+".ipk", "a"*64, 1, "https://fixture/"+name, depends, provides)


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.library = package("libiwinfo20210430", provides=("libiwinfo",))
        self.root = package("rpcd-mod-iwinfo", depends=("libiwinfo (>= 2022-12-15)",))
        self.index = {self.root.name: self.root, self.library.name: self.library, "libiwinfo": self.library}

    def test_official_virtual_dependency_uses_provider_version(self):
        result = resolve_packages(self.index, [self.root.name], set(), satisfies)
        self.assertEqual([p.name for p in result], [self.library.name, self.root.name])

    def test_undeclared_alias_cannot_satisfy_versioned_dependency(self):
        self.index["libiwinfo"] = replace(self.library, provides=())
        with self.assertRaises(ValueError):
            resolve_packages(self.index, [self.root.name], set(), satisfies)

    def test_installed_provider_uses_its_actual_version(self):
        check = Mock(side_effect=satisfies)
        result = resolve_packages(self.index, [self.root.name], InstalledPackages({self.library.name: "2023-01-21-1"}), check)
        self.assertEqual([p.name for p in result], [self.root.name])
        check.assert_called_once_with("2023-01-21-1", ">=", "2022-12-15")

    def test_old_or_unknown_installed_provider_is_never_replaced(self):
        for installed in [InstalledPackages({self.library.name: "2020-01-01-1"}), {self.library.name}]:
            with self.assertRaises(ValueError):
                resolve_packages(self.index, [self.root.name], installed, satisfies)

    def test_version_comparator_is_required(self):
        with self.assertRaises(ValueError):
            resolve_packages(self.index, [self.root.name], set())

    def test_online_feed_keeps_explicit_provider_evidence(self):
        text = "Package: libiwinfo20210430\nVersion: 2022-12-15-1\nFilename: libiwinfo.ipk\nSHA256sum: " + "a"*64 + "\nSize: 1\nProvides: libiwinfo\n"
        result = parse_packages_index(text, "https://fixture/Packages")
        self.assertEqual(result["libiwinfo"].provides, ("libiwinfo",))

    def test_epoch_revision_tilde_and_numeric_ordering(self):
        for left, right in [("1.0~rc1", "1.0"), ("1.0-2", "1.0-10"),
                ("1.9", "1.10"), ("9.0", "1:1.0"),
                ("2022-12-15", "2022-12-15-8d158096-1")]:
            with self.subTest(left=left, right=right):
                self.assertLess(compare(left, right), 0)
                self.assertGreater(compare(right, left), 0)
        self.assertEqual(compare("1.01", "1.1"), 0)
        self.assertEqual(compare("1.0", "1.0-0"), 0)
