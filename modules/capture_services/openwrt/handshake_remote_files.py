"""Release only a stopped regular capture's explicitly owned files."""
import re
from pathlib import Path, PurePosixPath
from .command_builder import quote_argument
from .command_runner import require_command_completion


def _prefix(value):
    path = PurePosixPath(str(value))
    if (not path.is_absolute() or ".." in path.parts or str(path.parent) == "/"
            or not re.fullmatch(r"handshake(?:-[A-Za-z0-9]{1,32})?", path.name)):
        raise ValueError("Invalid capture prefix")
    return path


def release_command(prefix, local_cap=None):
    prefix = _prefix(prefix)
    cap = str(prefix) + "-01.cap"
    files = [cap, str(prefix) + "-01.csv", str(prefix.parent / "airodump-ng.log"),
             str(prefix.parent / "handshake-hint.txt")]
    quoted = " ".join(quote_argument(name) for name in files)
    # Refuse links and verify the completed local copy before touching evidence.
    root = quote_argument(str(prefix.parent))
    guard = f"[ -d {root} ] && [ ! -L {root} ] || exit 72; for f in {quoted}; do [ ! -L \"$f\" ] || exit 72; done; "
    if local_cap is not None:
        size = Path(local_cap).stat().st_size
        if size <= 0:
            raise ValueError("Completed capture copy is empty")
        target = quote_argument(cap)
        guard += f"[ -f {target} ] && [ \"$(wc -c <{target})\" -eq {size} ] || exit 73; "
    else:
        guard += f"[ ! -s {quote_argument(cap)} ] || exit 73; "
    return guard + f"rm -f -- {quoted} || exit 74; for f in {quoted}; do [ ! -e \"$f\" ] || exit 75; done"


def release_downloaded(runner, prefix, local_cap=None, cancel_event=None):
    """Call only after the producer and its content checker have stopped."""
    result = runner.run("release_downloaded_handshake", release_command(prefix, local_cap),
                        timeout=5, cancel_event=cancel_event)
    require_command_completion(result)
    if result.exit_code != 0:
        raise RuntimeError("Stopped capture files could not be safely released; local copy retained")


def storage_inventory_command(directory):
    directory = PurePosixPath(str(directory))
    if not directory.is_absolute() or ".." in directory.parts or str(directory) == "/":
        raise ValueError("Invalid owned directory")
    root = quote_argument(str(directory))
    return (f"du -sk {root}; for f in {root}/*; do "
            '[ -f "$f" ] && [ ! -L "$f" ] || continue; wc -c "$f"; '
            'done | sort -nr | head -n 20')
