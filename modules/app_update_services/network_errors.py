"""Bounded update diagnostics without proxy credentials or testing codes."""
from urllib.parse import urlsplit


def route_failure(url, proxy_url, reason, status=None):
    host = urlsplit(url).hostname or "update service"
    route = "\u4ee3\u7406" if proxy_url else "\u76f4\u8fde"
    reasons = {
        "timeout": "\u8fde\u63a5\u6216\u8bfb\u53d6\u8d85\u65f6",
        "tls": "HTTPS \u5b89\u5168\u8fde\u63a5\u5931\u8d25\uff08\u8bc1\u4e66\u6821\u9a8c\u6216\u8fde\u63a5\u88ab\u4e2d\u65ad\uff09",
        "proxy": "\u4ee3\u7406\u8fde\u63a5\u5931\u8d25",
        "dns": "\u57df\u540d\u89e3\u6790\u5931\u8d25",
        "connection": "\u7f51\u7edc\u8fde\u63a5\u5931\u8d25\u6216\u88ab\u4e2d\u65ad",
    }
    detail = f"HTTP {int(status)}" if status is not None else reasons.get(reason, reasons["connection"])
    return f"{route} {host}\uff1a{detail}\u3002"


def request_failure_reason(exc):
    import requests
    if isinstance(exc, requests.exceptions.SSLError):
        return "tls"
    if isinstance(exc, requests.exceptions.ProxyError):
        return "proxy"
    from urllib3.exceptions import ReadTimeoutError
    # Requests wraps a streaming read timeout in ConnectionError, unlike the
    # ReadTimeout raised while waiting for the initial response headers.
    if (isinstance(exc, requests.exceptions.ConnectionError) and exc.args
            and isinstance(exc.args[0], ReadTimeoutError)):
        return "timeout"
    if isinstance(exc, requests.exceptions.Timeout):
        return "timeout"
    return "connection"


def qt_failure_reason(error):
    from PySide6.QtNetwork import QNetworkReply as Reply
    if error == Reply.SslHandshakeFailedError:
        return "tls"
    if error in (Reply.TimeoutError, Reply.OperationCanceledError, Reply.ProxyTimeoutError):
        return "timeout"
    if error == Reply.HostNotFoundError:
        return "dns"
    if error in (Reply.ProxyConnectionRefusedError, Reply.ProxyConnectionClosedError,
                 Reply.ProxyNotFoundError, Reply.ProxyAuthenticationRequiredError):
        return "proxy"
    return "connection"
