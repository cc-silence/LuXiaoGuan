"""Use Qt's actual update-check transport without changing its production class."""
import base64
import json
from pathlib import Path
import time
from urllib.parse import unquote, urlsplit


def run_request(options):
    from PySide6.QtCore import QCoreApplication, QTimer, QUrl
    from PySide6.QtNetwork import QNetworkAccessManager, QNetworkProxy, QNetworkRequest, QNetworkReply
    app = QCoreApplication.instance() or QCoreApplication([])
    manager = QNetworkAccessManager()
    proxy = QNetworkProxy(QNetworkProxy.NoProxy)
    if options["proxy"]:
        parsed = urlsplit(options["proxy"])
        kind = QNetworkProxy.HttpProxy if parsed.scheme == "http" else QNetworkProxy.Socks5Proxy
        proxy = QNetworkProxy(kind, parsed.hostname, parsed.port,
                              unquote(parsed.username or ""), unquote(parsed.password or ""))
    manager.setProxy(proxy)
    request = QNetworkRequest(QUrl(options["url"]))
    request.setAttribute(QNetworkRequest.RedirectPolicyAttribute, QNetworkRequest.ManualRedirectPolicy)
    request.setTransferTimeout(round(options["profile"]["idle"] * 1000))
    request.setRawHeader(b"Accept-Encoding", b"identity")
    for key, value in options["headers"].items():
        request.setRawHeader(key.encode("ascii"), value.encode("ascii"))
    started = time.monotonic()
    result = dict(stage="connecting", received_bytes=0, outcome="failed", reason="unknown")
    body = bytearray()
    reply = manager.get(request)
    connection = QTimer()
    connection.setSingleShot(True)

    def progress():
        temporary = Path(options["directory"]) / "progress.tmp"
        temporary.write_text(json.dumps(result), encoding="utf-8")
        temporary.replace(temporary.with_name("progress.json"))

    def abort(reason):
        result["reason"] = reason
        reply.abort()

    def encrypted():
        connection.stop()
        result["stage"] = "waiting_response"
        progress()

    def metadata():
        encrypted()
        status = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
        if status is not None:
            result["status"] = int(status)
        result.setdefault("headers_seconds", round(time.monotonic() - started, 3))
        progress()

    def ready():
        available = options["limit"] - len(body) + 1
        data = bytes(reply.read(available))
        if data:
            result.setdefault("first_byte_seconds", round(time.monotonic() - started, 3))
            body.extend(data)
            result.update(stage="transferring", received_bytes=len(body))
            progress()
        if len(body) > options["limit"]:
            abort("size_limit")

    def finished():
        result["qt_error"] = int(reply.error())
        if result["reason"] == "unknown":
            if result.get("status") is not None and result["status"] != 200:
                result["reason"] = "http_status"
            elif reply.error() == QNetworkReply.NoError:
                if result.get("status") == 200:
                    result.update(stage="complete", outcome="success", reason="ok",
                                  body=base64.b64encode(body).decode("ascii"))
                else:
                    result["reason"] = "http_status"
            elif reply.error() in (QNetworkReply.TimeoutError, QNetworkReply.OperationCanceledError):
                result["reason"] = ("transfer_interrupted" if body else
                                    "response_timeout" if result["stage"] != "connecting" else "connection_timeout")
            elif 101 <= int(reply.error()) <= 105:
                result.update(reason="proxy_failed", stage="proxy_connection")
            elif reply.error() == QNetworkReply.SslHandshakeFailedError:
                result.update(stage="tls", reason="tls_failed")
            elif reply.error() == QNetworkReply.RemoteHostClosedError:
                result["reason"] = "transfer_interrupted" if body else "connection_closed_before_response"
            elif reply.error() == QNetworkReply.HostNotFoundError:
                result["reason"] = "dns_failed"
            else:
                result["reason"] = "transfer_interrupted" if body else "connection_or_response_failed"
        app.quit()

    reply.encrypted.connect(encrypted)
    reply.requestSent.connect(encrypted)
    reply.metaDataChanged.connect(metadata)
    reply.readyRead.connect(ready)
    reply.finished.connect(finished)
    connection.timeout.connect(lambda: abort("connection_timeout"))
    connection.start(round(options["profile"]["connect"] * 1000))
    progress()
    app.exec()
    connection.stop()
    reply.deleteLater()
    manager.deleteLater()
    app.processEvents()
    return result
