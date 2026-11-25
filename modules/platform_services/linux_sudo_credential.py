"""Linux sudo password storage: session cache, keyring, and user-approved restricted file fallback.

The plaintext fallback is only used when the desktop keyring is unavailable and the user
explicitly chose to remember the password. It stores the password in a user-private file with
0600 permissions, never in normal config/log output.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from threading import Lock

from .credentials import CredentialError, delete_generic_password, read_generic_password, write_generic_password
from .runtime import is_linux

SUDO_TARGET = "RouterManager Linux sudo"
SUDO_USERNAME = "sudo"
_FALLBACK_NAME = "sudo-credential"

_SESSION_LOCK = Lock()
_SESSION_PASSWORD = None
_SESSION_REMEMBERED = False
_FALLBACK_NOTICE = (
    "系统密钥环不可用。你选择了记住密码时，路小管会把 sudo 密码保存在当前用户私有文件中，"
    "只允许当前用户读取。请确认这台电脑只有你信任的人能使用当前账号。"
)


def fallback_notice():
    return _FALLBACK_NOTICE


def _fallback_path() -> Path:
    from .application_paths import ensure_directory, user_config_dir
    config_dir = user_config_dir()
    ensure_directory(config_dir)
    return config_dir / _FALLBACK_NAME


def _read_fallback() -> str:
    path = _fallback_path()
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return ""
    except OSError:
        return ""
    if len(data) > 4096:
        return ""
    try:
        text = data.decode("utf-8")
    except UnicodeError:
        return ""
    if "\0" in text:
        return ""
    return text.rstrip("\n")


def _write_fallback(password: str) -> bool:
    path = _fallback_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".sudo-cred-", text=True)
        try:
            try:
                os.fchmod(fd, 0o600)
            except Exception:
                pass
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(password)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, path)
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            return False
        return True
    except Exception:
        return False


def _clear_fallback() -> None:
    try:
        _fallback_path().unlink(missing_ok=True)
    except OSError:
        pass


def clear_session() -> None:
    global _SESSION_PASSWORD, _SESSION_REMEMBERED
    with _SESSION_LOCK:
        _SESSION_PASSWORD = None
        _SESSION_REMEMBERED = False


def clear_saved() -> None:
    clear_session()
    if not is_linux():
        return
    try:
        delete_generic_password(SUDO_TARGET)
    except CredentialError:
        pass
    except Exception:
        pass
    _clear_fallback()


def session_password() -> str | None:
    with _SESSION_LOCK:
        return _SESSION_PASSWORD


def session_remembered() -> bool:
    with _SESSION_LOCK:
        return bool(_SESSION_REMEMBERED)


def set_session(password: str, remembered: bool) -> None:
    global _SESSION_PASSWORD, _SESSION_REMEMBERED
    with _SESSION_LOCK:
        _SESSION_PASSWORD = password
        _SESSION_REMEMBERED = bool(remembered)


def has_saved_password() -> bool:
    if not is_linux():
        return False
    with _SESSION_LOCK:
        if _SESSION_PASSWORD:
            return True
    try:
        value = read_generic_password(SUDO_TARGET)
        if value:
            return True
    except CredentialError:
        pass
    except Exception:
        pass
    return bool(_read_fallback())


def load_saved_password() -> str:
    if not is_linux():
        return ""
    with _SESSION_LOCK:
        if _SESSION_PASSWORD:
            return _SESSION_PASSWORD
    try:
        value = read_generic_password(SUDO_TARGET)
        if isinstance(value, str) and value:
            set_session(value, True)
            return value
    except CredentialError:
        pass
    except Exception:
        pass
    value = _read_fallback()
    if value:
        set_session(value, True)
    return value


def save_password(password: str, remember: bool, allow_fallback_file: bool = False) -> tuple[bool, str | None]:
    """Save password to session and optionally persistent storage.

    Returns (remembered_persistently, warning_code). warning_code is None, "keyring_unavailable",
    or "fallback_file".
    """
    set_session(password, remember)
    if not remember:
        # User asked not to remember persistently: clear persistent stores.
        try:
            delete_generic_password(SUDO_TARGET)
        except Exception:
            pass
        _clear_fallback()
        return False, None
    if not is_linux():
        return False, None
    try:
        write_generic_password(SUDO_TARGET, SUDO_USERNAME, password)
        _clear_fallback()
        return True, None
    except CredentialError as exc:
        if not allow_fallback_file:
            return False, exc.code
        if _write_fallback(password):
            return True, "fallback_file"
        return False, exc.code
    except Exception:
        if allow_fallback_file and _write_fallback(password):
            return True, "fallback_file"
        return False, "unavailable"
