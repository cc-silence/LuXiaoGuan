"""Read back an owned PID after an uncertain stop, without sending another signal."""


def confirm_stopped(manager, handle):
    runner = manager.command_runner
    ssh = getattr(runner, "ssh_client", None)
    if ssh is not None and getattr(ssh, "connected", True) is False:
        return False
    pid, token = int(handle.pid), str(handle.start_token)
    if pid <= 0 or not token.isascii() or not token.isdigit():
        raise ValueError("Invalid owned process identity")
    command = (
        f"if [ ! -d /proc/{pid} ]; then printf 'STOPPED\\n'; exit 0; fi; "
        f"stat=$(cat /proc/{pid}/stat 2>/dev/null) || exit 2; "
        "current=$(printf '%s' \"$stat\" | sed 's/^.*) //' | cut -d ' ' -f 20); "
        "case \"$current\" in ''|*[!0-9]*) exit 2;; esac; "
        f"if [ \"$current\" != {token} ]; then printf 'STOPPED\\n'; exit 0; fi; "
        "state=$(printf '%s' \"$stat\" | sed 's/^.*) //' | cut -d ' ' -f 1); "
        "printf 'RUNNING %s\\n' \"$state\"; exit 3"
    )
    try:
        result = runner.run("verify_stop_" + handle.program, command, timeout=4)
        confirmed = (result.exit_code == 0 and not result.timed_out
                     and not result.cancelled and result.stdout.strip() == "STOPPED")
        state = result.stdout.strip().removeprefix("RUNNING ")
        reason = ("process_absent" if confirmed else "process_state_" + state
                  if state in {"R", "S", "D", "Z", "T", "I"} else "process_stop_unconfirmed")
        journal = getattr(runner, "operation_diagnostics", None)
        if journal is not None:
            journal.record("workflow", operation="verify_process_stop",
                           status="completed" if confirmed else "uncertain", reason=reason,
                           exit_code=result.exit_code)
        return confirmed
    except Exception:
        return False
