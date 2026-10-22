"""Two-stage offline resources: inspect size, confirm, then download."""
from functools import partial
from threading import Event
from uuid import uuid4
from PySide6.QtCore import QObject, Signal


class CommonBundleDownloadService(QObject):
    """\u4e3b\u7ebf\u7a0b\u786e\u8ba4\u524d\u53ea\u67e5\u6e05\u5355\uff0c\u5e38\u9a7b\u670d\u52a1\u62e5\u6709\u4e0b\u8f7d\u4e0e\u9000\u51fa\u56de\u6536\u3002"""
    changed = Signal()
    progress = Signal(object)
    finished = Signal(bool, str)
    offerReady = Signal(object)

    def __init__(self, owner, kind="common"):
        super().__init__(owner)
        from .openwrt.bundle_specs import bundle_spec
        self.spec = bundle_spec(kind)
        self.owner = owner
        self.active = False
        self.phase = "idle"
        self.message = ""
        self.cancel_event = None
        self.offer = None
        self.download_size = 0
        self.local_status = dict(installed=False, reason="")

    def _reject(self, reason):
        from .workflow_diagnostics import record_workflow
        record_workflow(self.owner, "offline_bundle_request", "blocked", reason=reason)
        self.message = reason
        self.changed.emit()
        self.owner.settingsMessageGenerated.emit(reason, "warning")
        return False

    def _available(self):
        if self.owner._shutting_down:
            return self._reject("\u8f6f\u4ef6\u6b63\u5728\u9000\u51fa\uff0c\u79bb\u7ebf\u5305\u4efb\u52a1\u672a\u5f00\u59cb\u3002")
        if self.active or self.owner.is_busy:
            return self._reject("\u5df2\u6709\u4efb\u52a1\u8fd0\u884c\uff0c\u8bf7\u7b49\u5f85\u7ed3\u675f\u540e\u518d\u68c0\u67e5\u6216\u4e0b\u8f7d\u79bb\u7ebf\u5305\u3002")
        return True

    def refresh_local(self):
        """Background-only local detection, also usable without a router connection."""
        if self.active or self.owner._shutting_down:
            return self._reject("\u79bb\u7ebf\u5305\u4efb\u52a1\u6b63\u5728\u8fd0\u884c\u6216\u8f6f\u4ef6\u6b63\u5728\u9000\u51fa\uff0c\u672c\u5730\u68c0\u67e5\u672a\u5f00\u59cb\u3002")
        from .openwrt.bundle_status import local_bundle_status
        return self._run("local_check", partial(local_bundle_status, kind=self.spec.kind), self._local_checked)

    def _local_checked(self, result):
        self.local_status = result
        self.active, self.phase, self.cancel_event = False, "idle", None
        self.message = ("已检测到本地离线包，资源版本：" + result.get("label", "")
                        if result.get("installed") else result.get("reason", ""))
        self.changed.emit()

    def inspect(self):
        """\u540e\u53f0\u83b7\u53d6\u5927\u5c0f\uff0c\u4e0d\u521b\u5efa\u79bb\u7ebf\u5305\u6587\u4ef6\u6216\u81ea\u52a8\u4e0b\u8f7d\u3002"""
        if not self._available():
            return False
        from .openwrt.common_bundle_download import inspect_bundle
        self.offer = None
        return self._run("checking", partial(inspect_bundle, kind=self.spec.kind), self._inspected)

    def start(self):
        """\u7528\u6237\u786e\u8ba4\u540e\u4e0b\u8f7d\u540c\u4e00\u7b7e\u540d\u7248\u672c\uff0c\u4e0d\u91cd\u65b0\u8bfb\u53d6\u53ef\u80fd\u5df2\u6539\u53d8\u7684\u6e05\u5355\u3002"""
        if not self._available():
            return False
        if self.offer is None:
            return self._reject("\u4e0b\u8f7d\u786e\u8ba4\u5df2\u5931\u6548\uff0c\u8bf7\u91cd\u65b0\u68c0\u67e5\u79bb\u7ebf\u5305\u66f4\u65b0\u3002")
        from .openwrt.common_bundle_download import download_bundle
        raw = self.offer["manifest"]
        self.download_size = self.offer["payload"]["size"]
        self.offer = None
        return self._run("downloading", partial(download_bundle, kind=self.spec.kind, manifest=raw), self._succeeded)

    def _run(self, phase, operation, succeeded):
        self.active, self.phase = True, phase
        self.message = "\u6b63\u5728\u83b7\u53d6\u79bb\u7ebf\u5305\u5927\u5c0f\u2026" if phase == "checking" else "\u6b63\u5728\u51c6\u5907\u4e0b\u8f7d\u2026"
        if phase == "local_check":
            self.message = "正在检查本地离线包版本…"
        elif phase == "checking":
            self.message = "正在检测离线包更新…"
        self.cancel_event = Event()
        self.changed.emit()
        from .task_completion import start_service_worker
        started = start_service_worker(
            self.owner, "offline-bundle-" + uuid4().hex, operation, self.cancel_event,
            succeeded, self._failed, self.progress.emit, self._stage)
        self.owner.activityChanged.emit()
        return started

    def _inspected(self, result):
        """\u7ebf\u7a0b\u7ed3\u675f\u540e\u4ea4\u7ed9\u9875\u9762\u5f02\u6b65\u786e\u8ba4\uff0c\u907f\u514d\u5d4c\u5957\u4e8b\u4ef6\u5faa\u73af\u5360\u4f4f\u65e7\u4efb\u52a1\u3002"""
        if self.cancel_event.is_set() or self.owner._shutting_down:
            self._finish(False, "\u83b7\u53d6\u79bb\u7ebf\u5305\u5927\u5c0f\u5df2\u53d6\u6d88\u3002")
            return
        self.local_status = result.get("local_status", self.local_status)
        if result.get("comparison") in ("current", "local_newer"):
            text = "本地离线包已是最新版本，无需重复下载。" if result["comparison"] == "current" else "本地离线包版本更新，不降级已有资源。"
            self._finish(True, text + "\n当前资源版本：" + self.local_status.get("label", ""))
            return
        self.active, self.phase = False, "confirming"
        self.cancel_event = None
        self.offer = result
        self.message = ""
        self.changed.emit()
        self.offerReady.emit(result["payload"])

    def decline(self):
        """\u53d6\u6d88\u786e\u8ba4\u65f6\u4e22\u5f03\u5f53\u6b21\u6e05\u5355\uff0c\u4e0d\u4fdd\u5b58\u4e0b\u8f7d\u6388\u6743\u3002"""
        if self.phase == "confirming":
            self.offer = None
            self.phase, self.message = "idle", ""
            self.changed.emit()

    def cancel(self):
        if self.cancel_event is not None:
            self.cancel_event.set()
        self.decline()

    def _stage(self, text, _level):
        self.owner.settingsMessageGenerated.emit(str(text), str(_level))
        self.message = str(text)
        self.changed.emit()

    def _succeeded(self, result):
        from .openwrt.bundle_status import revision_label
        payload = result.get("payload", {})
        from .openwrt.common_bundle_repository import bundle_root, safe_path
        location = safe_path(bundle_root(self.spec.kind), "versions/" + result["sha256"])
        self.local_status = dict(installed=True, path=str(location.resolve()), revision=result["sha256"],
                                 published_at=payload.get("published_at", ""), label=revision_label(payload))
        self._finish(True, self.spec.title + "\u5305\u5df2\u4e0b\u8f7d\u5e76\u6821\u9a8c\uff0c\u65ad\u7f51\u540e\u53ef\u5728\u5f53\u524d\u8f6f\u4ef6\u4e2d\u5b89\u88c5\u3002" + "\n当前资源版本：" + self.local_status["label"])

    def _failed(self, summary, _detail):
        cancelled = self.cancel_event is not None and self.cancel_event.is_set()
        self._finish(False, "\u79bb\u7ebf\u5305\u4e0b\u8f7d\u5df2\u53d6\u6d88\u3002" if cancelled else str(summary))

    def _finish(self, success, message):
        self.active, self.phase = False, "idle"
        self.offer = None
        self.message = message
        self.cancel_event = None
        self.changed.emit()
        self.owner.settingsMessageGenerated.emit(message, "success" if success else "info" if "\u53d6\u6d88" in message else "error")
        self.finished.emit(success, message)
