"""Windows short names remain usable without weakening owned-cache boundaries."""
import ctypes
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from modules.platform_services.owned_cache import checked_path,inventory,delete_inventory,_windows_long_name_matches


class OwnedCachePathTests(unittest.TestCase):
    def test_native_short_name_supports_inventory_and_owned_deletion(self):
        if os.name != "nt":
            self.assertFalse(_windows_long_name_matches(Path("/alias"),Path("/target")))
            return
        from ctypes import wintypes
        short_name=ctypes.windll.kernel32.GetShortPathNameW
        short_name.argtypes=(wintypes.LPCWSTR,wintypes.LPWSTR,wintypes.DWORD)
        short_name.restype=wintypes.DWORD
        with TemporaryDirectory() as folder:
            base=Path(folder)
            root=base/"long cache directory for native short names"
            root.mkdir()
            (root/"payload.txt").write_bytes(b"owned")
            sentinel=base/"unrelated.txt"
            sentinel.write_bytes(b"retain")
            required=short_name(str(root),None,0)
            self.assertGreater(required,0)
            buffer=ctypes.create_unicode_buffer(required)
            written=short_name(str(root),buffer,required)
            self.assertTrue(0<written<required)
            short=Path(buffer.value)
            self.assertEqual(short.resolve(),root.resolve())
            self.assertEqual(checked_path(short,short/"payload.txt").st_ino,(root/"payload.txt").stat().st_ino)
            entries=inventory(short,short)
            self.assertEqual(len(entries),2)
            delete_inventory(short,entries)
            self.assertFalse(root.exists())
            self.assertEqual(sentinel.read_bytes(),b"retain")

    def test_reparse_ancestor_is_rejected_before_alias_conversion(self):
        with TemporaryDirectory() as folder:
            root=Path(folder)
            info=SimpleNamespace(st_mode=root.stat().st_mode,st_file_attributes=0x400)
            with patch.object(Path,"lstat",return_value=info),patch(
                    "modules.platform_services.owned_cache._windows_long_name_matches",return_value=True) as convert:
                with self.assertRaisesRegex(ValueError,"reparse"):
                    checked_path(root,root)
                convert.assert_not_called()

    def test_resolved_redirection_is_rejected_unless_native_name_matches(self):
        with TemporaryDirectory() as folder:
            root=Path(folder)
            with patch.object(Path,"resolve",return_value=root/"elsewhere"),patch(
                    "modules.platform_services.owned_cache._windows_long_name_matches",return_value=False):
                with self.assertRaisesRegex(ValueError,"Noncanonical"):
                    checked_path(root,root)

    def test_parent_traversal_never_uses_native_alias_exception(self):
        with TemporaryDirectory() as folder:
            root=Path(folder)
            with patch("modules.platform_services.owned_cache._windows_long_name_matches") as convert:
                with self.assertRaises(ValueError):checked_path(root,root/".."/"outside")
                convert.assert_not_called()


if __name__=="__main__":unittest.main()

