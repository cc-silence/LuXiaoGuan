"""One bounded wait policy driven by positive device CAC observations."""
import time
from ..configuration_progress import phase


class ApStartupWait:
    SETTLE_SECONDS = 10
    MAX_CAC_SECONDS = 3600

    def __init__(self, seconds=45, *, deadline=None, hard_deadline=None, rollback=False):
        now = time.monotonic()
        self.deadline = now + seconds if deadline is None else deadline
        self.limit = min(now + self.MAX_CAC_SECONDS + seconds,
                         hard_deadline if hard_deadline is not None else float("inf"))
        self.deadline = min(self.deadline, self.limit)
        self.rollback = rollback
        self.events = {}
        self.native_cycles = {}
        self.reported = None

    def observe(self, status, identity=""):
        if not isinstance(status, dict):
            return
        if status.get("cac_active") is False and identity in self.native_cycles:
            _, generation = self.native_cycles[identity]
            self.native_cycles[identity] = (None, generation)
        if status.get("cac_active") is not True:
            return
        duration = status.get("cac_seconds")
        remaining = status.get("cac_seconds_left", duration)
        if (type(duration) is not int or not 0 < duration <= self.MAX_CAC_SECONDS
                or type(remaining) is not int or not 0 <= remaining <= duration):
            return
        event = status.get("cac_event")
        if type(event) is not int or not 0 <= event < 10**15:
            event = None
        if event is None:
            if identity not in self.native_cycles and len(self.native_cycles) >= 128:
                return
            last, generation = self.native_cycles.get(identity, (remaining, 0))
            # An explicitly increasing native countdown proves a new CAC cycle.
            # Constant or missing countdowns must never renew a stale deadline.
            explicit = "cac_seconds_left" in status
            if last is None or (explicit and remaining > last + 1):
                generation += 1
            self.native_cycles[identity] = (remaining if explicit or last is None else last, generation)
            event = ("native", generation)
        key = (identity, event)
        now = time.monotonic()
        end = now + remaining + self.SETTLE_SECONDS
        if key in self.events:
            end = min(end, self.events[key])
        elif len(self.events) >= 128:
            return
        self.events[key] = end
        self.deadline = max(self.deadline, min(end, self.limit))
        if self.reported != self.deadline:
            self.reported = self.deadline
            phase("\u6b63\u5728\u8fdb\u884c\u96f7\u8fbe\u68c0\u6d4b\uff0c\u5b8c\u6210\u540e\u81ea\u52a8\u6838\u5bf9\u70ed\u70b9",
                  deadline=self.deadline, rollback=self.rollback, cancellable=True)
