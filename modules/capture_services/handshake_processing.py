"""Bounded local processing; radio ownership stays on the capture coordinator."""
from collections import deque
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class DeferredHandshake:
    finish: Callable


class HandshakeProcessingQueue:
    """One local worker and at most two downloaded groups including running work."""

    capacity = 2

    def __init__(self):
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="WinBoxResult")
        self._pending = deque()

    @property
    def full(self):
        return len(self._pending) >= self.capacity

    def submit(self, group, job, *, on_ready=None):
        if self.full:
            raise RuntimeError("Local result queue is full.")
        def finish():
            entries = job.finish()
            if on_ready is not None:
                try:
                    on_ready(entries)
                except Exception:
                    from modules.capture_services.capture_logging import LOGGER
                    LOGGER.exception("Immediate result publication failed; retained for coordinator delivery")
            return entries
        self._pending.append((tuple(group), self._pool.submit(finish)))

    @property
    def pending(self):
        return bool(self._pending)

    def pop(self, *, wait=False, cancel_event=None):
        if not self._pending or (not wait and not self._pending[0][1].done()):
            return None
        group, future = self._pending[0]
        if wait and cancel_event is not None:
            while not future.done():
                if cancel_event.is_set():
                    return None  # Leave downloaded work queued for post-restore draining.
                try:
                    future.result(timeout=0.1)
                except FutureTimeout:
                    continue
                except Exception:
                    break
        self._pending.popleft()
        try:
            entries = future.result()
            if len(entries) != len(group):
                raise ValueError("Local result count differs from capture group.")
        except Exception as exc:
            from .backends.openwrt_backend import HandshakeTargetResult
            entries = tuple((HandshakeTargetResult(target, error=str(exc) or type(exc).__name__,
                             error_code="processing_failed"), False) for target in group)
        return group, entries

    def close(self):
        self._pool.shutdown(wait=True)
