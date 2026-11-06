"""Bounded candidate timing and WPS reply liveness from native log observations."""
import hashlib
import re
import time
from .wps_observation import _TRY, _ANSI

_REPLY = re.compile(r"received (?:identity request|m[1357] message|wsc nack|eap_failure message)\b", re.I)
NO_REPLY_SECONDS = 60


class WpsCandidateTiming:
    def __init__(self):
        self.current_pin = ""
        self.candidate_started = None
        self.first_started = None
        self.last_reply = None
        self.transitions = 0
        self.previous = []
        self.frozen_at = None

    def begin_phase(self):
        self.previous = []
        self.last_reply = None

    def feed(self, text, now=None):
        now = time.monotonic() if now is None else now
        lines = _ANSI.sub("", str(text or "")[-16384:]).splitlines()[-100:]
        if not lines:
            return
        hashes = [hashlib.sha256(line.encode()).digest() for line in lines]
        overlap = next((size for size in range(min(len(self.previous), len(hashes)), 0, -1)
                        if self.previous[-size:] == hashes[:size]), 0)
        self.previous = hashes
        for line in lines[overlap:]:
            match = _TRY.search(line)
            if match and match[1] != self.current_pin:
                if self.current_pin:
                    self.transitions += 1
                self.current_pin = match[1]
                self.candidate_started = now
                self.last_reply = now
                if self.first_started is None:
                    self.first_started = now
            if match and self.last_reply is None:
                self.last_reply = now
            if self.current_pin and _REPLY.search(line):
                self.last_reply = now

    def snapshot(self, now=None):
        now = self.frozen_at if self.frozen_at is not None else time.monotonic() if now is None else now
        if self.candidate_started is None:
            return 0.0, None
        elapsed = max(0.0, now-self.candidate_started)
        duration = max(0.0, now-self.first_started)
        rate = self.transitions * 60.0 / duration if self.transitions and duration > 0 else None
        return elapsed, rate

    def reply_timed_out(self, now=None):
        now = time.monotonic() if now is None else now
        return self.last_reply is not None and now-self.last_reply >= NO_REPLY_SECONDS

    def freeze(self, now=None):
        self.frozen_at = time.monotonic() if now is None else now
