"""Exercise real ZIP/catalog bytes; synthetic native headers are never executed."""
from contextlib import contextmanager
import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from scripts import linux_release_layout as layout
from scripts.linux_update_catalog import artifacts


def elf_bytes():
    value=bytearray(64); value[:7]=b'\x7fELF\x02\x01\x01'
    value[16:18]=(3).to_bytes(2,'little'); value[18:20]=(62).to_bytes(2,'little')
    return bytes(value)+b'synthetic; never executed'


@contextmanager
def modes():
    if sys.platform=='win32':
        with patch.object(layout,'_permissions',side_effect=lambda p,i:0o755 if p.name in {'LuXiaoGuan','libroutermanager_terminal.so',layout.LAUNCHER} else 0o644):
            yield
    else: yield


class LinuxReleaseLayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.package=self.root/'package';self.package.mkdir()
        values={layout.LAUNCHER:layout.SCRIPT,'LuXiaoGuan':elf_bytes(),'resources/base_library.zip':b'synthetic python archive',
                'resources/libpython3.10.so.1.0':b'synthetic python runtime',
                'bin/router_terminal/libroutermanager_terminal.so':elf_bytes(),
                'themes/dark.qss':b'dark','themes/light.qss':b'light',
                'images/icons/app-router.png':b'icon','bin/tools/scripts/hcx_autocap.sh':b'#!/bin/sh\n',
                'bin/tools/openwrt_base/base.zip':(Path(__file__).resolve().parents[1]/'tools/openwrt_base/base.zip').read_bytes()}
        for name,data in values.items():
            p=self.package/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
            p.chmod(0o755 if name in {'LuXiaoGuan','bin/router_terminal/libroutermanager_terminal.so',layout.LAUNCHER} else 0o644)
        self.version='1.0.21_alpha'

    def baseline(self):
        with modes(): return layout.write_baseline(self.package,self.version)

    def test_real_zip_round_trip_preserves_linux_identity_permissions_and_baseline(self):
        baseline=json.loads(self.baseline().read_bytes());target=self.root/'linux.zip'
        with modes(): result=layout.make_zip(self.package,target,self.version)
        self.assertTrue(result['zip_crc_verified'])
        with zipfile.ZipFile(target) as zipped:
            info=zipped.getinfo('LuXiaoGuan/LuXiaoGuan')
            self.assertEqual(info.create_system,3)
            self.assertEqual(info.external_attr>>16,stat.S_IFREG|0o755)
            self.assertEqual(zipped.read('LuXiaoGuan/resources/base_library.zip'),b'synthetic python archive')
        payload=dict(platform='linux-x64',version=self.version,url='https://example.invalid/files/linux-x64/package.zip',notes='fixture',published_at='2026-10-28T12:00:00+08:00')
        catalog,objects,fields=artifacts(target,self.root/'objects',payload)
        self.assertEqual(sorted(catalog['files'],key=lambda r:r['path']),baseline['files'])
        self.assertTrue(objects);self.assertEqual(fields['catalog_size'],Path(objects[fields['catalog_sha256']]['path']).stat().st_size)

    def test_existing_delivery_is_never_overwritten(self):
        self.baseline();target=self.root/'linux.zip';target.write_bytes(b'previous verified delivery')
        with modes(),self.assertRaises(FileExistsError):layout.make_zip(self.package,target,self.version)
        self.assertEqual(target.read_bytes(),b'previous verified delivery')

    def test_private_config_and_windows_binaries_are_rejected(self):
        for relative in ['config/settings.ini','resources/password.key','bin/Windows.exe','bin/tools/hc_offline_pack/old.ipk']:
            with self.subTest(relative=relative):
                path=self.package/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'private')
                with modes(),self.assertRaises(ValueError):layout.inventory(self.package)
                path.unlink()
                if relative.startswith('config/'):path.parent.rmdir()

    def test_changed_payload_or_version_cannot_pass_baseline_validation(self):
        self.baseline()
        with modes(),self.assertRaises(ValueError):layout.verify_files(self.package,'1.0.22_alpha')
        (self.package/'themes/dark.qss').write_bytes(b'changed')
        with modes(),self.assertRaises(ValueError):layout.verify_files(self.package,self.version)

    def test_windows_header_cannot_be_delivered_as_linux(self):
        (self.package/'LuXiaoGuan').write_bytes(b'MZ'+bytes(64));self.baseline()
        with modes(),self.assertRaises(ValueError):layout.make_zip(self.package,self.root/'linux.zip',self.version)
        self.assertFalse((self.root/'linux.zip').exists())

    def test_missing_private_interpreter_is_rejected(self):
        (self.package/'resources/libpython3.10.so.1.0').unlink();self.baseline()
        with modes(),self.assertRaises(ValueError):layout.verify_files(self.package,self.version)

    def test_tracked_assets_and_explicit_runtime_components_are_seeded(self):
        source=self.root/'source';source.mkdir();target=self.root/'assets';target.mkdir()
        files={'bin/third_party_licenses/wpspin-MIT.txt':b'fixture MIT license','images/default.png':b'default','images/custom-secret.png':b'custom',
               'config/settings.ini':b'private','bin/update_launcher/Windows.exe':b'MZ',
               'bin/router_terminal/libroutermanager_terminal.so':elf_bytes(),
               'bin/router_terminal/libvterm-LICENSE.txt':b'license',
               'bin/router_terminal/kodoterm-LICENSE.txt':b'license',
               'tools/openwrt_base/base.zip':(Path(__file__).resolve().parents[1]/'tools/openwrt_base/base.zip').read_bytes(),
               'tools/scripts/hcx_autocap.sh':b'#!/bin/sh\n','tools/hc_offline_pack/SHA256SUMS.txt':b'fixture hashes'}
        compatibility={f'bin/hcx_compat/{arch}/hcxdumptool':elf_bytes() for arch in ('x86_64','aarch64','arm','mips','mipsel')}
        compatibility.update({'bin/hcx_compat/'+name:b'fixture' for name in ('manifest.json','hcxdumptool-LICENSE.txt','Zig-LICENSE.txt','musl-COPYRIGHT.txt')})
        files.update(compatibility)
        for name,data in files.items():
            path=source/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
        with patch.object(layout.subprocess,'check_output',return_value=b'bin/third_party_licenses/wpspin-MIT.txt\0images/default.png\0tools/scripts/hcx_autocap.sh\0tools/openwrt_base/base.zip\0tools/hc_offline_pack/SHA256SUMS.txt\0'):
            layout.seed_assets(source,target)
        for name,data in compatibility.items():
            self.assertEqual((target/name).read_bytes(),data)
        self.assertEqual((target/'bin/third_party_licenses/wpspin-MIT.txt').read_bytes(),b'fixture MIT license')
        self.assertTrue((target/'images/default.png').is_file())
        self.assertEqual((target/'bin/tools/scripts/hcx_autocap.sh').read_bytes(),b'#!/bin/sh\n')
        self.assertFalse((target/'bin/tools/hc_offline_pack').exists())
        self.assertTrue((target/'bin/tools/openwrt_base/base.zip').is_file())
        self.assertFalse((target/'images/custom-secret.png').exists())
        self.assertFalse((target/'config').exists());self.assertFalse((target/'bin/update_launcher').exists())

    def test_tampered_builtin_dependency_zip_is_rejected(self):
        path = self.package / 'bin/tools/openwrt_base/base.zip'
        layout.verify_builtin_base(path)
        with path.open('r+b') as stream:
            first = stream.read(1)
            stream.seek(0)
            stream.write(bytes([first[0] ^ 1]))
        with self.assertRaisesRegex(ValueError, 'fingerprint'):
            layout.verify_builtin_base(path)

    def test_internal_file_link_is_encoded_as_a_link_and_escape_is_rejected(self):
        link=self.package/'resources/runtime-link'
        try:link.symlink_to('base_library.zip')
        except OSError as exc:self.skipTest('Native symlink creation unavailable: '+type(exc).__name__)
        self.baseline()
        with modes():layout.make_zip(self.package,self.root/'links.zip',self.version)
        with zipfile.ZipFile(self.root/'links.zip') as zipped:
            info=zipped.getinfo('LuXiaoGuan/resources/runtime-link')
            self.assertTrue(stat.S_ISLNK(info.external_attr>>16))
            self.assertEqual(zipped.read(info),b'base_library.zip')
        link.unlink();link.symlink_to('../../outside')
        with modes(),self.assertRaises(ValueError):layout.inventory(self.package)

    def test_valid_crc_with_wrong_archive_bytes_is_rejected(self):
        baseline=self.baseline().read_bytes();target=self.root/'tampered.zip'
        with modes():entries=layout.inventory(self.package)
        with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as zipped:
            for row in entries:
                info=zipfile.ZipInfo('LuXiaoGuan/'+row['path']);info.create_system=3
                info.external_attr=(stat.S_IFREG|row['mode'])<<16
                data=(self.package/row['path']).read_bytes()
                if row['path']=='themes/dark.qss':data=b'evil'
                zipped.writestr(info,data)
            info=zipfile.ZipInfo('LuXiaoGuan/'+layout.BASELINE);info.create_system=3
            info.external_attr=(stat.S_IFREG|0o644)<<16;zipped.writestr(info,baseline)
        with zipfile.ZipFile(target) as zipped:self.assertIsNone(zipped.testzip())
        with self.assertRaises(ValueError):layout.verify_zip(target,entries,baseline)
