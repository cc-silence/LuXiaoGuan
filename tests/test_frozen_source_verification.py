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
