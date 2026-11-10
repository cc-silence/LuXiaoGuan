"""Read directory ownership on stock BusyBox without requiring the stat applet."""
from .command_builder import quote_argument as q


def directory_metadata_command(path):
    return ("if stat -L -c '%a %u %g %Y' / >/dev/null 2>&1; then mode=stat; else mode=ls; fi; "
            "while IFS= read -r p; do if test -d \"$p\"; then printf '%s\\t' \"$p\"; "
            "if test \"$mode\" = stat; then stat -L -c '%a %u %g %Y' \"$p\" || exit 49; "
            "else printf 'ls\\t'; LC_ALL=C ls -ldnL \"$p\" || exit 49; fi; fi; done < " + q(path))


def _symbolic_mode(value):
    if len(value) != 10 or value[0] != "d":
        raise ValueError("Invalid directory permission record")
    mode = 0
    for index, ordinary in enumerate("rwxrwxrwx", 1):
        char = value[index]
        if char == ordinary:
            mode |= 1 << (9-index)
        elif char != "-":
            special = {3:("s", "S", 0o4000), 6:("s", "S", 0o2000), 9:("t", "T", 0o1000)}.get(index)
            if special is None or char not in special[:2]:
                raise ValueError("Invalid directory permission bits")
            mode |= special[2]
            if char == special[0]:
                mode |= 1 << (9-index)
    return mode


def parse_directory_metadata(output, expected):
    directories = {}
    for line in output.splitlines():
        path, separator, attributes = line.partition("\t")
        name = path.removeprefix("/")
        if not separator or name not in expected or name in directories:
            raise ValueError("Invalid directory metadata")
        if attributes.startswith("ls\t"):
            values = attributes[3:].split()
            if len(values) < 4 or any(not value.isdigit() for value in values[1:4]):
                raise ValueError("Invalid numeric directory ownership")
            mode, uid, gid = _symbolic_mode(values[0]), int(values[2]), int(values[3])
            mtime = 0  # Stock ls lacks an epoch timestamp; permissions never use defaults.
        else:
            values = attributes.split()
            if len(values) != 4 or any(not value.isdigit() for value in values):
                raise ValueError("Invalid directory permissions")
            mode, uid, gid, mtime = int(values[0], 8), *(int(value) for value in values[1:])
        if mode > 0o7777:
            raise ValueError("Invalid directory mode")
        directories[name] = (mode, uid, gid, mtime)
    return directories


def file_metadata_command(path):
    value = q(path)
    return ("test -f " + value + " && test ! -L " + value + " || exit 53; "
            "if stat -c '%a %u %g' " + value + " >/dev/null 2>&1; then "
            "printf 'stat\\t'; stat -c '%a %u %g' " + value + "; "
            "else printf 'ls\\t'; LC_ALL=C ls -ldn " + value + "; fi")


def parse_file_metadata(output):
    kind, separator, attributes = output.partition("\t")
    values = attributes.split()
    if not separator or len(output.splitlines()) != 1:
        raise ValueError("Invalid regular-file metadata")
    if kind == "stat":
        if len(values) != 3 or any(not value.isdigit() for value in values):
            raise ValueError("Invalid regular-file ownership")
        try:
            mode = int(values[0], 8)
        except ValueError as exc:
            raise ValueError("Invalid regular-file permissions") from exc
        uid, gid = map(int, values[1:])
    elif kind == "ls":
        if (len(values) < 4 or not values[0].startswith("-")
                or any(not value.isdigit() for value in values[1:4])):
            raise ValueError("Invalid regular-file ownership")
        mode = _symbolic_mode("d" + values[0][1:])
        uid, gid = int(values[2]), int(values[3])
    else:
        raise ValueError("Unknown regular-file metadata format")
    if mode > 0o7777:
        raise ValueError("Invalid regular-file permissions")
    return mode, uid, gid
