"""One bounded SFTP-only checker; tool hints never establish handshake validity."""
import re
from pathlib import Path
from threading import Event, Lock, Thread
from time import monotonic
from .result_converter import analyze_handshake_cap, convertible_pairs


class HandshakeReady(InterruptedError):
    pass


class CheckCancellation:
    def __init__(self, stop, parent=None, ready=None):
        self.stop, self.parent, self.ready = stop, parent, ready

    @property
    def target_complete(self):
        return self.ready is not None and self.ready()

    def is_set(self):
        return self.stop.is_set() or (self.parent is not None and self.parent.is_set()) or self.target_complete


class LiveHandshakeCheck:
    def __init__(self, manager, workspace, remote_cap, hint_path, targets, cancel_event):
        self.manager, self.remote_cap, self.hint_path = manager, remote_cap, hint_path
        self.targets = frozenset(targets)
        self.local = Path(workspace.local_dir) / (Path(str(remote_cap)).name + '.live-check')
        self.hint_local = self.local.with_suffix('.hint-check')
        self._stop, self._lock = Event(), Lock()
        self.cancel = CheckCancellation(self._stop, cancel_event)
        self._completed = set()
        self.last_error = ''
        self._thread = Thread(target=self._run, name='CaptureContentCheck', daemon=True)

    def start(self):
        self._thread.start()
        return self

    def completed(self):
        with self._lock:
            return frozenset(self._completed)

    def target_event(self, target):
        return CheckCancellation(self._stop, self.cancel.parent, lambda: target in self.completed())

    def _run(self):
        next_check = monotonic() + 15
        last_hint = ''
        settle_until = 0
        while not self.cancel.is_set():
            now = monotonic()
            try:
                self.manager.download_atomic(self.hint_path, self.hint_local, self.cancel)
                hint = self.hint_local.read_text(encoding='utf-8', errors='replace')[:128]
                match = re.search(r'(?:WPA handshake|PMKID found):\s*((?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2})', hint)
                if match and match.group(1).upper() in self.targets and hint != last_hint:
                    last_hint = hint
                    next_check = now
                    settle_until = now + 6  # A hint may precede the next capture-file flush.
            except Exception as exc:
                self.last_error = type(exc).__name__
            if self.cancel.is_set():
                break
            if now >= next_check:
                try:
                    self.manager.download_atomic(self.remote_cap, self.local, self.cancel)
                    _, pairs = analyze_handshake_cap(self.local)
                    verified = {ap.upper() for ap, _ in convertible_pairs(pairs)} & self.targets
                    with self._lock:
                        self._completed.update(verified)
                except Exception as exc:
                    self.last_error = type(exc).__name__
                if self.targets.issubset(self.completed()):
                    break
                next_check = monotonic() + (2 if monotonic() < settle_until else 15)
            if self._stop.wait(2):
                break

    def close(self):
        self._stop.set()
        self._thread.join()  # Transfers already have bounded timeout and cancellation checks.
        for path in (self.local, self.hint_local):
            path.unlink(missing_ok=True)
            path.with_suffix(path.suffix + '.part').unlink(missing_ok=True)
