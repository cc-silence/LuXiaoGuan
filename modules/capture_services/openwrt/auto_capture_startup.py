"""Bounded preparation barrier; failed lanes release peers only after recovery."""
import threading
import time


class CaptureStartupCoordinator:
    def __init__(self, expected_radios):
        self._expected = set(expected_radios)
        self._ready = set()
        self._failed = {}
        self._settled = set()
        self._cond = threading.Condition()
        self._cancelled = False

    def mark_ready(self, radio):
        with self._cond:
            if radio not in self._expected:
                raise ValueError("Unexpected radio in capture startup")
            if not self._cancelled and radio not in self._failed:
                self._ready.add(radio)
            self._cond.notify_all()

    def mark_failed(self, radio, error):
        with self._cond:
            if radio not in self._expected:
                raise ValueError("Unexpected radio in capture startup")
            self._ready.discard(radio)
            self._settled.discard(radio)
            self._failed[radio] = error
            self._cond.notify_all()

    def cleanup_finished(self, radio, confirmed):
        with self._cond:
            if not confirmed:
                self._cancelled = True
            elif radio in self._failed:
                self._settled.add(radio)
            self._cond.notify_all()

    def cancel(self):
        with self._cond:
            self._cancelled = True
            self._cond.notify_all()

    def wait_ready(self, radio, timeout=30.0, cancel_event=None):
        deadline = time.monotonic() + timeout
        with self._cond:
            if radio not in self._expected:
                raise ValueError("Unexpected radio in capture startup")
            while True:
                if self._cancelled or (cancel_event is not None and cancel_event.is_set()):
                    self._cancelled = True
                    self._cond.notify_all()
                    return False
                if self._ready | self._settled == self._expected:
                    return radio in self._ready
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._cancelled = True
                    self._cond.notify_all()
                    return False
                self._cond.wait(timeout=min(remaining, 0.5))
