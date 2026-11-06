"""Durable WPS sessions are independent of user-facing result directories."""
import shutil
import unittest
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from modules.capture_services.openwrt.wps_session import checkpoint, prepare_session, session_name
from modules.capture_services.openwrt.result_converter import final_results_dir

MAC="02:11:22:33:44:55"

def native(position=12):
    return f"{position}\n3\n1\n"+"\n".join(f"{n:04d}" for n in range(10000))+"\n"+"\n".join(f"{n:03d}" for n in range(1000))+"\n"

class WpsStorageTests(unittest.TestCase):
    def test_results_can_be_deleted_without_losing_automatic_resume(self):
        with TemporaryDirectory() as folder:
            root=Path(folder);output=root/"results";state=root/"state"
            output.mkdir();workspace=NS(remote_dir="/tmp/owned");manager=Mock()
            manager.download_atomic.side_effect=lambda remote,path:Path(path).write_text(native(),encoding="ascii")
            with patch("modules.capture_services.openwrt.wps_session.session_directory",return_value=state):
                saved=Path(checkpoint(workspace,manager,MAC,output))
                self.assertEqual(saved,state/session_name(MAC))
                self.assertEqual(list(output.iterdir()),[])
                shutil.rmtree(output)
                remote,restored=prepare_session(workspace,manager,MAC,{},root/"different-results")
            self.assertTrue(restored)
            self.assertEqual(remote,"/tmp/owned/"+session_name(MAC))
            manager.upload_atomic.assert_called_once_with(saved,PurePosixPath(remote))

    def test_legacy_result_files_are_not_read_or_imported(self):
        with TemporaryDirectory() as folder:
            root=Path(folder);output=root/"results";state=root/"state"
            legacy=final_results_dir(output)/session_name(MAC)
            legacy.write_text(native(),encoding="ascii")
            before=legacy.read_bytes();manager=Mock();builder=Mock()
            with patch("modules.capture_services.openwrt.wps_session.session_directory",return_value=state):
                _,restored=prepare_session(NS(remote_dir="/tmp/owned"),manager,MAC,{},output,builder)
            self.assertFalse(restored);builder.assert_called_once()
            manager.upload_atomic.assert_not_called()
            self.assertEqual(legacy.read_bytes(),before)
            self.assertFalse((state/session_name(MAC)).exists())

    def test_invalid_checkpoint_preserves_previous_progress(self):
        with TemporaryDirectory() as folder:
            state=Path(folder)/"state";state.mkdir()
            destination=state/session_name(MAC);destination.write_text(native(),encoding="ascii")
            before=destination.read_bytes();manager=Mock()
            manager.download_atomic.side_effect=lambda remote,path:Path(path).write_text("invalid",encoding="ascii")
            with patch("modules.capture_services.openwrt.wps_session.session_directory",return_value=state):
                self.assertEqual(checkpoint(NS(remote_dir="/tmp/owned"),manager,MAC,folder),"")
            self.assertEqual(destination.read_bytes(),before)
            self.assertEqual(list(state.iterdir()),[destination])

    def test_explicit_user_selected_file_remains_supported(self):
        with TemporaryDirectory() as folder:
            root=Path(folder);source=root/"chosen.wpc";state=root/"state"
            source.write_text(native(),encoding="ascii");manager=Mock()
            with patch("modules.capture_services.openwrt.wps_session.session_directory",return_value=state):
                _,restored=prepare_session(NS(remote_dir="/tmp/owned"),manager,MAC,
                                          {"resume_file":str(source)},root/"results")
            self.assertTrue(restored);self.assertTrue(source.exists())
            self.assertEqual((state/session_name(MAC)).read_bytes(),source.read_bytes())

if __name__=="__main__":
    unittest.main()
