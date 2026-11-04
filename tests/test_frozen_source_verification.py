"""Stale frozen code must never pass release validation."""
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
from scripts import frozen_source_verification as frozen


class FrozenSourceTests(unittest.TestCase):
    def verify(self, *, stale=False):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/"fixture.py";source.write_text("value = 2\n",encoding="utf-8")
            code=compile("value = 1\n" if stale else source.read_bytes(),"fixture.py","exec")
            pyz=NS(toc={"fixture":None,"thirdparty":None},extract=Mock(return_value=code))
            archive=NS(toc={"PYZ.pyz":None},open_embedded_archive=Mock(return_value=pyz))
            with patch.object(frozen,"CArchiveReader",return_value=archive):
                return frozen.verify_frozen_sources(root/"fixture.exe",root)
    def test_same_project_source_is_verified(self):
        self.assertEqual(self.verify()["frozen_source_modules"],1)
    def test_old_cached_code_is_rejected(self):
        with self.assertRaisesRegex(ValueError,"fixture"):self.verify(stale=True)


class FrozenEntrypointTests(unittest.TestCase):
    def verify(self, stale=False):
        import marshal
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'main.py'
            source.write_text('APP_VERSION = "1.0.21_alpha"\n',encoding='utf-8')
            code=compile('APP_VERSION = "1.0.20_alpha"\n' if stale else source.read_bytes(),'main.py','exec')
            archive=NS(extract=Mock(return_value=marshal.dumps(code)))
            with patch.object(frozen,'CArchiveReader',return_value=archive):
                return frozen.verify_frozen_entrypoint(root/'LuXiaoGuan',source)

    def test_entrypoint_outside_pyz_is_compared_to_exact_source(self):
        self.assertEqual(len(self.verify()['frozen_entrypoint_sha256']),64)

    def test_cached_entrypoint_with_old_version_is_rejected(self):
        with self.assertRaises(ValueError):self.verify(stale=True)
