"""Distribution renaming must preserve validated archive/install boundaries."""
import tempfile
import unittest
from pathlib import Path
from modules.release_identity import APPLICATION_EXECUTABLE, archive_root, installed_executable
from modules.app_update_services.catalog import managed_path


class ReleaseIdentityTests(unittest.TestCase):
    def test_both_archive_generations_and_executable_catalogs(self):
        for root,exe in (("LuXiaoGuan",APPLICATION_EXECUTABLE),("LuXiaoGuan","LuXiaoGuan.exe"),("RouterManager","RouterManager.exe")):
            self.assertEqual(archive_root([root+"/"+exe,root+"/resources/test.dll"]),root+"/")
            self.assertEqual(managed_path(exe),exe)
        for names in (["Other/app.exe"],["LuXiaoGuan/a","RouterManager/b"]):
            with self.assertRaises(ValueError):archive_root(names)

    def test_stable_executable_round_trips_zip_and_update_catalog(self):
        import zipfile
        from types import SimpleNamespace
        from scripts.app_update_catalog import artifacts
        from modules.app_update_services.manifest import canonical
        from modules.app_update_services.catalog import parse_catalog
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);archive=root/"release.zip"
            with zipfile.ZipFile(archive,"w") as zipped:
                zipped.writestr("LuXiaoGuan/"+APPLICATION_EXECUTABLE,b"application")
            payload=dict(version="1.0.9_alpha",notes="",published_at="2026-10-22T00:00:00+08:00",
                         url="https://example.com/files/fixture/release.zip")
            previous=dict(files=[{"path":"LuXiaoGuan.exe"}])
            catalog,objects,fields=artifacts(archive,root/"objects",payload,previous)
            release=SimpleNamespace(**payload,**fields,resource_revision=0)
            checked=parse_catalog(canonical(catalog),release)
            self.assertEqual([row["path"] for row in checked["files"]],[APPLICATION_EXECUTABLE])
            self.assertEqual(checked["retired"],[])
            self.assertEqual(APPLICATION_EXECUTABLE,"LuXiaoGuan.exe")

    def test_restart_selects_the_requested_installed_binary(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            old=root/"RouterManager.exe";old.touch()
            self.assertEqual(installed_executable(root),old)
            new=root/"LuXiaoGuan.exe";new.touch()
            self.assertEqual(installed_executable(root),new)
            chinese=root/APPLICATION_EXECUTABLE;chinese.touch()
            self.assertEqual(installed_executable(root),chinese)
            self.assertEqual(installed_executable(root,new.name),new)
            self.assertEqual(installed_executable(root,old.name),old)
            with self.assertRaises(ValueError):installed_executable(root,"../other.exe")
            with self.assertRaises(FileNotFoundError):installed_executable(root/"absent")
        with self.assertRaises(ValueError):managed_path("other.exe")


if __name__ == "__main__":unittest.main()
