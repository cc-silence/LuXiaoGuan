import io
import socket
import tempfile
import unittest
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock
from modules.capture_services.openwrt.dependency_snapshot_transfer import SnapshotGuard, SnapshotTransfer


class Channel:
    def __init__(self, output=b"", error=b"", status=0, send_step=3):
        self.output, self.error, self.status = output, error, status
        self.sent, self.write_closed, self.closed = bytearray(), False, False
        self.send_step = send_step

    def settimeout(self, _): pass
    def shutdown_write(self): self.write_closed = True
    def send(self, data):
        value = data[:self.send_step]
        self.sent.extend(value)
        return len(value)
    def recv_ready(self): return bool(self.output)
    def recv(self, size):
        data, self.output = self.output[:size], self.output[size:]
        return data
    def recv_stderr_ready(self): return bool(self.error)
    def recv_stderr(self, size):
        data, self.error = self.error[:size], self.error[size:]
        return data
    def exit_status_ready(self): return self.write_closed
    def recv_exit_status(self): return self.status
    def close(self): self.closed = True


class TransferTests(unittest.TestCase):
    def create(self, channel):
        ssh = SimpleNamespace(_client=object(), host_fingerprint="fingerprint", connected=True)
        streams = tuple(io.BytesIO() for _ in range(3))
        ssh.exec_command = Mock(return_value=(*streams, channel))
        event = Event()
        transfer = SnapshotTransfer(SnapshotGuard(ssh,event),Mock())
        return transfer, ssh, event, streams

    def test_binary_read_and_cleanup_without_sftp(self):
        channel = Channel(b"\x00\xffdata")
        transfer, ssh, _, streams = self.create(channel)
        self.assertEqual(transfer.read("cat /tmp/owned"), b"\x00\xffdata")
        self.assertTrue(channel.closed)
        self.assertTrue(all(stream.closed for stream in streams))
        self.assertFalse(hasattr(ssh, "open_sftp"))

    def test_partial_send_and_eof(self):
        channel = Channel()
        transfer, _, _, _ = self.create(channel)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"data"; path.write_bytes(b"\x00\xffbinary"*100)
            transfer.upload(path,"/tmp/private")
            self.assertEqual(channel.sent,path.read_bytes())
        self.assertTrue(channel.write_closed)
        self.assertTrue(channel.closed)

    def test_output_limit_and_nonzero_exit(self):
        for channel, limit in ((Channel(b"12345"),4),(Channel(status=1),20)):
            transfer,_,_,streams=self.create(channel)
            with self.assertRaises((ValueError,OSError)):
                transfer.read("cat /tmp/private",limit=limit)
            self.assertTrue(channel.closed)
            self.assertTrue(all(stream.closed for stream in streams))

    def test_cancel_and_changed_connection_before_open(self):
        transfer,ssh,event,_=self.create(Channel())
        event.set()
        with self.assertRaises(InterruptedError):
            transfer.read("cat /tmp/private")
        ssh.exec_command.assert_not_called()
        event.clear();ssh._client=object()
        with self.assertRaises(RuntimeError):
            transfer.read("cat /tmp/private")
        ssh.exec_command.assert_not_called()

    def test_send_timeout_then_partial_write_completes(self):
        channel=Channel();transfer,_,_,streams=self.create(channel)
        send=channel.send;first=True
        def sometimes_blocked(data):
            nonlocal first
            if first:
                first=False
                raise socket.timeout("fixture backpressure")
            return send(data)
        channel.send=sometimes_blocked
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"payload";path.write_bytes(b"binary"*20)
            transfer.upload(path,"/tmp/private")
            self.assertEqual(channel.sent,path.read_bytes())
        self.assertTrue(channel.closed)
        self.assertTrue(all(stream.closed for stream in streams))

    def test_cancel_during_read_closes_all_resources(self):
        channel=Channel(b"payload")
        transfer,_,event,streams=self.create(channel)
        receive=channel.recv
        def cancel_during_receive(size):
            event.set()
            return receive(size)
        channel.recv=cancel_during_receive
        with self.assertRaises(InterruptedError):transfer.read("cat /tmp/private")
        self.assertTrue(channel.closed)
        self.assertTrue(all(stream.closed for stream in streams))

    def test_remote_early_success_does_not_accept_incomplete_upload(self):
        channel=Channel();channel.exit_status_ready=lambda:True
        transfer,_,_,streams=self.create(channel)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"payload";path.write_bytes(b"incomplete upload")
            with self.assertRaises(OSError):transfer.upload(path,"/tmp/private")
        self.assertTrue(channel.closed)
        self.assertTrue(all(stream.closed for stream in streams))

    def test_truncated_or_wrong_digest_download_is_not_accepted(self):
        for size,digest in ((6,"a"*64),(5,"0"*64)):
            channel=Channel(b"hello");transfer,_,_,streams=self.create(channel)
            with tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(ValueError):
                    transfer.download("/tmp/private",Path(directory)/"payload",size,digest)
            self.assertTrue(channel.closed)
            self.assertTrue(all(stream.closed for stream in streams))

    def test_large_remote_error_is_bounded_and_closed(self):
        channel=Channel(error=b"x"*17000);transfer,_,_,streams=self.create(channel)
        with self.assertRaisesRegex(ValueError,"error output exceeds limit"):
            transfer.read("cat /tmp/private")
        self.assertTrue(channel.closed)
        self.assertTrue(all(stream.closed for stream in streams))

    def test_deadline(self):
        transfer,ssh,_,_=self.create(Channel())
        transfer.guard.deadline=0
        with self.assertRaises(TimeoutError):
            transfer.read("cat /tmp/private")
        ssh.exec_command.assert_not_called()

