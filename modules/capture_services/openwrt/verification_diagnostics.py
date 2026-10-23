"""Preparation phase evidence; never inspect arguments, credentials or raw errors."""
from functools import wraps
from time import monotonic


def verification_phase(stage):
    def decorate(method):
        @wraps(method)
        def wrapped(self, *args, **kwargs):
            from .operation_diagnostics import OperationDiagnostics
            journal = getattr(self.command_runner, "operation_diagnostics", None)
            if not isinstance(journal, OperationDiagnostics):
                return method(self, *args, **kwargs)
            start = monotonic()
            journal.record("workflow", operation="verification_prepare", stage=stage, status="started")
            try:
                result = method(self, *args, **kwargs)
            except Exception as exc:
                kind = next((name for cls, name in ((InterruptedError, "cancelled"),
                    (TimeoutError, "timeout"), (OSError, "io_error"), (ValueError, "invalid_data"),
                    (RuntimeError, "runtime_error")) if isinstance(exc, cls)), "unexpected_error")
                if type(exc).__name__ == "PasswordVerifyCancelled":
                    kind = "cancelled"
                status = kind if kind in {"cancelled", "timeout"} else "failed"
                journal.record("workflow", operation="verification_prepare", stage=stage,
                    status=status, reason=kind, elapsed_seconds=monotonic() - start)
                raise
            journal.record("workflow", operation="verification_prepare", stage=stage,
                status="failed" if stage == "restore" and result else "completed",
                reason=("restore_reported_errors" if result else "configuration_untouched"
                        if not getattr(self, "_configuration_touched", True) else "phase_completed")
                    if stage == "restore" else "phase_completed",
                elapsed_seconds=monotonic() - start)
            return result
        return wrapped
    return decorate
