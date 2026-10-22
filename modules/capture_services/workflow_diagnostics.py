"""Record bounded workflow decisions without commands, passwords or device names."""

def record_workflow(owner, operation, status, **fields):
    backend = getattr(owner, "openwrt_backend", None)
    runner = getattr(backend, "command_runner", None)
    journal = getattr(runner, "operation_diagnostics", None)
    if journal is not None:
        journal.record("workflow", operation=operation, status=status, **fields)
