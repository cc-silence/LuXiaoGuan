"""Virtual elapsed time for adapter-readiness tests; no real network operations."""
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch
from modules.platform_services import lan_network as facade

@contextmanager
def fast_address_wait():
    clock=[100.0]
    def wait(seconds):
        clock[0]+=seconds
    with patch.object(facade.time,'monotonic',side_effect=lambda:clock[0]), patch.object(facade,'Event',return_value=SimpleNamespace(wait=wait)):
        yield clock
