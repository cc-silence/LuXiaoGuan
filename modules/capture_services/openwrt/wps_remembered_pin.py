"""Remember a confirmed WPS PIN in the OS credential vault, keyed by BSSID."""
from modules.platform_services.credentials import (
    delete_generic_password,
    read_generic_password,
    write_generic_password,
)
from .command_builder import normalize_bssid

PREFIX = "RouterManager WPS PIN|"
USERNAME = "wps"


def valid_pin(value):
    text = str(value or "").strip()
    return text if text.isascii() and text.isdigit() and len(text) == 8 else ""


def credential_target(bssid):
    """Return the credential target string; invalid BSSID raises ValueError."""
    return PREFIX + normalize_bssid(bssid).upper()


def read_pin(bssid):
    """Read a remembered PIN; Linux callers must run off the GUI thread."""
    return valid_pin(read_generic_password(credential_target(bssid)))


def write_pin(bssid, pin):
    value = valid_pin(pin)
    if not value:
        raise ValueError("WPS PIN 必须是 8 位数字。")
    return write_generic_password(credential_target(bssid), USERNAME, value)


def delete_pin(bssid):
    return delete_generic_password(credential_target(bssid))
