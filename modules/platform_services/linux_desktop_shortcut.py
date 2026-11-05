"""Bounded freedesktop launcher operations, executed by the shortcut worker."""
import base64
from configparser import ConfigParser, Error as ConfigError
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from .desktop_shortcut import ShortcutSpec
from .runtime import is_linux

OWNER = "RouterManager.Desktop"
MAX_BYTES = 65536


def _value(text):
    text = str(text)
    if any(char in text for char in ("\0", "\n", "\r")):
        raise ValueError("Launcher values cannot contain NUL or line breaks.")
    return text.replace("\\", "\\\\")


def _argument(text):
    text = str(text)
    _value(text)
    for char in ("\\", '"', "$", chr(96)):
        text = text.replace(char, "\\" + char)
    return '"' + text.replace("%", "%%") + '"'


def shortcut_spec(desktop=None, base=None, executable=None, frozen=None):
    from modules.config_manager import BASE_DIR
    base = Path(base or BASE_DIR).absolute()
    target = Path(executable or sys.executable).absolute()
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    if "=" in str(target) or target.suffix.lower() == ".exe":
        raise ValueError("The Linux launcher requires a native executable path.")
    if desktop is None:
        from PySide6.QtCore import QStandardPaths
        location = QStandardPaths.writableLocation(QStandardPaths.DesktopLocation)
        if not location:
            raise OSError("The desktop directory is not configured.")
        desktop = Path(location)
    desktop = Path(desktop)
    if not desktop.is_absolute():
        raise ValueError("The desktop directory must be absolute.")
    if frozen and target==base/"LuXiaoGuan":
        from .linux_recovery_launcher import LAUNCHER, verify_launcher
        launcher=base/LAUNCHER
        if launcher.exists():target=verify_launcher(launcher)
    args = "" if frozen else _argument(base / "main.py")
    return ShortcutSpec(desktop / "LuXiaoGuan.desktop", target, args,
                        target.parent if frozen else base, str(base / "images/icons/app-router.png"))


def _fields(spec):
    from modules.release_identity import DISPLAY_NAME
    return {"Type": "Application", "Name": _value(DISPLAY_NAME),
            "Exec": _value(_argument(spec.target) + (" " + spec.arguments if spec.arguments else "")),
            "Path": _value(spec.working_directory), "Icon": _value(spec.icon),
            "Terminal": "false", "X-RouterManager-Owner": OWNER}


def inspect_shortcut(path):
    path = Path(path)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {"exists": False, "fingerprint": ""}
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
        raise OSError("Launcher is linked, not a regular file, or too large.")
    with path.open("rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise OSError("Launcher exceeds the inspection budget.")
    result = {"exists": True, "fingerprint": hashlib.sha256(data).hexdigest(),
              "fields": {}, "executable": bool(info.st_mode & stat.S_IXUSR)}
    parser = ConfigParser(interpolation=None, strict=True)
    parser.optionxform = str
    try:
        parser.read_string(data.decode("utf-8"))
        result["fields"] = dict(parser["Desktop Entry"])
    except (ConfigError, UnicodeError, KeyError):
        pass
    return result


def shortcut_state(spec, observed):
    if not observed.get("exists"):
        return "missing"
    fields = observed.get("fields", {})
    if fields == _fields(spec) and observed.get("executable"):
        return "valid"
    return "outdated" if fields.get("X-RouterManager-Owner") == OWNER else "foreign"


def operate(spec, operation, observed=None, replace_foreign=False, temporary=None):
    if spec.path.suffix != ".desktop" or not spec.path.is_absolute():
        raise ValueError("Unexpected Linux launcher path.")
    if not spec.path.parent.is_dir():
        raise OSError("Desktop directory is unavailable; no replacement directory was created.")
    current = inspect_shortcut(spec.path)
    if operation == "inspect":
        return current
    if operation != "create" or not isinstance(observed, dict):
        raise ValueError("Inspect the launcher before creating it.")
    if current["fingerprint"] != observed.get("fingerprint"):
        raise OSError("Launcher changed after inspection; retry.")
    if shortcut_state(spec, current) == "foreign" and not replace_foreign:
        raise PermissionError("Replacing another launcher requires confirmation.")
    required = [spec.target, Path(spec.icon)]
    if spec.arguments:
        required.append(spec.working_directory / "main.py")
    if any(not path.is_file() for path in required):
        raise FileNotFoundError("Application executable, entry script or icon is missing.")
    temporary = Path(temporary)
    if (temporary.parent != spec.path.parent or not temporary.name.startswith(".luxiaoguan-shortcut-")
            or temporary.suffix != ".desktop"):
        raise ValueError("Unexpected launcher staging path.")
    text = "[Desktop Entry]\n" + "".join(key + "=" + value + "\n" for key, value in _fields(spec).items())
    created = False
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            created = True
            os.chmod(temporary, 0o700)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        if shortcut_state(spec, inspect_shortcut(temporary)) != "valid":
            raise OSError("Staged launcher verification failed.")
        if inspect_shortcut(spec.path)["fingerprint"] != observed.get("fingerprint"):
            raise OSError("Launcher changed while saving; retry.")
        if not current["exists"]:
            os.link(temporary, spec.path)
        else:
            os.replace(temporary, spec.path)
        result = inspect_shortcut(spec.path)
        if shortcut_state(spec, result) != "valid":
            raise OSError("Saved launcher verification failed.")
        return result
    finally:
        if created:
            temporary.unlink(missing_ok=True)


def worker_arguments(spec, operation, observed, replace_foreign, temporary):
    payload = {"path": str(spec.path), "target": str(spec.target), "arguments": spec.arguments,
               "working_directory": str(spec.working_directory), "icon": spec.icon,
               "operation": operation, "observed": observed, "replace_foreign": replace_foreign,
               "temporary": str(temporary)}
    encoded = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
    if len(encoded) > MAX_BYTES:
        raise ValueError("Launcher request is too large.")
    arguments = ["--desktop-shortcut-worker", encoded]
    if not getattr(sys, "frozen", False):
        arguments.insert(0, str(Path(__file__).resolve().parents[2] / "main.py"))
    return arguments


def run_worker(argv):
    try:
        if not is_linux():
            raise OSError("This launcher worker requires Linux.")
        if len(argv) != 3 or len(argv[2]) > MAX_BYTES:
            raise ValueError("Invalid launcher request.")
        payload = json.loads(base64.b64decode(argv[2], validate=True).decode("utf-8"))
        spec = ShortcutSpec(Path(payload["path"]), Path(payload["target"]), payload["arguments"],
                            Path(payload["working_directory"]), payload["icon"])
        result = operate(spec, payload["operation"], payload["observed"],
                         payload["replace_foreign"], payload["temporary"])
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
