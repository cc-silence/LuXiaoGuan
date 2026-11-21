"""Owned foreground deauthentication, with verified timeout cleanup."""
from uuid import uuid4

from .command_builder import quote_argument
from .command_runner import require_command_completion
from .models import CaptureCleanupError
from .remote_process import RemoteProcessHandle


DEAUTH_TIMEOUT_NOTE = "\u4e3b\u52a8\u65ad\u8fde\u547d\u4ee4\u8d85\u65f6\uff0c\u5df2\u786e\u8ba4\u505c\u6b62\uff1b\u672c\u8f6e\u8df3\u8fc7\u8be5\u76ee\u6807\u3002"


class DeauthTargetTimeout(RuntimeError):
    """Only this target failed; its remote sender has been confirmed stopped."""


def deauth_timeout_seconds(batches):
    # Count means 128 frames per directed round, not seconds. Driver TX and
    # receive draining vary; allow three seconds per round plus beacon startup.
    return 10 + 3 * batches


def run_deauth(runner, processes, command, batches, cancel_event):
    identity = '/tmp/.lxg-deauth-' + uuid4().hex
    path = quote_argument(identity)
    script = (
        f'umask 077; : >{path} || exit 125; ' + command + ' </dev/null & pid=$!; '
        "token=$(sed 's/^.*) //' /proc/$pid/stat 2>/dev/null | cut -d ' ' -f 20); "
        f'printf "%s %s" "$pid" "$token" >{path}; '
        'wait "$pid"; exit $?'
    )
    result = None
    try:
        result = runner.run('deauth_client', script,
            timeout=deauth_timeout_seconds(batches), cancel_event=cancel_event,
            output_limit=1024 * 1024)
    finally:
        if result is None or result.timed_out or result.cancelled:
            # Closing an SSH channel alone does not prove its child exited.
            # Cleanup ignores cancellation, and uses the existing PID/token stop.
            try:
                state = runner.run('read_deauth_owner', f'cat {path}', timeout=8)
                require_command_completion(state)
                fields = state.stdout.strip().split()
                if state.exit_code != 0:
                    raise RuntimeError('Cannot read deauthentication process ownership')
                if len(fields) != 2 or not all(v.isdigit() for v in fields) or int(fields[0]) <= 1:
                    raise RuntimeError('Cannot verify deauthentication process ownership')
                handle = RemoteProcessHandle(int(fields[0]), fields[1],
                    'aireplay-ng', identity.rsplit('/', 1)[-1], '/tmp')
                processes.register(handle)
                if processes.stop(handle) is not True:
                    raise RuntimeError('Deauthentication stop was not confirmed')
            except Exception as exc:
                raise CaptureCleanupError('Deauthentication cleanup unconfirmed; batch stopped',
                    (str(exc),)) from exc
        removed = runner.run('release_deauth_owner', f'rm -f -- {path}', timeout=5)
        require_command_completion(removed)
        if removed.exit_code != 0:
            raise CaptureCleanupError('Cannot release deauthentication ownership record',
                ('Deauthentication ownership record retained: ' + identity,))
    if result.cancelled:
        raise InterruptedError('Deauthentication cancelled')
    if result.timed_out:
        raise DeauthTargetTimeout(DEAUTH_TIMEOUT_NOTE)
    return result
