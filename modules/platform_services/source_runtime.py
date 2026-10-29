"""Use this checkout's Python environment before loading GUI or worker dependencies."""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]


def source_interpreter(root, *, executable, prefix, base_prefix, platform, frozen=False):
    """Respect packaged applications and explicitly selected virtual environments."""
    if frozen or Path(prefix).resolve() != Path(base_prefix).resolve():
        return None
    environment = Path(root) / '.venv'
    if not (environment / 'pyvenv.cfg').is_file():
        return None
    if platform == 'win32':
        name = 'pythonw.exe' if Path(executable).stem.casefold() == 'pythonw' else 'python.exe'
        candidate = environment / 'Scripts' / name
    else:
        candidate = environment / 'bin/python'
    if not candidate.is_file():
        raise RuntimeError('Incomplete project Python environment: ' + str(candidate))
    return candidate


def ensure_source_runtime():
    candidate = source_interpreter(ROOT, executable=sys.executable, prefix=sys.prefix,
                                   base_prefix=sys.base_prefix, platform=sys.platform,
                                   frozen=getattr(sys, 'frozen', False))
    if candidate is not None:
        # No Qt import, shell, polling, new configuration or router connection in the old process.
        os.execv(str(candidate), [str(candidate), str(Path(sys.argv[0]).resolve()), *sys.argv[1:]])
