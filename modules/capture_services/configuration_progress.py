"""Task-local configuration phases; the clock never controls task completion."""
from contextlib import contextmanager
from contextvars import ContextVar
import time

_observer = ContextVar("router_configuration_observer", default=None)


@contextmanager
def observing(callback):
    token = _observer.set(callback)
    try:
        yield
    finally:
        _observer.reset(token)


_reporter = ContextVar("router_configuration_progress", default=None)


@contextmanager
def reporting(callback):
    token = _reporter.set(callback)
    try:
        yield
    finally:
        _reporter.reset(token)


def phase(stage, *, seconds=None, deadline=None, rollback=False, **extra):
    callback = _reporter.get()
    observer = _observer.get()
    if callback is None and observer is None:
        return
    if deadline is None and seconds is not None:
        deadline = time.monotonic() + seconds
    value = dict(configuration_apply=True, stage=stage, deadline=deadline,
                 rollback=bool(rollback), power_warning=True, uncancellable=True, **extra)
    if observer is not None:
        observer(value)
    if callback is not None:
        callback(value)
