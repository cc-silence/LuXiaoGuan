"""Local test shells only: explicit Git Bash on Windows, no WSL discovery."""
import os
from pathlib import Path
import sys


def local_shell(*, bash=False):
    if sys.platform.startswith('linux'):
        value=Path('/bin/bash' if bash else '/bin/sh')
    elif sys.platform=='win32':
        value=Path(os.environ.get('ProgramFiles',r'C:\Program Files'))/'Git/bin/bash.exe'
    else:
        return None
    return value if value.is_file() else None
