"""Task-local configuration phases; the clock never controls task completion."""
from contextlib import contextmanager
from contextvars import ContextVar
import time

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
    if callback is None:
        return
    if deadline is None and seconds is not None:
        deadline = time.monotonic() + seconds
    callback(dict(configuration_apply=True, stage=stage, deadline=deadline,
                  rollback=bool(rollback), power_warning=True, uncancellable=True, **extra))
