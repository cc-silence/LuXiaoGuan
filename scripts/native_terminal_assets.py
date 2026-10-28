"""Verify native terminal provenance and the actual exported Qt version before packaging."""
import hashlib
import json
from pathlib import Path


def source_inputs(source):
    source = Path(source)
    rows = {}
    for path in sorted(source.rglob('*')):
        if path.is_symlink():
            raise ValueError('Linked terminal source is not accepted')
        if path.is_file():
            rows[path.relative_to(source).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    if not {'bridge.cpp', 'terminal.pro', 'upstream.json'} <= rows.keys():
        raise ValueError('Incomplete native terminal source')
    return rows


def verify_native_terminal(package, source_root, expected_qt):
    folder = Path(package) / 'bin/router_terminal'
    manifest = json.loads((folder / 'build-manifest.json').read_text(encoding='utf-8'))
    if manifest.get('qt_version') != expected_qt:
        raise RuntimeError('Native terminal Qt mismatch: required ' + expected_qt +
                           ', compiled=' + str(manifest.get('qt_version')) +
                           '. Rebuild with scripts/build_native_terminal.py.')
    dll = folder / 'winbox_terminal.dll'
    digest = hashlib.sha256(dll.read_bytes()).hexdigest()
    if digest != manifest.get('dll_sha256'):
        raise RuntimeError('Native terminal DLL does not match its build manifest')
    source = Path(source_root) / 'native/router_terminal'
    if source_inputs(source) != manifest.get('source_sha256'):
        raise RuntimeError('Native terminal sources changed; rebuild the terminal')
    if json.loads((source / 'upstream.json').read_text(encoding='utf-8')) != manifest.get('upstream'):
        raise RuntimeError('Native terminal upstream provenance mismatch')
    for name in ('kodoterm', 'libvterm'):
        if (folder / (name + '-LICENSE.txt')).read_bytes() != (source / 'vendor' / name / 'LICENSE').read_bytes():
            raise RuntimeError('Native terminal license mismatch: ' + name)
    # Do not trust an edited manifest to disguise an old binary. No widget is created here.
    from modules.terminal_widgets.native_view import NativeTerminalView
    library = NativeTerminalView._load_library(dll)
    compiled = library.wb_qt_version().decode('ascii')
    if compiled != expected_qt:
        raise RuntimeError('Native terminal binary Qt mismatch: ' + compiled)
    return dict(qt_version=compiled, dll_sha256=digest, source_files=len(manifest['source_sha256']))
