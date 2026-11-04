"""Bounded worker-to-main-thread decisions for package candidates."""
from dataclasses import dataclass,field
from threading import Event,Lock
import time
from PySide6.QtCore import QObject,Signal

@dataclass(eq=False)
class PackageDecision:
    review: object
    completed: Event=field(default_factory=Event)
    lock: Lock=field(default_factory=Lock)
    accepted: bool=False
    error: str=""

    def answer(self,accepted,error=""):
        with self.lock:
            if self.completed.is_set():return
            self.accepted=bool(accepted)
            self.error=str(error)
            self.completed.set()

class PackageConfirmationGate(QObject):
    requested=Signal(object)
    closed=Signal(object)

    def __init__(self,parent=None):
        super().__init__(parent)
        self.available=False
        self.pending=None

    def cancel_pending(self):
        if self.pending is not None:self.pending.answer(False)

    def request(self,review,cancel,connected,timeout=300):
        from .openwrt.signal_manager import SignalError
        if not self.available:
            raise SignalError('dependencies','\u5f53\u524d\u6ca1\u6709\u53ef\u7528\u7684\u786e\u8ba4\u7a97\u53e3\uff0c\u672a\u5b89\u88c5 hcx \u5019\u9009\u5305\u3002')
        if self.pending is not None:raise RuntimeError('Package confirmation already pending')
        request=PackageDecision(review)
        self.pending=request
        deadline=time.monotonic()+timeout
        try:
            self.requested.emit(request)
            while True:
                if cancel is not None and cancel.is_set():
                    raise SignalError('cancelled','\u4f9d\u8d56\u5b89\u88c5\u5df2\u53d6\u6d88\u3002')
                if not connected():
                    raise SignalError('disconnected','\u8fde\u63a5\u5df2\u53d8\u5316\uff0c\u672a\u5b89\u88c5\u5019\u9009\u5305\u3002')
                if request.completed.is_set():
                    if request.error:raise SignalError("dependencies",request.error)
                    return request.accepted
                remaining=deadline-time.monotonic()
                if remaining<=0:
                    raise SignalError('candidate_declined','\u786e\u8ba4\u5df2\u8d85\u65f6\uff0c\u672a\u5b89\u88c5 hcx \u5019\u9009\u5305\u3002')
                request.completed.wait(min(0.2,remaining))
        finally:
            request.answer(False)
            if self.pending is request:self.pending=None
            self.closed.emit(request)
