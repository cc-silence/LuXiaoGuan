"""Background update-log export; kept outside the standalone installer dependency graph."""
from collections import deque
import json
import os
import platform
import ssl
import time
from .diagnostics import MARKER


def export_update_logs(destination, entries, app_version='', cancel=None, timeout=60):
    from modules.error_log_hooks import current_error_logs
    from modules.error_logs import regular_file
    from modules.log_services import export_logs
    deadline = time.monotonic() + timeout
    rows = deque(maxlen=128)
    skipped = 0
    omitted_history = 0
    notes = []
    try:
        store = current_error_logs()
        store.cleanup()
        files = store.files()
        if store.last_error:
            notes.append((json.dumps({"history_write_failure_reported": True}), "warning"))
    except OSError:
        files = []
        skipped += 1
    for path, info in files:
        if cancel is not None and cancel.is_set():
            raise InterruptedError('export cancelled')
        if time.monotonic() >= deadline:
            raise TimeoutError('export deadline exceeded')
        try:
            before = regular_file(path)
            if before is None or info.st_size > 2 * 1024 * 1024:
                skipped += 1
                continue
            # Rotation can remove a file during export; never follow a replaced link.
            with path.open('rb') as stream:
                opened = os.fstat(stream.fileno())
                if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                    skipped += 1
                    continue
                data = stream.read(2 * 1024 * 1024 + 1)
            if len(data) > 2 * 1024 * 1024:
                skipped += 1
                continue
        except OSError:
            skipped += 1
            continue
        # Include only update records, never unrelated errors or configuration files.
        for line in data.decode('utf-8', errors='replace').splitlines():
            if line.startswith(MARKER):
                if len(rows) == rows.maxlen:
                    omitted_history += 1
                rows.append((line, 'error'))
    if omitted_history:
        notes.append((json.dumps({"history_records_omitted": omitted_history}), "warning"))
    if skipped:
        notes.append((json.dumps({'history_files_skipped': skipped}), 'warning'))
    import requests
    from PySide6.QtCore import qVersion
    from PySide6.QtNetwork import QSslSocket
    environment = dict(qt_tls_backend=QSslSocket.activeBackend(), qt_tls_version=QSslSocket.sslLibraryVersionString(),
                       requests_ca_available=os.path.isfile(requests.certs.where()), requests=requests.__version__, qt=qVersion(), version=app_version, system=platform.system(), release=platform.release(),
                       architecture=platform.machine(), python=platform.python_version(), tls=ssl.OPENSSL_VERSION)
    return export_logs(destination, 'application-update',
                       [(json.dumps(environment), 'info')] + list(rows) + notes + list(entries),
                       app_version=app_version, cancel=cancel, timeout=max(0, deadline-time.monotonic()))
