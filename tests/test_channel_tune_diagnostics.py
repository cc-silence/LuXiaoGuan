"""Failure evidence remains bounded and cannot interfere with cleanup."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from modules.capture_services.openwrt.channel_tune_diagnostics import tool_snapshot, append_stopped_output
from modules.capture_services.openwrt.handshake_capture import OpenWrtHandshakeCapture, ChannelTuneError
from modules.capture_services.openwrt.models import AccessPointInfo


class TuneDiagnosticsTests(unittest.TestCase):
    def test_running_tool_output_is_bounded(self):
        manager=Mock()
        manager.is_running.return_value=True
        manager.read_log_tail.return_value='x'*20000+'END'
        text=tool_snapshot(manager,object())
        self.assertIn('process_running=True',text)
        self.assertTrue(text.endswith('END'))
        self.assertLess(len(text),8500)
        manager.read_log_tail.assert_called_once()

    def test_read_failure_is_explicit_and_nonfatal(self):
        manager=Mock()
        manager.is_running.side_effect=RuntimeError('private detail')
        manager.read_log_tail.side_effect=OSError('private detail')
        text=tool_snapshot(manager,object())
        self.assertIn('process_state_unavailable=RuntimeError',text)
        self.assertIn('tool_log_unavailable=OSError',text)
        self.assertNotIn('private detail',text)

    def test_post_stop_output_appends_without_overwriting_scene(self):
        with TemporaryDirectory() as folder:
            workspace=NS(local_dir=Path(folder))
            path=Path(folder)/'scene.txt';path.write_text('scene',encoding='utf-8')
            manager=Mock();manager.read_log_tail.return_value='z'*20000+'FLUSHED'
            append_stopped_output(workspace,'scene.txt',manager,object())
            text=path.read_text(encoding='utf-8')
            self.assertTrue(text.startswith('scene'))
            self.assertIn('FLUSHED',text)
            self.assertLess(len(text),8400)
            append_stopped_output(workspace,'../outside',manager,object())
            self.assertEqual(manager.read_log_tail.call_count,1)

    def test_capture_failure_collects_before_stop_and_flushed_output_after(self):
        with TemporaryDirectory() as folder:
            workspace=NS(local_dir=Path(folder),remote_dir='/tmp/capture')
            manager=Mock();manager.read_log_tail.return_value='TOOL OUTPUT'
            capture=OpenWrtHandshakeCapture(Mock(),manager)
            capture._verify_tuned_channel=Mock(side_effect=ChannelTuneError('wrong channel'))
            order=[]
            manager.stop.side_effect=lambda *a:order.append('stop')
            def collect(*a,**kw):
                order.append('scene')
                self.assertIn('--channel 11',kw['tool_log'])
                self.assertIn('process_running=True',kw['tool_log'])
                (Path(folder)/'scene.txt').write_text(kw['tool_log'],encoding='utf-8')
                return 'scene.txt'
            with patch('modules.capture_services.openwrt.handshake_capture.collect_tool_failure_diagnostics',side_effect=collect):
                with self.assertRaises(ChannelTuneError):
                    capture.capture_passive(workspace,'wbmon0','session',
                        AccessPointInfo('02:11:22:33:44:55',channel=11,band='2.4G'),5,Mock())
            self.assertEqual(order,['scene','stop'])
            self.assertIn('tool-output-after-stop',(Path(folder)/'scene.txt').read_text(encoding='utf-8'))
            self.assertEqual(manager.read_log_tail.call_count,2)

    def test_empty_output_is_marked(self):
        manager=Mock();manager.is_running.return_value=False;manager.read_log_tail.return_value=''
        text=tool_snapshot(manager,object())
        self.assertIn('process_running=False',text)
        self.assertIn('<empty or unavailable>',text)
