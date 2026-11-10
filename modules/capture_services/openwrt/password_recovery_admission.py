"""Separate local recovery-record admission from an authenticated SSH session."""
import json

ERROR_TEXT = '\u4e0a\u6b21\u5bc6\u7801\u9a8c\u8bc1\u7684\u6062\u590d\u8bb0\u5f55\u65e0\u6cd5\u6838\u5b9e\uff0c\u672a\u81ea\u52a8\u4fee\u6539\u8def\u7531\u5668\uff1b\u8bb0\u5f55\u5df2\u4fdd\u7559\u3002'
WARNING = '\u5df2\u4fdd\u7559\u767b\u5f55\u8fde\u63a5\uff0c\u4f46\u672c\u5730\u5bc6\u7801\u9a8c\u8bc1\u6062\u590d\u8bb0\u5f55\u65e0\u6cd5\u8bfb\u53d6\u6216\u6821\u9a8c\uff1b\u672a\u6267\u884c\u81ea\u52a8\u6e05\u7406\uff0c\u539f\u8bb0\u5f55\u4fdd\u7559\u3002\u90e8\u5206\u65e0\u7ebf\u4efb\u52a1\u548c\u81ea\u52a8\u4fee\u6539\u8def\u7531\u5668\u7684\u64cd\u4f5c\u5df2\u6682\u7f13\uff0c\u53ef\u5148\u67e5\u770b\u8bbe\u5907\u4fe1\u606f\u6216\u4f7f\u7528\u7ec8\u7aef\u6392\u67e5\uff0c\u5904\u7406\u540e\u91cd\u65b0\u767b\u5f55\u6838\u5bf9\u3002'


class RecoveryRecordsUnavailable(RuntimeError):
    def __init__(self, error):
        super().__init__(ERROR_TEXT)
        from .password_recovery_store import RecoveryDirectoryError
        name = type(error).__name__
        allowed = {'OSError','PermissionError','FileNotFoundError','NotADirectoryError',
                   'IsADirectoryError','ValueError','TypeError','JSONDecodeError','UnicodeDecodeError'}
        self.local_error = error
        self.evidence = {'exception_type':name if name in allowed else 'Exception'}
        for field in ('errno', 'winerror'):
            value = getattr(error, field, None)
            if type(value) is int and abs(value) <= 10**10:
                self.evidence[field] = value
        self.evidence['reason'] = ('recovery_directory' if isinstance(error, RecoveryDirectoryError)
                                  else 'recovery_record_io' if isinstance(error, OSError)
                                  else 'recovery_record_json' if isinstance(error, json.JSONDecodeError)
                                  else 'recovery_record_validation')


def read_records(store, fingerprint):
    try:
        return store.records(fingerprint)
    except InterruptedError:
        raise
    except (OSError, ValueError, TypeError) as error:
        raise RecoveryRecordsUnavailable(error) from error


def prepare_login(backend, fingerprint, cancel=None, progress_callback=None):
    from .password_recovery import recover_pending
    if cancel is not None and cancel.is_set():
        raise InterruptedError('Login cancelled')
    try:
        recovered = recover_pending(backend.password_recovery_store, backend.command_runner,
                                    fingerprint, cancel, progress_callback=progress_callback)
    except RecoveryRecordsUnavailable as error:
        if cancel is not None and cancel.is_set():
            raise InterruptedError('Login cancelled') from error
        from .password_recovery_store import RecoveryRecordCorrupt
        local = error.local_error
        backend.reconnect_cleanup._corrupt_password_record = local if isinstance(local, RecoveryRecordCorrupt) else None
        backend.reconnect_cleanup.set_password_check_issue(fingerprint, error.evidence)
        backend.command_runner.operation_diagnostics.record(
            'recovery', operation='password_recovery_check', stage='preflight',
            status='blocked', **error.evidence)
        return 0, WARNING
    if cancel is not None and cancel.is_set():
        raise InterruptedError('Login cancelled')
    backend.reconnect_cleanup._corrupt_password_record = None
    backend.reconnect_cleanup.set_password_check_issue(fingerprint, None)
    return recovered, ''


def require_ready(backend, action=None, arguments=None):
    if action is not None:
        readonly = {'capture_monitor_preview','local_list','local_upload_selection',
                    'local_delete','local_mkdir','list','download','download_directory',
                    'common_read','policy_read','policy_status','policy_preview',
                    'maintenance_preview','maintenance_backup'}
        if action in readonly:
            return
        if action == 'file_batch' and all(job.get('operation') in
                {'download','download_directory','local_delete','local_move'}
                for job in (arguments or {}).get('jobs', ())):
            return
    cleanup = getattr(backend, 'reconnect_cleanup', None)
    check = getattr(cleanup, 'require_password_check_ready', None)
    if check is not None:
        check()
