"""Bounded live target snapshots using the existing offline log analyzer."""
from dataclasses import asdict, replace
from threading import RLock

from .auto_capture_log_analyzer import AutoCaptureLogAnalyzer


class LiveCaptureDetails:
    def __init__(self, limit=1024, line_limit=8192):
        self.limit = limit
        self.line_limit = line_limit
        self._lock = RLock()
        self._pending = ""
        self._discard_line = False
        self._targets = {}
        self._revision = 0
        self._truncated = False
        self._cache = None

    def feed(self, text):
        with self._lock:
            completed = []
            for part in text.splitlines(keepends=True):
                ended = part.endswith(("\n", "\r"))
                if not self._discard_line:
                    if len(self._pending) + len(part) > self.line_limit:
                        self._discard_line = True
                        if not self._truncated:
                            self._truncated = True
                            self._revision += 1
                        self._pending = ""
                    else:
                        self._pending += part
                if not ended:
                    continue
                if not self._discard_line:
                    self._merge(self._pending)
                    completed.append(self._pending)
                self._pending = ""
                self._discard_line = False
            return "".join(completed)

    def _merge(self, line):
        report = AutoCaptureLogAnalyzer.analyze_log_text(line)
        for target in report.targets:
            previous = self._targets.get(target.bssid)
            if previous is None:
                if len(self._targets) >= self.limit:
                    disposable = next((key for key, row in self._targets.items()
                                       if not row.has_pmkid and not row.has_eapol), None)
                    if disposable is None:
                        if not self._truncated:
                            self._truncated = True
                            self._revision += 1
                        continue
                    self._targets.pop(disposable)
                merged = replace(target)
            else:
                frames = set(previous.handshake_frames.split("+")) | set(target.handshake_frames.split("+"))
                merged = replace(previous,
                    ssid=target.ssid if target.ssid and "\ufffd" not in target.ssid else previous.ssid,
                    channel=target.channel or previous.channel,
                    has_pmkid=target.has_pmkid or previous.has_pmkid,
                    has_eapol=target.has_eapol or previous.has_eapol,
                    handshake_frames="+".join(f for f in ("M1", "M2", "M3", "M4") if f in frames),
                    details=target.details or previous.details)
            if merged != previous:
                self._targets[target.bssid] = merged
                self._revision += 1

    def snapshot(self):
        with self._lock:
            if self._cache is None or self._cache["revision"] != self._revision:
                self._cache = dict(revision=self._revision, observed=len(self._targets),
                    truncated=self._truncated, targets=tuple(asdict(row) for row in self._targets.values()
                        if row.has_pmkid or row.has_eapol))
            return self._cache
