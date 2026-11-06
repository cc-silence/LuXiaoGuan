"""Restore saved success labels during background scans without reading passwords."""
from contextlib import closing
from dataclasses import replace
import sqlite3


def load_found_states(cancel_event=None):
    from .openwrt.password_history import history_path
    path = history_path()
    if (cancel_event is not None and cancel_event.is_set()) or not path.is_file():
        return {}
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=1)) as connection:
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='password_targets'").fetchone():
            return {}
        rows = connection.execute("SELECT ssid,bssid FROM password_targets WHERE state='found' AND result_file<>'' ORDER BY updated DESC LIMIT 10000")
        states = {}
        for ssid, bssid in rows:
            if cancel_event is not None and cancel_event.is_set():
                return {}
            states[(bssid.upper(), ssid)] = ('found', '\u5386\u53f2\u8bb0\u5f55\u5df2\u627e\u5230\u5bc6\u7801\uff0c\u53ef\u5728\u731c\u5bc6\u7801\u7ed3\u679c\u4e2d\u67e5\u770b\u3002')
        return states


def scan_with_password_history(scan, *args, cancel_event=None, progress_callback=None, message_callback=None):
    """One metadata read per scan; decorate both incremental and final snapshots."""
    try:
        states = load_found_states(cancel_event)
    except (OSError, sqlite3.Error):
        states = {}
        if message_callback is not None:
            message_callback('\u8bfb\u53d6\u5386\u53f2\u5bc6\u7801\u72b6\u6001\u5931\u8d25\uff0c\u672c\u6b21\u626b\u63cf\u7ee7\u7eed\u3002', 'warning')
    def decorate(update):
        from .password_target_status import restore_scan
        return replace(update, access_points=tuple(restore_scan(point, states) for point in update.access_points))
    def progress(update):
        if progress_callback is not None:
            progress_callback(decorate(update))
    return decorate(scan(*args, cancel_event=cancel_event,
                         progress_callback=progress if progress_callback is not None else None))
