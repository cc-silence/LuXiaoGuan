"""Keep one device-only snapshot for feedback after a connection failure."""
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace

@dataclass(frozen=True)
class DeviceEvidence:
    snapshot: object
    observed_at: str

class RouterDiagnosticHistory:
    def __init__(self):
        self.latest = None
        self._key = None

    def observe(self, snapshot):
        if snapshot.connection_state.value != "connected":
            return
        device = snapshot.device
        if not any(getattr(device, key, "") for key in ("model","board_name","version")):
            return
        key = (getattr(snapshot,"host_fingerprint",""),id(device),id(snapshot.dependencies))
        if key == self._key:
            return
        self._key = key
        # Do not retain access points, credentials, task results or the session snapshot.
        value = SimpleNamespace(device=device, dependencies=tuple(snapshot.dependencies[:64]))
        self.latest = DeviceEvidence(value, datetime.now(timezone.utc).isoformat())
