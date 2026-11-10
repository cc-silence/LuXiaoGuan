"""Real Paramiko channel closure is not proof of remote process exit."""
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
from paramiko import Channel
from modules.capture_services.openwrt.capture_channel_state import (
    CaptureExitUnconfirmed, drain_capture_tail, require_capture_exit_status)
from modules.capture_services.openwrt.auto_capture_service import AutoCaptureRadioSession
from modules.capture_services.openwrt.command_runner import RemoteCommandResult
from modules.capture_services.openwrt.models import OpenWrtRadioInfo
from modules.capture_services.openwrt.radio_usage import RadioUsage


class CaptureChannelStateTests(unittest.TestCase):
    def lane(self):
        return AutoCaptureRadioSession(OpenWrtRadioInfo('radio7',interface='phy2'),Mock(),
                                       NS(connected=True,_client=object(),host_fingerprint='same'),
                                       Path(tempfile.gettempdir()),Mock())

    def test_real_closed_channel_reports_ready_with_unknown_exit(self):
        channel=Channel(7)
        with channel.lock:channel._set_closed()
        self.assertTrue(channel.exit_status_ready())
        self.assertEqual(channel.recv_exit_status(),-1)
        with self.assertRaises(CaptureExitUnconfirmed):require_capture_exit_status(channel.recv_exit_status())
        self.assertEqual(require_capture_exit_status(0),0)
        self.assertEqual(require_capture_exit_status(1),1)

    def test_final_tool_diagnostics_are_read_even_when_channel_already_closed(self):
        lane=self.lane();channel=Channel(7)
        channel._feed(b'last diagnostic: failed to arm interface\n')
        with channel.lock:channel._set_closed()
        lane._channel=channel
        with self.assertRaises(CaptureExitUnconfirmed):lane._stream_loop()
        texts=[call.args[1] for call in lane.service.radioStreamOutput.emit.call_args_list]
        self.assertTrue(any('last diagnostic' in text for text in texts))

    def test_unexpected_eof_and_recv_exception_are_not_silently_successful(self):
        for error in (None,OSError('read broken')):
            lane=self.lane();channel=Mock();channel.exit_status_ready.return_value=False
            if error:channel.recv.side_effect=error
            else:channel.recv.return_value=b''
            lane._channel=channel;lane._start_time=time.monotonic()
            with self.assertRaises((RuntimeError,OSError)):lane._stream_loop()

    def test_manual_stop_is_not_reported_as_unexpected_channel_loss(self):
        lane=self.lane();lane._channel=Mock();lane._stop_event.set()
        lane._stream_loop();lane._channel.recv.assert_not_called()

    def test_tail_drain_has_read_and_memory_bounds(self):
        channel=Mock();channel.recv_ready.return_value=True
        channel.recv.side_effect=lambda count:b'x'*count
        result=drain_capture_tail(channel)
        self.assertEqual(len(result),16384)
        self.assertLessEqual(channel.recv.call_count,8)

    def test_startup_without_exit_status_never_resets_or_starts_another_engine(self):
        lane=self.lane();channel=Channel(7);channel._feed(b'last startup diagnostic\n')
        with channel.lock:channel._set_closed()
        lane.ssh_client.open_capture_stream=Mock(return_value=channel)
        lane.ssh_client._client=Mock();lane.ssh_client._client.get_transport.return_value.is_active.return_value=True
        lane._bound_client=lane.ssh_client._client
        def run(name,*args,**kwargs):
            return RemoteCommandResult(name,0,'/usr/sbin/hcxdumptool' if name=='find_hcx_bin' else 'ABSENT' if name=='sweep_stale' else '* 5180 MHz [36] (20.0 dBm)' if name=='capture_channel_capabilities' else '')
        lane.runner.run.side_effect=run
        lane._build_attempt_commands=Mock(return_value=[('first','owned capture'),('second','owned capture')])
        with patch('modules.capture_services.openwrt.auto_capture_service.OpenWrtRadioTakeover') as takeover, \
             patch('modules.capture_services.openwrt.auto_capture_service.read_radio_usage',return_value={'radio7':RadioUsage('radio7','phy2','idle','idle')}), \
             patch.object(lane,'_cleanup_session',return_value=True),patch('time.sleep'):
            takeover.return_value.resolve_phy.return_value=('phy2',())
            lane._run_capture_pipeline('',False)
        lane.ssh_client.open_capture_stream.assert_called_once()
        self.assertIn('exit_status=-1',lane.stats.error_message)
        self.assertFalse(any(call.args[0]=='reset_iface' for call in lane.runner.run.call_args_list))
