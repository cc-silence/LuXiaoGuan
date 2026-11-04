"""Portable fixtures for deliberately simulated Windows-only backend calls."""
from contextlib import contextmanager
import os
import sys
from types import ModuleType
from unittest.mock import Mock, patch

@contextmanager
def mock_windows_elevation(**options):
    name = 'modules.platform_services.update_elevation'
    if os.name == 'nt':
        with patch(name + '.run_elevated_update', **options) as call:
            yield call
    else:
        backend = ModuleType(name)
        backend.run_elevated_update = Mock(**options)
        with patch.dict(sys.modules, {name: backend}):
            yield backend.run_elevated_update
