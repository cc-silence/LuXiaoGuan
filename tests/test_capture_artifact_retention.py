"""Downloaded source remains until every result has been saved successfully."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
from unittest.mock import patch
import unittest
from modules.capture_services.openwrt.handshake_capture import DownloadedHandshake


class ArtifactRetentionTests(unittest.TestCase):
    def capture(self,directory):
        source=Path(directory)/"owned.cap";source.write_bytes(b"invalid-header")
        targets=tuple(NS(bssid=mac,ssid="offline") for mac in ("02:11:22:33:44:55","02:11:22:33:44:66"))
        return DownloadedHandshake(source,targets,(),0,True)

    def test_actual_split_failure_keeps_original_bytes(self):
        with TemporaryDirectory() as directory:
            capture=self.capture(directory)
            with self.assertRaises(ValueError):capture.analyze()
            self.assertEqual(capture.local_cap.read_bytes(),b"invalid-header")

    def test_split_io_failure_keeps_original(self):
        with TemporaryDirectory() as directory:
            capture=self.capture(directory)
            with patch("modules.capture_services.openwrt.channel_capture.split_channel_cap",side_effect=OSError("disk write failed")):
                with self.assertRaises(OSError):capture.analyze()
            self.assertTrue(capture.local_cap.is_file())

    def test_analysis_failure_after_successful_split_keeps_original(self):
        with TemporaryDirectory() as directory:
            capture=self.capture(directory)
            paths={t.bssid:Path(directory)/(str(i)+".cap") for i,t in enumerate(capture.targets)}
            with patch("modules.capture_services.openwrt.channel_capture.split_channel_cap",return_value=(paths,False)),patch("modules.capture_services.openwrt.handshake_capture.analyze_handshake_cap",side_effect=ValueError("parse failed")):
                with self.assertRaises(ValueError):capture.analyze()
            self.assertTrue(capture.local_cap.is_file())

    def test_successful_analysis_keeps_source_until_explicit_release(self):
        with TemporaryDirectory() as directory:
            capture=self.capture(directory)
            paths={t.bssid:Path(directory)/(capture.local_cap.stem+"-"+t.bssid.replace(":","")+".cap") for t in capture.targets}
            for p in paths.values():p.write_bytes(b"split")
            verdict=NS(status="none",detail="none",content_complete=True)
            with patch("modules.capture_services.openwrt.channel_capture.split_channel_cap",return_value=(paths,False)),patch("modules.capture_services.openwrt.handshake_capture.analyze_handshake_cap",return_value=(verdict,{})),patch("modules.capture_services.openwrt.handshake_capture.handshake_content_fingerprint",return_value=""):
                self.assertEqual(len(capture.analyze()),2)
            self.assertTrue(capture.local_cap.is_file())
            capture.release()
            self.assertFalse(capture.local_cap.exists())
            self.assertFalse(any(p.exists() for p in paths.values()))

    def test_real_shared_capture_handoff_retains_raw_until_release(self):
        import struct
        from unittest.mock import Mock
        from modules.capture_services.openwrt.handshake_capture import OpenWrtHandshakeCapture
        from modules.capture_services.openwrt.command_runner import RemoteCommandResult
        with TemporaryDirectory() as directory:
            workspace=NS(local_dir=Path(directory),remote_dir="/tmp/owned")
            runner=Mock();runner.run.return_value=RemoteCommandResult("offline",0)
            processes=Mock();processes.is_running.return_value=True
            capture=OpenWrtHandshakeCapture(runner,processes);capture._verify_tuned_channel=Mock()
            manager=Mock();data=bytes.fromhex("d4c3b2a1")+struct.pack("<HHiiii",2,4,0,0,65535,105)
            manager.download_atomic.side_effect=lambda remote,local,event:Path(local).write_bytes(data)
            targets=tuple(NS(bssid=mac,ssid="offline",channel=6,band="2.4G") for mac in ("02:11:22:33:44:55","02:11:22:33:44:66"))
            with patch("modules.capture_services.openwrt.handshake_capture.time.monotonic",side_effect=[0,6]),patch("modules.capture_services.openwrt.handshake_capture.time.sleep"):
                downloaded=capture.capture_passive(workspace,"wbmonoffline","session",targets[0],5,manager,group_targets=targets,defer_analysis=True)
            self.assertIsInstance(downloaded,DownloadedHandshake)
            results=downloaded.analyze();self.assertEqual(len(results),2)
            processes.start.assert_called_once();processes.stop.assert_called_once()
            self.assertTrue(downloaded.local_cap.is_file())
            downloaded.release();self.assertFalse(downloaded.local_cap.exists())
