"""OpenWrt control files contain indented blank continuation lines."""
import io
import tarfile
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock
from modules.capture_services.openwrt.installed_dependency_catalog import parse_opkg, OpkgRecordError
from modules.capture_services.openwrt.installed_dependency_reader import InstalledDependencyReader


class OpkgMultilineTests(unittest.TestCase):
    def test_official_libevent_description_survives_control_parsing(self):
        text=('Package: libevent2-core7\nVersion: 2.1.12-1\nDepends: libc\n'
              'Description:  The libevent API provides a mechanism to execute a callback function\n'
              ' when a specific event occurs on a file descriptor or after a timeout\n'
              ' has been reached. Furthermore, libevent also support callbacks due\n'
              ' to signals or regular timeouts.\n \n'
              ' libevent is meant to replace the event loop found in event driven\n'
              ' network servers.\n \n This package contains the libevent core shared library.\n')
        for newline in ('\n','\r\n'):
            with self.subTest(newline=repr(newline)):
                record=parse_opkg(text.replace('\n',newline),control_record=True)['libevent2-core7']
                self.assertIn('\n \n libevent is meant',record.metadata['Description'])
                self.assertIn('This package contains',record.metadata['Description'])
                pkg=parse_opkg('Package: libevent2-core7\nVersion: 2.1.12-1\nStatus: install ok installed\n')['libevent2-core7']
                data=io.BytesIO()
                with tarfile.open(fileobj=data,mode='w:gz') as tar:
                    for name,content in [('libevent2-core7.control',text.replace('\n',newline).encode()),('libevent2-core7.list',b'/usr/lib/libevent.so\n')]:
                        member=tarfile.TarInfo(name);member.size=len(content);tar.addfile(member,io.BytesIO(content))
                reader=InstalledDependencyReader(Mock(),Mock(read=Mock(return_value=data.getvalue())),Mock())
                reader._opkg_metadata([pkg])
                self.assertIn('libevent is meant',pkg.metadata['Description'])
                self.assertEqual(pkg.metadata['Status'],'install ok installed')

    def test_multiline_description_round_trips_rebuilt_package(self):
        from modules.capture_services.openwrt.dependency_snapshot_archive import rebuild_package
        from modules.capture_services.openwrt.dependency_snapshot_package_check import inspect_package
        text=('Package: libevent2-core7\nVersion: 2.1.12-1\nArchitecture: all\n'
              'Description: libevent API\n first paragraph\n \n second paragraph\n')
        pkg=parse_opkg(text,control_record=True)['libevent2-core7']
        pkg.files=('usr/lib/libevent.so',)
        with TemporaryDirectory() as directory:
            root=Path(directory);payload=root/'payload.tar.gz';output=root/'libevent.ipk'
            with tarfile.open(payload,'w:gz') as tar:
                content=b'library';entry=tarfile.TarInfo(pkg.files[0]);entry.size=len(content);tar.addfile(entry,io.BytesIO(content))
            rebuild_package(pkg,'opkg',payload,output,lambda:None)
            inspect_package(output,'opkg',{'name':pkg.name,'version':pkg.version})
            with tarfile.open(output,'r:gz') as outer:
                with tarfile.open(fileobj=io.BytesIO(outer.extractfile('control.tar.gz').read()),mode='r:gz') as inner:
                    rebuilt=inner.extractfile('control').read().decode('utf-8')
            reread=parse_opkg(rebuilt,control_record=True)[pkg.name]
            self.assertEqual(reread.metadata['Description'],pkg.metadata['Description'])

    def test_empty_line_still_separates_distinct_packages(self):
        text='Package: one\nVersion: 1\nStatus: install ok installed\n\nPackage: two\nVersion: 1\nStatus: install ok installed\n'
        self.assertEqual(set(parse_opkg(text,require_clean=True)),{'one','two'})

    def test_invalid_record_and_duplicate_are_not_ignored(self):
        for text in ('Package: one\nVersion: 1\n\n unknown line\n','Package: one\nVersion: 1\nVersion: 2\n'):
            with self.subTest(text=text),self.assertRaises(OpkgRecordError):parse_opkg(text,control_record=True)
