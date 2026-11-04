"""Use real Paramiko buffering to verify resume after a partial failed read."""
import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock
from paramiko import Message
from paramiko.sftp import CMD_READ, CMD_CLOSE, CMD_DATA, CMD_STATUS, SFTPError
from threading import Event
from paramiko.sftp_file import SFTPFile
from modules.capture_services.openwrt.wps_scan import WpsScanReader
from tests.test_wps_scan import pcap, beacon, MAC


class RealParamikoReadTests(unittest.TestCase):
    def test_partial_buffer_timeout_reopens_at_last_committed_offset(self):
        data = pcap(*[beacon(True)]*1000)
        peer = Mock()
        reads, closes, fail = [0], [0], [True]
        streams = []
        def request(operation, *args):
            if operation == CMD_CLOSE:
                closes[0] += 1
                return CMD_STATUS, Message()
            self.assertEqual(operation, CMD_READ)
            reads[0] += 1
            if reads[0] == 2 and fail[0]:
                raise TimeoutError("controlled second-packet timeout")
            _, offset, size = args
            chunk = data[int(offset):int(offset)+int(size)]
            if not chunk:
                raise EOFError()
            message = Message(); message.add_string(chunk)
            return CMD_DATA, Message(message.asbytes())
        peer._request.side_effect = request
        peer.stat.return_value = SimpleNamespace(st_size=len(data))
        def opened(*args):
            stream = SFTPFile(peer,b"owned",mode="rb",bufsize=0)
            streams.append(stream)
            return stream
        peer.open.side_effect = opened
        ssh = Mock(); ssh.open_sftp.return_value = peer
        ssh.transfer.side_effect = lambda *args,**options: nullcontext()
        reader = WpsScanReader()
        reader.sync(ssh,"/tmp/owned/scan-01.cap")
        self.assertEqual(reader.offset,0)
        self.assertFalse(reader.points)
        ssh.drop_sftp.assert_called_once()
        self.assertTrue(streams[0]._closed)
        fail[0] = False
        reader.sync(ssh,"/tmp/owned/scan-01.cap",final=True)
        self.assertEqual(reader.offset,len(data))
        self.assertEqual(reader.points,{MAC:True})
        self.assertFalse(reader.pending)
        self.assertEqual(closes[0],2)
        self.assertTrue(all(stream._closed for stream in streams))

    def test_cancel_during_read_survives_close_protocol_error_without_double_close(self):
        for close_fails in (False, True):
            with self.subTest(close_fails=close_fails):
                data=pcap(beacon(True)); event=Event(); closes=[0]; streams=[]
                peer=Mock()
                def request(operation,*args):
                    if operation==CMD_CLOSE:
                        closes[0]+=1
                        if close_fails:
                            raise SFTPError("controlled close protocol failure")
                        return CMD_STATUS,Message()
                    self.assertEqual(operation,CMD_READ)
                    event.set()
                    _,offset,size=args
                    packet=data[int(offset):int(offset)+int(size)]
                    if not packet: raise EOFError()
                    message=Message();message.add_string(packet)
                    return CMD_DATA,Message(message.asbytes())
                peer._request.side_effect=request
                peer.stat.return_value=SimpleNamespace(st_size=len(data))
                def opened(*args):
                    stream=SFTPFile(peer,b"owned",mode="rb",bufsize=0)
                    streams.append(stream);return stream
                peer.open.side_effect=opened
                ssh=Mock();ssh.open_sftp.return_value=peer
                ssh.transfer.side_effect=lambda *args,**options:nullcontext()
                with self.assertRaises(InterruptedError):
                    WpsScanReader().sync(ssh,"/tmp/owned/scan-01.cap",event,final=True)
                self.assertEqual(closes[0],1)
                self.assertTrue(streams[0]._closed)
                streams[0].close()
                self.assertEqual(closes[0],1)
                ssh.close.assert_not_called()
                if close_fails:ssh.drop_sftp.assert_called_once()
