"""Hints cannot replace packet verification, including grouped and partial captures."""
import struct
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import unittest
from modules.capture_services.openwrt.handshake_live_check import LiveHandshakeCheck
from modules.capture_services.handshake_processing import HandshakeProcessingQueue, DeferredHandshake

AP='00:11:22:33:44:55'
STA='00:11:22:33:44:66'


def packet(ap,sta,message):
    ap=bytes.fromhex(ap.replace(':',''));sta=bytes.fromhex(sta.replace(':',''))
    body=bytes([2,3])+struct.pack('>H',95)+bytes([2])+struct.pack('>HHQ',0x8a if message==1 else 0x10a,16,1)
    body+=(b'a' if message==1 else b's')*32+bytes(48)+bytes(2)
    if message==2:body=body[:81]+b'm'*16+body[97:]
    header=struct.pack('<HH',0x0208 if message==1 else 0x0108,0)
    header+=(sta+ap+ap if message==1 else ap+sta+ap)+bytes(2)
    return header+bytes.fromhex('aaaa03000000888e')+body


def cap(*frames):
    data=struct.pack('<IHHIIII',0xa1b2c3d4,2,4,0,0,65535,105)
    for index,frame in enumerate(frames):data+=struct.pack('<IIII',index,0,len(frame),len(frame))+frame
    return data


class LiveCheckTests(unittest.TestCase):
    def run_check(self, payloads, hint=True, targets=(AP,)):
        with TemporaryDirectory() as folder:
            manager=Mock();payloads=iter(payloads)
            def download(remote,local,event):
                if str(remote).endswith('hint.txt'):
                    Path(local).write_text('WPA handshake: '+AP if hint else '',encoding='utf-8')
                else:Path(local).write_bytes(next(payloads))
            manager.download_atomic.side_effect=download
            check=LiveHandshakeCheck(manager,NS(local_dir=Path(folder)),'/tmp/input.cap','/tmp/hint.txt',targets,None)
            # Advance a deterministic clock, rather than sleeping through fallback intervals.
            values=iter(range(0,1000,20))
            with patch('modules.capture_services.openwrt.handshake_live_check.monotonic',side_effect=lambda:next(values)),patch.object(check._stop,'wait',side_effect=[False,True]):
                check._run()
            return check.completed()

    def test_m1_and_tool_hint_are_not_usable(self):
        self.assertEqual(self.run_check([cap(packet(AP,STA,1))]*2),frozenset())

    def test_partial_file_then_completed_pair_is_verified(self):
        full=cap(packet(AP,STA,1),packet(AP,STA,2))
        self.assertEqual(self.run_check([full[:-20],full]),frozenset({AP}))

    def test_no_hint_fallback_still_checks_real_packets(self):
        self.assertEqual(self.run_check([cap(packet(AP,STA,1),packet(AP,STA,2))],hint=False),frozenset({AP}))

    def test_different_hotspots_or_clients_do_not_form_a_pair(self):
        other_ap='00:11:22:33:44:77';other_sta='00:11:22:33:44:88'
        for second in (packet(other_ap,STA,2),packet(AP,other_sta,2)):
            self.assertEqual(self.run_check([cap(packet(AP,STA,1),second)]*2,targets=(AP,other_ap)),frozenset())

    def test_checker_runs_without_command_lane_and_only_cancels_completed_target(self):
        with TemporaryDirectory() as folder:
            parent=Event()
            manager=NS(download_atomic=lambda remote,local,event: Path(local).write_text('WPA handshake: '+AP) if str(remote).endswith('hint.txt') else Path(local).write_bytes(cap(packet(AP,STA,1),packet(AP,STA,2))))
            check=LiveHandshakeCheck(manager,NS(local_dir=Path(folder)),'/tmp/input.cap','/tmp/hint.txt',(AP,),parent).start()
            try:
                check._thread.join(2)
                self.assertFalse(check._thread.is_alive())
                self.assertTrue(check.target_event(AP).is_set())
                self.assertFalse(parent.is_set())
                self.assertFalse(check.target_event('00:11:22:33:44:99').is_set())
            finally:check.close()

    def test_finished_local_job_publishes_before_coordinator_pop(self):
        queue=HandshakeProcessingQueue();published=Event()
        try:
            queue.submit(('target',),DeferredHandshake(lambda:(('entry',False),)),on_ready=lambda entries:published.set())
            self.assertTrue(published.wait(2))
            self.assertTrue(queue.pending)
            self.assertEqual(queue.pop(wait=True)[1],(('entry',False),))
        finally:queue.close()
