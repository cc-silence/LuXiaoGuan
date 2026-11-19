"""Commands scoped to the application's retained console, never the tmux server."""

import shlex


def startup_command(command):
    if not isinstance(command, str) or not command or any(c in command for c in ("\x00", "\r", "\n")):
        raise ValueError("Invalid terminal startup command")
    body = "printf '%s\\n' " + shlex.quote(command) + "; " + command + " ; exec /bin/sh -i"
    return "sh -c " + shlex.quote(body)


def session_command(*, fresh=False, initial_command=None):
    prefix = ''
    if fresh:
        prefix = (
            "if tmux -L winbox has-session -t '=winbox-terminal' 2>/dev/null; then "
            "tmux -L winbox kill-session -t '=winbox-terminal' || exit $?; fi; "
        )
    if initial_command is not None and not fresh:
        raise ValueError('Startup commands require a fresh session')
    initial = ' ' + shlex.quote(startup_command(initial_command)) if initial_command is not None else ''
    attach = '' if fresh else '-A '
    return (prefix + 'exec tmux -u -L winbox -f /dev/null new-session '
            + attach + '-s winbox-terminal' + initial + ' \\; set-option -t winbox-terminal mouse on')
