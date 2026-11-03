"""Progress-aware socket reads with bounded inactivity and cancellation."""
import http.client
import io
import socket
import time
import urllib.request
from contextlib import contextmanager
from contextvars import ContextVar

BODY_IDLE_TIMEOUT = 60.0
CANCEL_CHECK_INTERVAL = 0.25
_request_cancel_event = ContextVar("dependency_request_cancel_event", default=None)


class _StreamingReader(io.RawIOBase):
    def __init__(self, connection_socket):
        super().__init__()
        self.connection_socket = connection_socket
        # The original file owns the socket reference, including Connection: close.
        # Read directly from the socket: SocketIO becomes unusable after a timeout.
        self.owner = None
        self.owner = connection_socket.makefile("rb", buffering=0)
        self.cancel_event = _request_cancel_event.get()
        self.streaming = False
        self.last_data = None

    def readable(self):
        return True

    def start_body(self):
        self.streaming = True
        self.last_data = time.monotonic()

    def readinto(self, buffer):
        if not buffer:
            return 0
        if not self.streaming:
            return self.connection_socket.recv_into(buffer)
        while True:
            if self.cancel_event is not None and self.cancel_event.is_set():
                raise InterruptedError("Download cancelled")
            remaining = BODY_IDLE_TIMEOUT - (time.monotonic() - self.last_data)
            if remaining <= 0:
                raise TimeoutError("No download data received within idle timeout")
            self.connection_socket.settimeout(min(CANCEL_CHECK_INTERVAL, remaining))
            try:
                count = self.connection_socket.recv_into(buffer)
            except socket.timeout:
                continue
            if count:
                self.last_data = time.monotonic()
            return count

    def close(self):
        try:
            if self.owner is not None:
                self.owner.close()
        finally:
            super().close()


class _ResponseSocket:
    def __init__(self, reader):
        self.reader = reader

    def makefile(self, *args, **kwargs):
        return io.BufferedReader(self.reader)


class StreamingHTTPResponse(http.client.HTTPResponse):
    def __init__(self, connection_socket, *args, **kwargs):
        self.stream_reader = _StreamingReader(connection_socket)
        try:
            super().__init__(_ResponseSocket(self.stream_reader), *args, **kwargs)
        except BaseException:
            self.stream_reader.close()
            raise

    def read(self, amt=None):
        try:
            return super().read(amt)
        except BaseException:
            # urllib drains redirect bodies outside the caller's response context.
            self.close()
            raise

    def read1(self, n=-1):
        try:
            return super().read1(n)
        except BaseException:
            self.close()
            raise


class _StreamingResponseMixin:
    response_class = StreamingHTTPResponse

    def getresponse(self):
        response = super().getresponse()
        response.stream_reader.start_body()
        return response


class StreamingHTTPConnection(_StreamingResponseMixin, http.client.HTTPConnection):
    pass


class StreamingHTTPSConnection(_StreamingResponseMixin, http.client.HTTPSConnection):
    pass


class StreamingHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(StreamingHTTPConnection, req)


class StreamingHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        options = {"context": self._context}
        if hasattr(self, "_check_hostname"):
            options["check_hostname"] = self._check_hostname
        return self.do_open(StreamingHTTPSConnection, req, **options)


class DownloadHTTPError(RuntimeError):
    """Preserve the status for safe format fallback without parsing messages."""
    def __init__(self, code, detail=""):
        self.code = code
        super().__init__(("\u4e0b\u8f7d\u5931\u8d25\uff1aHTTP %d\u3002" % code) + detail)


@contextmanager
def download_cancellation(cancel_event):
    """Scope cancellation to the complete request, including redirect responses."""
    token = _request_cancel_event.set(cancel_event)
    try:
        yield
    finally:
        _request_cancel_event.reset(token)
