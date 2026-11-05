"""Platform lifecycle hooks without eager Windows API imports."""
from .runtime import is_windows


def close_startup_sessions():
    if is_windows():
        from .firewall_session import close_firewall_session
        close_firewall_session()
