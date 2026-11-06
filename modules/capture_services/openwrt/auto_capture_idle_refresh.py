"""Coalesced, event-driven read-only admission refresh after capture cleanup."""
import threading
from PySide6.QtCore import QObject, Signal, Slot, Qt
from modules.capture_services.capture_logging import LOGGER
from .radio_usage import read_radio_usage


class RadioIdleRefresh(QObject):
    ready = Signal(object, object)

    def __init__(self, service):
        super().__init__(service)
        self.service = service
        self.pending = False
        self.running = None
        self.revision = 0
        self.ready.connect(self._complete, Qt.QueuedConnection)
        service.radioStateChanged.connect(self.capture_state_changed)
        service.owner.openwrtSnapshotChanged.connect(self.snapshot_changed)

    def connected(self):
        service = self.service
        snapshot = getattr(service.owner, 'openwrt_snapshot', None)
        state = getattr(snapshot, 'connection_state', None)
        return (not service._disposed and getattr(state, 'value', state) == 'connected'
                and service.ssh.connected and service.ssh._client is not None)

    def request(self):
        self.revision += 1
        self.pending = self.connected()
        return self._start()

    @Slot(str, str)
    def capture_state_changed(self, radio, state):
        if state in {'idle', 'error'}:
            self.request()
        elif state in {'starting', 'stopping'}:
            # A read spanning a resource mutation cannot certify the later state.
            self.revision += 1

    @Slot(object)
    def snapshot_changed(self, snapshot):
        if not self.connected():
            self.pending = False
            self.revision += 1
            if self.running is not None:
                self.running[2].set()
        elif self.running is not None and (
                self.service.is_busy or getattr(getattr(self.service.owner, 'task_controller', None), 'busy', False) is True):
            # Even a task that finishes before this read returns invalidates it.
            self.revision += 1
            self.pending = True
            self.running[2].set()
        elif self.pending:
            self._start()

    def _start(self):
        service = self.service
        if not self.pending or self.running is not None or not self.connected():
            return False
        if service.is_busy or getattr(getattr(service.owner, 'task_controller', None), 'busy', False) is True:
            return False
        snapshot = service.owner.openwrt_snapshot
        fallback = {r.name: None for r in getattr(getattr(snapshot, 'device', None), 'radios', ())}
        cancel = threading.Event()
        job = (self.revision, service.ssh._client, cancel)
        self.running = job
        self.pending = False
        service._idle_probe_cancel = cancel
        service._is_probing_idle = True

        def work():
            result = fallback
            try:
                # No task is running: remaining monitors must remain conflicts.
                result = read_radio_usage(service.runner, cancel)
            except Exception as error:
                LOGGER.debug('Probe radio idle status failed: %s', error)
            self.ready.emit(job, result)

        try:
            threading.Thread(target=work, name='ProbeRadioIdle', daemon=True).start()
        except Exception:
            self.running = None
            service._is_probing_idle = False
            raise
        return True

    @Slot(object, object)
    def _complete(self, job, result):
        if self.running is not job:
            return
        service = self.service
        self.running = None
        service._is_probing_idle = False
        valid = (self.connected() and service.ssh._client is job[1]
                 and not job[2].is_set() and job[0] == self.revision)
        busy = service.is_busy or getattr(getattr(service.owner, 'task_controller', None), 'busy', False) is True
        if valid and busy:
            self.pending = True
        elif valid and result:
            service.owner.update_openwrt_radio_idle_status(result)
            service.radioIdleStatusUpdated.emit(result)
        if self.pending:
            self._start()
