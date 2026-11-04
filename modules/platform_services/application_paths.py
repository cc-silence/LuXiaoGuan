"""User-owned writable locations; application resources remain in the install tree."""
import os
from pathlib import Path, PurePosixPath
import tempfile
from .runtime import is_linux

APP_DIRECTORY = "RouterManager"
_XDG = {
    "config": ("XDG_CONFIG_HOME", ".config"),
    "state": ("XDG_STATE_HOME", ".local/state"),
    "data": ("XDG_DATA_HOME", ".local/share"),
}


def linux_user_directory(kind, environ=None, home=None):
    """Resolve XDG paths without filesystem access; ignore relative XDG values."""
    environment = os.environ if environ is None else environ
    variable, fallback = _XDG[kind]
    user_home = PurePosixPath(Path.home().as_posix() if home is None else home)
    if not user_home.is_absolute():
        raise ValueError("The Linux user home must be absolute.")
    value = environment.get(variable, "")
    base = PurePosixPath(value) if value and PurePosixPath(value).is_absolute() else user_home / fallback
    return base / APP_DIRECTORY


def user_config_dir():
    override = os.environ.get("ROUTERMANAGER_USER_CONFIG_DIR")
    if override:
        path = Path(override)
        if is_linux() and not path.is_absolute():
            raise ValueError("ROUTERMANAGER_USER_CONFIG_DIR must be absolute on Linux.")
        return path
    if is_linux():
        return Path(linux_user_directory("config"))
    return Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()) / APP_DIRECTORY / "config"


def configuration_dir(application_root):
    """Windows keeps its portable mirror; Linux has one writable user location."""
    return user_config_dir() if is_linux() else Path(application_root) / "config"


def user_state_dir():
    if is_linux():
        return Path(linux_user_directory("state"))
    return Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()) / APP_DIRECTORY


def work_data_dir(application_root):
    if is_linux():
        return Path(linux_user_directory("data"))
    return Path(application_root) / "RouterManagerData"


def automatic_capture_directory(legacy_root):
    """Keep Linux results outside installed resources and preserve Windows paths."""
    if is_linux():
        return work_data_dir(legacy_root) / 'captures'
    return Path(legacy_root) / 'dist' / 'captures'


def error_log_dir(application_root):
    if is_linux():
        return user_state_dir() / "error-logs"
    return work_data_dir(application_root) / "error-logs"


def ensure_directory(path):
    """Create new Linux directories privately without chmod on existing folders."""
    path = Path(path)
    if not is_linux():
        path.mkdir(parents=True, exist_ok=True)
        return
    missing = []
    current = path
    while not current.exists():
        missing.append(current)
        parent = current.parent
        if parent == current:
            raise OSError("No existing ancestor for directory: " + str(path))
        current = parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700, exist_ok=True)
    if not path.is_dir():
        raise NotADirectoryError(str(path))


def expand_portable_tokens(text, replacements):
    # Backslashes are separators only inside recognized portable placeholders.
    if is_linux():
        for token, root in replacements:
            if text == token:
                return str(root)
            if text.startswith(token) and text[len(token):len(token)+1] in ('/', '\\'):
                return str(root) + text[len(token):].replace('\\', '/')
    for token, root in replacements:
        text = text.replace(token, str(root))
    return text


def join_portable_token(token, relative_path):
    if str(relative_path) == '.':
        return token
    if is_linux():
        return token + '/' + relative_path.as_posix()
    return token + '\\' + str(relative_path)
