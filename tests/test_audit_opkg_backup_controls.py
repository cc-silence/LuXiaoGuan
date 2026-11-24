"""Regression for reproducible offline IPK compatibility audit."""
import hashlib
import io
import json
from pathlib import Path
import tarfile
from tempfile import TemporaryDirectory
import unittest
import zipfile
from scripts.audit_opkg_backup_controls import audit


def ipk(control):
    target=io.BytesIO()
    with tarfile.open(fileobj=target,mode='w:gz') as outer:
        inner=io.BytesIO()
        with tarfile.open(fileobj=inner,mode='w:gz') as tar:
            member=tarfile.TarInfo('control');member.size=len(control);tar.addfile(member,io.BytesIO(control))
        blob=inner.getvalue();member=tarfile.TarInfo('control.tar.gz');member.size=len(blob);outer.addfile(member,io.BytesIO(blob))
    return target.getvalue()


def fixture(path,control):
    blob=ipk(control);sha=hashlib.sha256(blob).hexdigest();name='objects/'+sha
    row=dict(name='libevent2-core7',version='1',sha256=sha,size=len(blob),blob=name)
    catalog=dict(profiles=[dict(package_manager='opkg',release='23.05.4',packages=[row]),dict(package_manager='opkg',release='23.05.5',packages=[row])])
    with zipfile.ZipFile(path,'w') as archive:
        archive.writestr('catalog.json',json.dumps(catalog));archive.writestr(name,blob)


class AuditBackupControlsTests(unittest.TestCase):
    def test_realistic_multiline_and_dedup(self):
        with TemporaryDirectory() as d:
            path=Path(d)/'test.zip';fixture(path,b'Package: libevent2-core7\nVersion: 1\nDescription: text\n \n continued\n')
            result=audit([path]);self.assertEqual(result['stats']['unique'],1)
            self.assertEqual(result['stats']['indented_blank'],1);self.assertEqual(result['failures'],[])
            self.assertEqual(result['references_by_series']['23.05'],2)

    def test_bad_control_fails_without_copying_private_contents(self):
        with TemporaryDirectory() as d:
            path=Path(d)/'test.zip';fixture(path,b'Package: libevent2-core7\nVersion: 1\npassword=private\n')
            result=audit([path]);self.assertEqual(len(result['failures']),1)
            self.assertNotIn('password',str(result['failures']))
