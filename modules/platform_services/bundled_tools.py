"""Resolve shipped tools inside the legacy-compatible managed resource tree."""
from pathlib import Path
import sys


def bundled_tools(root, *, frozen=None):
    if frozen is None:
        frozen = getattr(sys, "frozen", False)
    return Path(root) / "bin" / "tools" if frozen else Path(root) / "tools"
