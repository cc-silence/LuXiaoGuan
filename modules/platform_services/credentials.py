"""Shared credential API; Linux blocking calls are restricted to background callers."""
import json
from pathlib import Path
import subprocess
import sys
from .runtime import is_linux, is_windows

MAX_BYTES = 65536
ERRORS = {
    "missing_dependency": "Linux secure storage dependencies are not installed.",
    "unavailable": "The desktop Secret Service or its default persistent keyring is unavailable.",
    "locked": "Unlock the desktop keyring before saving or reading this password.",
    "ambiguous": "Multiple matching credentials require inspection in the system keyring.",
    "unencrypted": "The keyring did not establish an encrypted Secret Service session.",
    "invalid": "Invalid credential request.",
    "verification": "The credential operation could not be verified; retry before relying on it.",
    "timeout": "The credential operation timed out; its persistent state is unconfirmed.",
    "background_required": "Linux credential access must run in the background.",
    "unsupported": "Secure credential storage is not implemented for this operating system.",
}


class CredentialError(OSError):
    def __init__(self, code):
        self.code = code if code in ERRORS else "unavailable"
        super().__init__(ERRORS[self.code])


def validate_request(payload):
    if not isinstance(payload, dict) or set(payload) != {"operation", "target", "username", "password"}:
        raise CredentialError("invalid")
    if payload["operation"] not in {"read", "write", "delete"}:
        raise CredentialError("invalid")
    for key, limit in (("target", 2048), ("username", 1024), ("password", 4096)):
        value = payload[key]
        if not isinstance(value, str) or len(value) > limit or "\0" in value:
            raise CredentialError("invalid")
    if not payload["target"] or payload["operation"] != "write" and payload["password"]:
        raise CredentialError("invalid")
    return payload


def worker_command():
    arguments = [sys.executable]
    if not getattr(sys, "frozen", False):
        arguments.append(str(Path(__file__).resolve().parents[2] / "main.py"))
    return arguments + ["--credential-worker"]


def _linux_request(operation, target, username="", password=""):
    qt = sys.modules.get("PySide6.QtCore")
    if qt is not None:
        app = qt.QCoreApplication.instance()
        if app is not None and qt.QThread.currentThread() == app.thread():
            raise CredentialError("background_required")
    payload = validate_request({"operation": operation, "target": target,
                                "username": username, "password": password})
    data = json.dumps(payload, ensure_ascii=True).encode("utf-8")
    if len(data) > MAX_BYTES:
        raise CredentialError("invalid")
    try:
        result = subprocess.run(worker_command(), input=data, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, timeout=10)
    except subprocess.TimeoutExpired:
        raise CredentialError("timeout") from None
    except OSError:
        raise CredentialError("unavailable") from None
    try:
        if len(result.stdout) > MAX_BYTES:
            raise ValueError()
        reply = json.loads(result.stdout)
        if not isinstance(reply, dict) or type(reply.get("ok")) is not bool:
            raise ValueError()
        if not reply["ok"]:
            raise CredentialError(reply.get("code"))
        if result.returncode:
            raise ValueError()
        value = reply["value"]
        if operation == "read":
            if not isinstance(value, str) or len(value) > 4096:
                raise ValueError()
        elif value is not True:
            raise ValueError()
        return value
    except CredentialError:
        raise
    except (ValueError, KeyError, TypeError, UnicodeError):
        raise CredentialError("unavailable") from None


def read_generic_password(target):
    if is_windows():
        from .windows_credentials import read_generic_password as read
        return read(target)
    if is_linux():
        return _linux_request("read", target)
    raise CredentialError("unsupported")


def write_generic_password(target, username, password):
    if is_windows():
        from .windows_credentials import write_generic_password as write
        return write(target, username, password)
    if is_linux():
        return _linux_request("write", target, username, password)
    raise CredentialError("unsupported")


def delete_generic_password(target):
    if is_windows():
        from .windows_credentials import delete_generic_password as delete
        return delete(target)
    if is_linux():
        return _linux_request("delete", target)
    raise CredentialError("unsupported")
