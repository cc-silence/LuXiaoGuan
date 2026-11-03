"""Distinguish a confirmed remote exit from a closed SSH channel."""


class CaptureExitUnconfirmed(RuntimeError):
    ssh_kind = "capture"
    ssh_stage = "exit_status"


def require_capture_exit_status(status):
    if type(status) is not int or status < 0:
        raise CaptureExitUnconfirmed(
            "\u6293\u5305\u901a\u9053\u5df2\u7ed3\u675f\uff0c\u4f46\u672a\u6536\u5230\u8fdb\u7a0b\u9000\u51fa\u72b6\u6001\uff08exit_status=-1\uff09\uff1b"
            "\u5df2\u505c\u6b62\u81ea\u52a8\u6362\u6863\uff0c\u8f6c\u5165\u8d44\u6e90\u6536\u5c3e\u6838\u9a8c\u3002")
    return status


def drain_capture_tail(channel):
    """Retain final diagnostics with bounded reads and bounded memory."""
    tail = b""
    remaining = 262144
    while remaining > 0 and channel.recv_ready():
        chunk = channel.recv(min(32768, remaining))
        if not chunk:
            break
        remaining -= len(chunk)
        tail = (tail + chunk)[-16384:]
    return tail.decode("utf-8", errors="replace")
