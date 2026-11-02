"""Task cancellation with immediate callbacks for native OS wait handles."""
from threading import Event,RLock


class CancellationEvent(Event):
    def __init__(self):
        super().__init__();self._callback_lock=RLock();self._callbacks=set()
    def subscribe(self,callback):
        with self._callback_lock:
            self._callbacks.add(callback)
            if self.is_set():callback()
        def unsubscribe():
            with self._callback_lock:self._callbacks.discard(callback)
        return unsubscribe
    def set(self):
        with self._callback_lock:
            super().set()
            for callback in tuple(self._callbacks):callback()
