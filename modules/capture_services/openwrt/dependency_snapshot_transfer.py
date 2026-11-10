"""Bounded binary transfer over SSH exec, without SFTP or remote extra tools."""
import io
import socket
import time
from .command_builder import quote_argument as q

MAX_BYTES = 512 * 1024 * 1024


class SnapshotGuard:
    def __init__(self, ssh, cancel, seconds=900):
        self.ssh, self.cancel = ssh, cancel
        self.client, self.fingerprint = ssh._client, ssh.host_fingerprint
        self.deadline = time.monotonic() + seconds

    def check(self):
        if self.cancel.is_set():
            raise InterruptedError("Dependency backup/restore cancelled")
        if time.monotonic() >= self.deadline:
            raise TimeoutError("Dependency backup/restore timed out")
        if (not self.ssh.connected or self.ssh._client is not self.client
                or self.ssh.host_fingerprint != self.fingerprint):
            raise RuntimeError("Router connection changed; stopped old task")


class SnapshotTransfer:
    def __init__(self, guard, progress):
        self.guard, self.progress = guard, progress

    def read(self, command, limit=16 * 1024 * 1024):
        target = io.BytesIO()
        self._exchange(command, target=target, limit=limit)
        return target.getvalue()

    def download(self, remote, local, size, digest):
        import hashlib
        if not 0 <= size <= MAX_BYTES:
            raise ValueError("Invalid dependency archive size")
        with local.open("xb") as target:
            self._exchange("cat " + q(remote), target=target, limit=size)
        if local.stat().st_size != size:
            raise ValueError("Incomplete dependency archive transfer")
        with local.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest() if hasattr(hashlib, "file_digest") else _digest(stream)
        if actual != digest:
            raise ValueError("Dependency archive checksum mismatch")

    def upload(self, local, remote):
        size = local.stat().st_size
        if size > MAX_BYTES:
            raise ValueError("Dependency archive exceeds transfer limit")
        with local.open("rb") as source:
            self._exchange("umask 077; cat > " + q(remote), source=source, limit=4096, total=size)

    def _exchange(self, command, *, source=None, target=None, limit=4096, total=0):
        self.guard.check()
        streams = self.guard.ssh.exec_command(command, timeout=10, cancel_event=self.guard.cancel)
        stdin, stdout, stderr, channel = streams
        count, sent, errors, output = 0, 0, bytearray(), bytearray()
        pending, eof, last = b"", source is None, 0.0
        try:
            channel.settimeout(0.2)
            if source is None:
                channel.shutdown_write()
            while True:
                self.guard.check()
                changed = False
                if source is not None and not eof:
                    if not pending:
                        pending = source.read(65536)
                        if not pending:
                            channel.shutdown_write()
                            eof = True
                    if pending:
                        try:
                            n = channel.send(pending)
                            if n <= 0:
                                raise OSError("SSH upload stopped")
                            pending = pending[n:]
                            sent += n
                            changed = True
                        except socket.timeout:
                            pass
                while channel.recv_ready():
                    self.guard.check()
                    chunk = channel.recv(65536)
                    if not chunk:
                        break
                    count += len(chunk)
                    if count > limit:
                        raise ValueError("Remote dependency data exceeds limit")
                    if target is not None:
                        target.write(chunk)
                    else:
                        output.extend(chunk)
                    changed = True
                while channel.recv_stderr_ready():
                    self.guard.check()
                    chunk = channel.recv_stderr(4096)
                    if not chunk:
                        break
                    errors.extend(chunk)
                    if len(errors) > 16384:
                        raise ValueError("Remote dependency error output exceeds limit")
                    changed = True
                now = time.monotonic()
                if now - last >= 0.15:
                    self.progress(dict(stage="\u6b63\u5728\u4f20\u8f93\u4f9d\u8d56\u6587\u4ef6",
                                       completed=sent if source else count, total=total or limit))
                    last = now
                if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                    code = channel.recv_exit_status()
                    if code != 0 or (source is not None and (not eof or sent != total)):
                        raise OSError("SSH dependency transfer failed (exit %s)" % code)
                    break
                if not changed:
                    self.guard.cancel.wait(0.02)
            self.guard.check()
        finally:
            channel.close()
            for stream in (stdin, stdout, stderr):
                stream.close()


def _digest(stream):
    import hashlib
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()

