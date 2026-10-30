"""\u6709\u5927\u5c0f\u4e0a\u9650\u3001\u8d85\u65f6\u548c\u53d6\u6d88\u7684\u5f02\u6b65\u81ea\u8eab\u66f4\u65b0\u68c0\u67e5\u3002"""
from __future__ import annotations
import json
import time
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal, QUrl
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkProxy, QNetworkRequest, QNetworkReply
from .manifest import MAX_MANIFEST_BYTES, https_url, verify_manifest, version_key
from .trust import PUBLIC_KEY
from modules.platform_services.app_update_support import package_unavailable_reason, require_package_platform
from .diagnostics import record, endpoint_host, exception_chain, begin_operation, response_trace, route_identity


class UpdateChecker(QObject):
    """\u6240\u6709\u7f51\u7edc\u8bf7\u6c42\u90fd\u5728 Qt \u4e8b\u4ef6\u5faa\u73af\u4e2d\u6267\u884c\uff0c\u4e0d\u963b\u585e\u4e3b\u7ebf\u7a0b\u3002"""
    completed = Signal(object, bool)
    failed = Signal(str, bool)
    activityChanged = Signal()
    TIMEOUT_MS = 12000

    def __init__(self, current_version, config_path, parent=None, public_key=PUBLIC_KEY):
        super().__init__(parent)
        self.current_version = current_version
        version_key(current_version)
        self.config_path = Path(config_path)
        self.public_key = public_key
        self._manager = None
        self._reply = None
        self._data = bytearray()
        self._channels = []
        self._manual = False
        self._stopped = False
        self._error = ""
        self.resource_revision = 0
        self._load_revision()
        from modules.platform_services.app_update_access import read_code
        self.test_code = "" if package_unavailable_reason() else read_code()
        self.latest = None
        self.manifest_data = b""
        self._stage = "manifest"
        self._candidate = None
        self._deadline = QTimer(self)
        self._deadline.setSingleShot(True)
        self._deadline.timeout.connect(self._timeout)

    def _load_revision(self):
        path = self.config_path.parent.parent / "resources/app-update-baseline.json"
        if path.is_file():
            if path.stat().st_size > 8 * 1024 * 1024:
                raise ValueError("Installed baseline exceeds size limit")
            baseline = json.loads(path.read_text(encoding="utf-8"))
            if baseline.get("version") == self.current_version:
                self.resource_revision = int(baseline.get("resource_revision", 0))

    @property
    def active(self):
        return self._manager is not None

    def check(self, manual=False):
        """Run one check, trying direct after configured proxies fail."""
        if self.active:
            self._manual = self._manual or manual
            return False
        self._manual = manual
        self._stopped = False
        self._stage = "manifest"
        self._candidate = None
        self._route_failures = []
        begin_operation("check", self.current_version)
        record("check_started", version=self.current_version, manual=manual, testing=bool(self.test_code))
        try:
            package_platform = require_package_platform()
            if self.config_path.stat().st_size > 4096:
                raise ValueError("Update channel configuration is too large")
            config = json.loads(self.config_path.read_text(encoding="utf-8"))
            if config.get("schema") != 1 or config.get("enabled") is not True or not self.public_key:
                raise ValueError("\u81ea\u52a8\u66f4\u65b0\u670d\u52a1\u5c1a\u672a\u542f\u7528\u3002")
            self._url = https_url(config.get("manifest_url"))
            if package_platform == "linux-x64":
                from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
                parts = urlsplit(self._url)
                query = dict(parse_qsl(parts.query));query["platform"] = package_platform
                self._url = urlunsplit(parts._replace(query=urlencode(query)))
            from modules.network_policy import allowed_channels
            mode = config.get("network_mode", "auto")
            if mode not in {"auto", "proxy", "direct"}:
                raise ValueError("Invalid update network mode")
            self._channels = list(allowed_channels(None, "proxy" if mode != "direct" else "direct"))
            if not self._channels:
                raise ValueError("\u672a\u914d\u7f6e\u53ef\u7528\u7684\u4ee3\u7406\u3002")
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            record("check_configuration_failed", error_type=type(exc).__name__, chain=exception_chain(exc))
            self.failed.emit(str(exc), manual)
            return False
        record("routes_selected", network_mode=mode, routes=",".join(c.name for c in self._channels))
        self._manager = QNetworkAccessManager(self)
        # Per-request idle timeouts must leave a bounded budget for every route.
        self._deadline.start(self.TIMEOUT_MS * (len(self._channels) + 1))
        self.activityChanged.emit()
        self._next_channel()
        return True

    def _next_channel(self):
        """\u5728\u4e00\u4e2a\u603b\u8d85\u65f6\u5185\u6309\u516c\u5171\u7f51\u7edc\u7b56\u7565\u5c1d\u8bd5\u901a\u9053\u3002"""
        from urllib.parse import unquote, urlsplit
        while self._channels:
            channel = self._channels.pop(0)
            if channel.proxy_url:
                try:
                    parsed = urlsplit(channel.proxy_url)
                    port = parsed.port
                    if parsed.scheme not in {"http", "socks5", "socks5h"} or not parsed.hostname or not port:
                        record("route_skipped", route=getattr(channel,"name","unknown"), error="invalid_proxy")
                        continue
                    proxy = QNetworkProxy(
                        QNetworkProxy.HttpProxy if parsed.scheme == "http" else QNetworkProxy.Socks5Proxy,
                        parsed.hostname, port, unquote(parsed.username or ""), unquote(parsed.password or ""))
                except ValueError:
                    record("route_skipped", route=getattr(channel,"name","unknown"), error="invalid_proxy")
                    continue
            else:
                proxy = QNetworkProxy(QNetworkProxy.NoProxy)
            self._route_name = channel.name
            self._route_proxy_url = channel.proxy_url
            self._manager.setProxy(proxy)
            self._request()
            return
        self._finish(error="\u6ca1\u6709\u53ef\u7528\u7684\u66f4\u65b0\u7f51\u7edc\u7ebf\u8def\u3002")

    def _request(self):
        self._request_started_at = time.monotonic()
        record("request_started", stage=self._stage, route=getattr(self,"_route_name","unknown"), host=endpoint_host(self._url),
               route_id=route_identity(getattr(self,"_route_proxy_url","")), deadline_s=self.TIMEOUT_MS/1000)
        request = QNetworkRequest(QUrl(self._url))
        request.setRawHeader(b"User-Agent", ("RouterManager/" + self.current_version).encode("ascii"))
        request.setRawHeader(b"Accept", b"application/json")
        request.setRawHeader(b"X-RouterManager-Update-Mode", b"full-package")
        if self._stage == "manifest" and self.test_code:
            request.setRawHeader(b"X-RouterManager-Test-Code", self.test_code.encode("ascii"))
        request.setAttribute(QNetworkRequest.RedirectPolicyAttribute, QNetworkRequest.ManualRedirectPolicy)
        request.setMaximumRedirectsAllowed(3)
        request.setTransferTimeout(self.TIMEOUT_MS)
        self._data.clear()
        self._error = ""
        self._reply = self._manager.get(request)
        self._reply.readyRead.connect(self._read)
        self._reply.finished.connect(self._received)
        self._reply.sslErrors.connect(self._ssl_errors)

    def _ssl_errors(self, errors):
        record("tls_failed", stage=self._stage, route=getattr(self,"_route_name","unknown"),
               host=endpoint_host(self._url), ssl_errors=",".join(item.error().name for item in errors))

    def _read(self):
        if self._reply is None:
            return
        limit = self._candidate.catalog_size if self._stage == "catalog" else MAX_MANIFEST_BYTES
        if len(self._data) + self._reply.bytesAvailable() > limit:
            self._error = "\u66f4\u65b0\u4fe1\u606f\u8d85\u8fc7\u5927\u5c0f\u4e0a\u9650\u3002"
            record("response_limit_failed", stage=self._stage, received=len(self._data)+self._reply.bytesAvailable(), expected=limit)
            self._reply.abort()
        else:
            self._data.extend(bytes(self._reply.readAll()))

    def _received(self):
        reply = self._reply
        if reply is None:
            return
        self._read()
        self._reply = None
        status = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
        error = reply.error()
        publishing = bytes(reply.rawHeader(b"X-RouterManager-Release-State")) == b"publishing"
        record("request_finished", stage=self._stage, route=getattr(self,"_route_name","unknown"),
               host=endpoint_host(getattr(self,"_url","")), status=status, qt_error=str(error),
               cf_ray=response_trace(reply.rawHeader(b"CF-Ray")),
               received=len(getattr(self,"_data",b"")),
               elapsed_ms=round((time.monotonic()-getattr(self,"_request_started_at",time.monotonic()))*1000))
        reply.deleteLater()
        if self._stopped:
            return
        if self._error:
            self._finish(error=self._error)
        elif status == 204 and self._stage == "manifest":
            self.latest = None
            self.manifest_data = b""
            self._finish()
        elif status == 403 and self._stage == "manifest":
            record("testing_access_failed", status=403)
            self._finish(error="\u5185\u6d4b\u7801\u65e0\u6548\u6216\u5df2\u5931\u6548\uff0c\u8bf7\u66f4\u65b0\u5185\u6d4b\u7801\u6216\u9000\u51fa\u5185\u6d4b\u3002")
        elif status == 503 and publishing and self._stage == "manifest":
            self._finish(error="alpha \u7248\u672c\u6b63\u5728\u53d1\u5e03\uff0c\u8bf7\u7a0d\u540e\u91cd\u8bd5\u3002")
        elif error != QNetworkReply.NoError or status != 200:
            # Every route validates TLS and signatures. Access denial remains
            # final; proxy and transient gateway failures may change routes.
            from modules.network_policy import retryable_route_status
            # Explicit cancellation disconnects the reply; Qt's own transfer
            # timeout can report OperationCanceledError while the task is active.
            from .network_errors import route_failure, qt_failure_reason
            failures = getattr(self, "_route_failures", [])
            failures.append(route_failure(getattr(self, "_url", ""), getattr(self, "_route_proxy_url", ""),
                                          qt_failure_reason(error), status))
            self._route_failures = failures[-4:]
            if (status is None or retryable_route_status(status)) and self._channels:
                self._next_channel()
            else:
                self._finish(error="\u65e0\u6cd5\u8fde\u63a5\u66f4\u65b0\u670d\u52a1\u3002" + " ".join(self._route_failures))
        else:
            try:
                if self._stage == "catalog":
                    from .catalog import parse_catalog
                    release = replace(self._candidate, catalog=parse_catalog(bytes(self._data), self._candidate))
                else:
                    self.manifest_data = bytes(self._data)
                    release = verify_manifest(self.manifest_data, self.public_key)
                    if version_key(release.version)[1] in {0,1,2,3} and not self.test_code:
                        self.latest = None
                        self.manifest_data = b""
                        self._finish()
                        return
            except Exception as exc:
                record("validation_failed", stage=self._stage, error_type=type(exc).__name__, chain=exception_chain(exc))
                self._finish(error="\u66f4\u65b0\u4fe1\u606f\u6821\u9a8c\u5931\u8d25\uff0c\u5df2\u505c\u6b62\u4f7f\u7528\u5176\u4e0b\u8f7d\u5730\u5740\u3002")
            else:
                record("release_verified", version=release.version, revision=release.catalog_sha256 or "")
                self.latest = release
                self._finish(release=release if version_key(release.version) > version_key(self.current_version) else None)

    def _timeout(self):
        record("deadline_failed", stage=self._stage, error="timeout")
        self._finish(error="\u68c0\u67e5\u66f4\u65b0\u8d85\u65f6\uff0c\u8bf7\u7a0d\u540e\u91cd\u8bd5\u3002")

    def cancel(self):
        """\u9000\u51fa\u65f6\u7acb\u5373\u4e2d\u6b62\u8bf7\u6c42\u5e76\u91ca\u653e\u7f13\u51b2\uff0c\u4e0d\u9700\u7b49\u5f85\u7f51\u7edc\u3002"""
        if self.active:record("check_cancelled", stage=self._stage)
        self._stopped = True
        self._release()

    def _release(self):
        self._deadline.stop()
        if self._reply is not None:
            reply, self._reply = self._reply, None
            reply.readyRead.disconnect(self._read)
            reply.finished.disconnect(self._received)
            reply.abort()
            reply.deleteLater()
        if self._manager is not None:
            self._manager.deleteLater()
            self._manager = None
        self._channels.clear()
        self._data.clear()
        self.activityChanged.emit()

    def _finish(self, release=None, error=""):
        manual = self._manual
        record("check_failed" if error else "check_completed", stage=self._stage, result="error" if error else "available" if release else "current")
        self._release()
        if error:
            self.failed.emit(error, manual)
        else:
            self.completed.emit(release, manual)
