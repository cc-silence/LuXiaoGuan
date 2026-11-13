"""Own a temporary radio session across consecutive batch targets."""

from .device_probe import read_monitor_status
from .radio_policy import phy_supports_channel, require_idle_phy


class BatchRadioCleanupError(RuntimeError):
    def __init__(self, errors):
        self.cleanup_errors = tuple(errors)
        super().__init__("; ".join(errors))


class HandshakeBatchRadio:
    def __init__(self, backend, workspace):
        self.backend = backend
        self.workspace = workspace
        self.band = None
        self.record = None
        self.takeover = None

    def acquire(self, session_id, settings, target, round_no, index, cancel_event, message_callback):
        backend = self.backend
        if self.record is not None and self.band == target.band:
            if backend.radio_session.record != self.record:
                raise RuntimeError("Batch radio session ownership changed")
            require_idle_phy(backend.command_runner, self.record.radio, cancel_event,
                             self.record.interface)
            if read_monitor_status(backend.command_runner, self.record.interface, cancel_event) != "ready":
                raise RuntimeError("Batch capture interface is no longer ready")
            if not phy_supports_channel(backend.command_runner, self.record.radio, target.channel, cancel_event):
                raise RuntimeError("Batch radio does not support the target channel")
            return self.record
        if self.record is not None or self.takeover is not None:
            errors = self.release(message_callback)
            if errors:
                raise BatchRadioCleanupError(errors)
        phy = backend._find_scan_radio(target.band, cancel_event, target.channel,
            bool(settings.get("idle_radio_only", False)), message_callback)
        self.takeover = backend._maybe_takeover(self.workspace, session_id, phy, settings,
            cancel_event, f"r{round_no}t{index}", target_channel=target.channel,
            expected_band=target.band, message_callback=message_callback)
        self.record = backend.radio_session.create(session_id, phy, target.band,
            cancel_event=cancel_event, idle_radio_only=bool(settings.get("idle_radio_only", False)))
        self.band = target.band
        return self.record

    def release(self, message_callback=None):
        errors = self.backend._restore_task_wireless(self.workspace, self.takeover, message_callback)
        if not errors:
            self.record = None
            self.takeover = None
            self.band = None
        return errors
