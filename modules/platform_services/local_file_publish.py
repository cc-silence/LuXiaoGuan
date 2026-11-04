"""Publish an owned staging file without replacing an existing destination."""
import os
from pathlib import Path
from .runtime import is_linux
from .local_rename import rename_without_replace


def publish_new_file(source, destination):
    """Source and destination must be on the same filesystem."""
    if os.name == "nt":
        # Windows rename refuses existing destinations and works on FAT/exFAT.
        os.rename(source, destination)
    elif is_linux():
        rename_without_replace(source, destination)
    else:
        # POSIX rename replaces existing files; link provides no-replace semantics.
        os.link(source, destination)
        Path(source).unlink()
