"""Bounded HTTP resume for immutable signed files; route policy belongs to callers."""
import hashlib
import re
import stat
from pathlib import Path
import requests


class PartialDownload:
    def __init__(self, path, size, sha256, cancel=lambda: False):
        self.path, self.size, self.sha256 = Path(path), size, sha256
        self.cancel, self.offset = cancel, 0

    def _check_cancel(self):
        if self.cancel():
            raise InterruptedError("Download cancelled")

    def _valid_hash(self):
        digest = hashlib.sha256()
        with self.path.open("rb") as stream:
            for block in iter(lambda: stream.read(65536), b""):
                self._check_cancel()
                digest.update(block)
        return digest.hexdigest() == self.sha256

    def prepare(self):
        """Reuse a complete verified file or retain the existing partial offset."""
        self._check_cancel()
        try:
            info = self.path.lstat()
        except FileNotFoundError:
            self.offset = 0
            return False
        if not stat.S_ISREG(info.st_mode) or self.path.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Download cache is not a regular file")
        self.offset = info.st_size
        if self.offset > self.size or (self.offset == self.size and not self._valid_hash()):
            self.path.unlink()
            self.offset = 0
        return self.offset == self.size

    def headers(self):
        headers = {"Accept-Encoding": "identity"}
        if self.offset:
            headers["Range"] = "bytes=" + str(self.offset) + "-"
        return headers

    def receive(self, response, progress, check_deadline=lambda: None):
        """Validate response offsets before opening, then stream and verify."""
        if response.status_code not in (200, 206):
            raise ValueError("Download HTTP " + str(response.status_code))
        encoding = response.headers.get("Content-Encoding", "")
        if encoding and encoding.lower() != "identity":
            raise ValueError("Encoded download cannot be resumed safely")
        start = self.offset if response.status_code == 206 else 0
        end = self.size - 1
        if response.status_code == 206:
            value = response.headers.get("Content-Range", "")
            match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", value)
            if not match:
                raise ValueError("Invalid download Content-Range")
            first, end, total = map(int, match.groups())
            if first != self.offset or total != self.size or not first <= end < total:
                raise ValueError("Download range differs from signed file")
        length = response.headers.get("Content-Length")
        if length is not None and (not length.isdigit() or int(length) != end - start + 1):
            raise ValueError("Download length differs from signed file")
        self.offset = start
        progress(start)
        with self.path.open("ab" if start else "wb") as stream:
            for block in response.iter_content(65536):
                self._check_cancel()
                check_deadline()
                if self.offset + len(block) > end + 1:
                    stream.close()
                    self.path.unlink(missing_ok=True)
                    raise ValueError("Download exceeds signed size or response range")
                stream.write(block)
                self.offset += len(block)
                progress(self.offset)
        self._check_cancel()
        if self.offset != self.size:
            raise requests.ConnectionError("Download incomplete; partial file retained")
        if not self._valid_hash():
            self.path.unlink(missing_ok=True)
            self.offset = 0
            raise ValueError("Downloaded file fingerprint mismatch")
        return self.offset
