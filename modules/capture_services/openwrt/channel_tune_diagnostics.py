"""Bounded channel-failure evidence, reusing existing read-only process APIs."""
from datetime import datetime, timezone

LIMIT = 8192


def tool_snapshot(manager, handle, cancel_event=None):
    parts = ["timestamp=" + datetime.now(timezone.utc).isoformat()]
    try:
        parts.append("process_running=" + str(bool(manager.is_running(handle, cancel_event))))
    except Exception as exc:
        parts.append("process_state_unavailable=" + type(exc).__name__)
    try:
        output = manager.read_log_tail(handle, 100, cancel_event)
        parts.append("combined_stdout_stderr_tail=" + (str(output)[-LIMIT:] if output else "<empty or unavailable>"))
    except Exception as exc:
        parts.append("tool_log_unavailable=" + type(exc).__name__)
    return "\n".join(parts)


def append_stopped_output(workspace, name, manager, handle):
    """Read once after normal stop to retain output flushed during shutdown."""
    try:
        path = workspace.local_dir / name
        if not name or path.name != name or not path.is_file():
            return
        output = manager.read_log_tail(handle, 100, None)
        with path.open("a", encoding="utf-8") as stream:
            stream.write("\n===== tool-output-after-stop =====\n" +
                         (str(output)[-LIMIT:] if output else "<empty or unavailable>") + "\n")
    except Exception:
        pass  # Diagnostic failure must not replace the original failure or cleanup result.
