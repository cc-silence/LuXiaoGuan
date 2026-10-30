"""Local capture evidence recorder; never executes router operations."""
import hashlib
import time
from PySide6.QtCore import QObject, Slot
from modules.log_history import current_history

CATEGORY = 'auto-capture'

def device_key(snapshot):
    fingerprint = getattr(snapshot, 'host_fingerprint', '')
    return hashlib.sha256(fingerprint.encode()).hexdigest() if isinstance(fingerprint,str) and fingerprint else ''

def context_for(owner, masker):
    from modules.capture_services.openwrt.diagnostic_context import router_context_json
    from PySide6.QtWidgets import QApplication
    backend = owner.openwrt_backend
    app = QApplication.instance()
    return router_context_json(owner.openwrt_snapshot, masker,
        radio_evidence=backend.command_runner.radio_diagnostics.snapshot(),
        operation_evidence=backend.command_runner.operation_diagnostics.snapshot(),
        signal_snapshot=owner.router_signals.snapshot,
        device_history=owner.diagnostic_history,
        signal_setup_snapshot=owner.router_signals.setup.snapshot,
        app_version=app.applicationVersion() if app else '')

class AutoCaptureDiagnostics(QObject):
    """Outlives pages and binds each radio stream to its originating device."""
    def __init__(self, service):
        super().__init__(service)
        self.service = service
        self.device = device_key(service.owner.openwrt_snapshot)
        self.radio_devices = {}
        self._partial_lines = {}
        self._context_time = 0
        service.radioStreamOutput.connect(self.stream)
        service.radioStateChanged.connect(self.state)
        service.owner.openwrtSnapshotChanged.connect(self.snapshot_changed)

    def begin(self, radio):
        key = device_key(self.service.owner.openwrt_snapshot) or self.device
        self.device = key
        self.radio_devices[radio] = key
        self._partial_lines.pop(radio, None)
        if current_history():
            current_history().auto_capture_device = key
        history = current_history()
        if history:
            history.reset_channel(CATEGORY, radio, key)
            history.reset_channel(CATEGORY, radio + '-diagnostic', key)
            self.capture_context(force=True)

    def capture_context(self, force=False):
        history = current_history()
        if not history or (not force and time.monotonic()-self._context_time < 5):
            return
        self._context_time = time.monotonic()
        try:
            history.context(CATEGORY, context_for(self.service.owner,history.masker), device=self.device)
        except Exception as exc:
            history.last_error = type(exc).__name__

    @Slot(object)
    def snapshot_changed(self, snapshot):
        key = device_key(snapshot)
        self.device = key or self.device
        history = current_history()
        if history:
            history.auto_capture_device = self.device
        self.capture_context()

    @Slot(str,str)
    def stream(self, radio, text):
        history = current_history()
        if history:
            text = self._partial_lines.pop(radio, '') + text
            complete, separator, partial = text.rpartition('\n')
            if separator:
                history.append(CATEGORY,complete,channel=radio,device=self.radio_devices.get(radio,self.device))
            else:
                partial = text
            if len(partial) > 8192:
                history.append(CATEGORY,'[oversized partial line omitted]',channel=radio,device=self.radio_devices.get(radio,self.device))
            else:
                self._partial_lines[radio] = partial

    @Slot(str,str)
    def state(self, radio, state):
        history = current_history()
        if history:
            if state in ('idle','error'):
                partial = self._partial_lines.pop(radio, '')
                if partial:
                    history.append(CATEGORY,partial,channel=radio,device=self.radio_devices.get(radio,self.device))
            history.append(CATEGORY,'state: ' + state,channel=radio + '-diagnostic',
                           device=self.radio_devices.get(radio,self.device))
            self.capture_context(force=state in ('idle','error'))

    def recovery(self, radio, text, workspace=None):
        history = current_history()
        if history:
            history.append(CATEGORY,text,'diagnostic',channel=radio + '-diagnostic',
                           device=self.radio_devices.get(radio,self.device))
            if workspace is not None:
                from modules.log_services import DiagnosticReference
                ref = DiagnosticReference(workspace.local_dir,workspace.local_dir.parent,'session')
                history.references(CATEGORY,[ref],device=self.radio_devices.get(radio,self.device),channel=radio)
