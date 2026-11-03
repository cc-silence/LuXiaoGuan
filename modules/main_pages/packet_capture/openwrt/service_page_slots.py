"""Bound QObject slots ignore queued notifications after page disposal."""
from functools import wraps
from PySide6.QtCore import Slot


def active_service_slot(*types):
    def decorate(method):
        @wraps(method)
        def receive(self, *args, **kwargs):
            if not getattr(self, "_service_disposed", False):
                return method(self, *args, **kwargs)
        return Slot(*types)(receive)
    return decorate
