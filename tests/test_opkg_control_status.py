"""Installed opkg control metadata may include or omit its own Status."""
import io
import tarfile
import unittest
from unittest.mock import Mock
from modules.capture_services.openwrt.installed_dependency_catalog import parse_opkg, OpkgRecordError
from modules.capture_services.openwrt.installed_dependency_reader import InstalledDependencyReader


def archive_for(control):
    output=io.BytesIO()
    with tarfile.open(fileobj=output,mode='w:gz') as archive:
        for name,content in (('libatomic1.control',control),('libatomic1.list',b'/usr/lib/libatomic.so\n')):
            member=tarfile.TarInfo(name);member.size=len(content)
            archive.addfile(member,io.BytesIO(content))
    return output.getvalue()


class OpkgControlStatusTests(unittest.TestCase):
    def pkg(self):
        return parse_opkg('Package: libatomic1\nVersion: 1-r1\nStatus: install ok installed\n')['libatomic1']

    def reader(self,control):
        return InstalledDependencyReader(Mock(),Mock(read=Mock(return_value=archive_for(control))),Mock())

    def test_control_with_or_without_status_uses_actual_installed_state(self):
        for status in (b'',b'Status: install ok installed\n',b'Status: deinstall ok config-files\n'):
            with self.subTest(status=status):
                pkg=self.pkg()
                self.reader(b'Package: libatomic1\nVersion: 1-r1\nRequire-User: tool=123\n'+status)._opkg_metadata([pkg])
                self.assertEqual(pkg.metadata['Status'],'install ok installed')
                self.assertEqual(pkg.metadata['Require-User'],'tool=123')
                self.assertEqual(pkg.files,('usr/lib/libatomic.so',))

    def test_duplicate_status_in_actual_control_still_rejected(self):
        data=b'Package: libatomic1\nVersion: 1-r1\nStatus: install ok installed\nStatus: install ok installed\n'
        with self.assertRaises(OpkgRecordError):self.reader(data)._opkg_metadata([self.pkg()])

    def test_malformed_later_paragraph_reports_owning_package_without_content(self):
        data=b'Package: libatomic1\nVersion: 1-r1\n\n secret-not-a-field\n'
        with self.assertRaises(OpkgRecordError) as context:
            self.reader(data)._opkg_metadata([self.pkg()])
        message=str(context.exception)
        self.assertIn('libatomic1',message)
        self.assertIn('\u6709\u7f29\u8fdb',message)
        self.assertNotIn('secret-not-a-field',message)

    def test_control_identity_mismatch_still_rejected(self):
        for data in (b'Package: other\nVersion: 1-r1\n',b'Package: libatomic1\nVersion: 2-r1\n'):
            with self.subTest(data=data), self.assertRaisesRegex(ValueError,'identity mismatch'):
                self.reader(data)._opkg_metadata([self.pkg()])

    def test_live_inventory_still_requires_installed_and_clean(self):
        self.assertEqual(parse_opkg('Package: foo\nVersion: 1\n'),{})
        with self.assertRaises(ValueError):parse_opkg('Package: foo\nVersion: 1\n',require_clean=True)
        with self.assertRaises(ValueError):parse_opkg('',require_clean=True,control_record=True)
