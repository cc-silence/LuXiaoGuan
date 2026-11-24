"""Bounded diagnostics for malformed opkg backup inventory."""
import unittest
from unittest.mock import Mock
from modules.capture_services.openwrt.installed_dependency_catalog import parse_opkg, OpkgRecordError
from modules.capture_services.openwrt.installed_dependency_reader import InstalledDependencyReader


class OpkgBackupDiagnosticTests(unittest.TestCase):
    def test_duplicate_field_reports_record_package_field_and_reason(self):
        text=('Package: good\nVersion: 1\nStatus: install ok installed\n\n'
              'Package: curl\nVersion: 2\nVersion: secret-value\nStatus: install ok installed\n')
        with self.assertRaises(OpkgRecordError) as context:
            parse_opkg(text, require_clean=True)
        message=str(context.exception)
        for value in ('2', 'curl', 'Version', '\u5b57\u6bb5\u91cd\u590d'):
            self.assertIn(value,message)
        self.assertNotIn('secret-value',message)

    def test_invalid_line_and_untrusted_name_do_not_leak_contents(self):
        text='Package: secret key\nDescription: value\npassword=do-not-log\n'
        with self.assertRaises(OpkgRecordError) as context:parse_opkg(text)
        message=str(context.exception)
        self.assertIn('\u5b57\u6bb5\u884c\u7f3a\u5c11\u5192\u53f7',message)
        for value in ('secret key','do-not-log','password='):self.assertNotIn(value,message)

    def test_source_distinguishes_installed_from_firmware_inventory(self):
        good=b'Package: curl\nVersion: 1\nStatus: install ok installed\n'
        bad=b'Package: curl\nVersion: 1\nVersion: 2\nStatus: install ok installed\n'
        for sequence,source in (([bad], '\u5f53\u524d'),([good,bad], '\u539f\u5382')):
            with self.subTest(source=source):
                reader=InstalledDependencyReader(Mock(),Mock(read=Mock(side_effect=sequence)),Mock())
                reader.run=Mock(return_value='opkg')
                with self.assertRaises(OpkgRecordError) as context:reader.inventory()
                self.assertIn(source,str(context.exception))
                self.assertIn('curl',str(context.exception))
