"""Bounded remote cleanup independent of an installation cancellation request."""
from threading import Event


def cleanup_remote(execute, name, command, tell=None):
    """Preserve the installation outcome and report unconfirmed cleanup separately."""
    try:
        execute(name, command, 30, Event())
    except Exception:
        if tell is not None:
            tell("\u8def\u7531\u5668\u4e34\u65f6\u5b89\u88c5\u6587\u4ef6\u6e05\u7406\u672a\u786e\u8ba4\uff0c\u8bf7\u5728\u8fde\u63a5\u6062\u590d\u540e\u6838\u5bf9\u3002", "warning")
        return False
    return True
