"""Asynchronously choose a default without overriding a user's actions."""
from threading import Event
from uuid import uuid4
from PySide6.QtCore import QObject
from shiboken6 import isValid
from modules.capture_services.openwrt.scan_default_band import choose_scan_band
from modules.capture_services.capture_logging import LOGGER


class ScanDefaultBandController(QObject):
    def __init__(self, page):
        super().__init__(page)
        self.page = page
        self.service = page.service
        self._event = None
        self._want = False
        page.tools.band_combo.activated.connect(self.cancel)
        page.tools.actionRequested.connect(self.cancel)

    def enter(self):
        self._want = True
        if self._event is not None:
            self._event.set()
            return
        self._start()

    def cancel(self, *_args):
        self._want = False
        if self._event is not None:
            self._event.set()

    def _start(self):
        service = self.service
        snapshot = service.openwrt_snapshot
        if (not self._want or service._shutting_down or service.task_controller.busy
                or snapshot.connection_state.value != "connected"):
            self._want = False
            return
        backend = service.openwrt_backend
        ssh = backend.ssh_client
        fingerprint = ssh.host_fingerprint
        client = ssh._client
        if not fingerprint or client is None or not ssh.connected:
            self._want = False
            return
        self._want = False
        event = Event()
        self._event = event
        initial = self.page.tools.band_combo.currentText()
        session_id = "scan-band-" + uuid4().hex

        def finished(band):
            self._finish(event, fingerprint, client, initial, band, "")

        def failed(summary, _detail):
            self._finish(event, fingerprint, client, initial, "", summary)

        try:
            service._start_openwrt_worker(session_id,
                lambda cancel: choose_scan_band(backend.command_runner, backend._find_scan_radio, cancel),
                event, finished, failed)
            service.activityChanged.emit()
        except Exception as exc:
            self._event = None
            LOGGER.warning("Scan default band worker could not start: %s", type(exc).__name__)

    def _finish(self, event, fingerprint, client, initial, band, error):
        if self._event is not event:
            return
        self._event = None
        if error and not event.is_set():
            LOGGER.info("Scan default band probe did not complete; original selection retained")
        service = self.service
        ssh = service.openwrt_backend.ssh_client
        if (isValid(self.page) and not event.is_set() and not service._shutting_down
                and not service.task_controller.busy and ssh.connected
                and ssh._client is client and ssh.host_fingerprint == fingerprint
                and service.openwrt_snapshot.connection_state.value == "connected"
                and self.page.tools.band_combo.currentText() == initial
                and band in ("2.4G", "5G")):
            if band != initial:
                self.page.tools.band_combo.setCurrentText(band)
                self.page.settingsChanged.emit()
        if self._want and isValid(self.page):
            self._start()
