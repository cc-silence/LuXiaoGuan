"""Resident dependency snapshot tasks; GUI lifetime never owns remote work."""
from threading import Event
from uuid import uuid4
from PySide6.QtCore import QObject, Signal, Slot
from .task_completion import start_service_worker


class DependencyBackupService(QObject):
    changed = Signal()
    progress = Signal(object)
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.active = False
        self.cancel_event = None
        self.action = None
        self.last_progress = {}

    @Slot(object)
    def _progress(self, value):
        self.last_progress = dict(value)
        self.progress.emit(value)

    def backup(self, path):
        return self._start("backup", path)

    def preview(self, path):
        return self._start("preview", path)

    def restore(self, review):
        return self._start("restore", dict(review))

    def _start(self, action, argument):
        owner = self.owner
        backend = owner.openwrt_backend
        ssh = backend.ssh_client
        if self.active or owner.is_busy or owner._shutting_down or not ssh.connected:
            self.failed.emit("\u8bf7\u5148\u767b\u5f55\u8def\u7531\u5668\uff0c\u5e76\u7b49\u5f85\u5176\u4ed6\u4efb\u52a1\u5b8c\u6210\u3002")
            return False
        from .openwrt.connection_bound_runner import ConnectionBoundRunner
        from .openwrt.installed_dependency_backup import InstalledDependencyBackup
        binding = (ssh._client, ssh.host_fingerprint)
        runner = ConnectionBoundRunner(backend.command_runner, ssh, *binding)
        previous = owner.openwrt_snapshot
        event = self.cancel_event = Event()
        self.active = True
        self.action = action
        self.last_progress = {}
        self.changed.emit()

        def operation(cancel, progress):
            runner._check()
            task = InstalledDependencyBackup(runner, ssh, cancel, progress)
            value = getattr(task, action)(argument)
            if action == "restore":
                # Reuse the shared cross-page verification path; never install during refresh.
                from .dependency_state_sync import refresh_related
                names = set(value["installed"]) | set(value["skipped"])
                from .openwrt.dependency_manager import OpenWrtDependencyManager
                manager = OpenWrtDependencyManager(runner)
                value["dependencies"] = refresh_related(manager, names,
                                                         previous, cancel)
            return value

        def finished(value):
            self.active = False
            self.cancel_event = None
            self.changed.emit()
            if ssh._client is not binding[0] or ssh.host_fingerprint != binding[1] or not ssh.connected:
                self.failed.emit("\u8fde\u63a5\u5df2\u53d8\u5316\uff0c\u8bf7\u5728\u539f\u8def\u7531\u5668\u4e0a\u6838\u5bf9\u7ed3\u679c\u3002")
                return
            if action == "restore" and value.get("dependencies"):
                owner.dependency_service.publish_verified(value["dependencies"], binding, notify=True)
            self.completed.emit(value)

        def failed(summary, detail):
            self.active = False
            self.cancel_event = None
            self.changed.emit()
            owner.dependencyMessageGenerated.emit("\u4f9d\u8d56\u5907\u4efd\u4e0e\u6062\u590d\u672a\u5b8c\u6210\uff1a" + summary, "error")
            self.failed.emit(_friendly_error(summary))

        started = start_service_worker(owner, "dependency-backup-" + uuid4().hex,
                                       operation, event, finished, failed, self._progress)
        owner.activityChanged.emit()
        return started

    def cancel(self):
        if self.cancel_event is not None:
            self.cancel_event.set()



def _friendly_error(summary):
    if any("\u4e00" <= c <= "\u9fff" for c in summary):
        return summary
    messages = {
        "Pending package operation must be resolved first:": "\u8def\u7531\u5668\u6709\u672a\u5b8c\u6210\u7684\u8f6f\u4ef6\u5305\u5b89\u88c5\u6216\u5378\u8f7d\uff0c\u8bf7\u5148\u4fee\u590d\uff0c\u672c\u6b21\u64cd\u4f5c\u5df2\u505c\u6b62\uff1a",
        "Cannot preserve package extended attributes:": "\u4f9d\u8d56\u542b\u6709\u6682\u65f6\u65e0\u6cd5\u5b8c\u6574\u63d0\u53d6\u7684\u6269\u5c55\u6743\u9650\uff0c\u5df2\u505c\u6b62\u5907\u4efd\uff0c\u907f\u514d\u6062\u590d\u540e\u6743\u9650\u4e22\u5931\uff1a",
        "No stable hardware address": "\u65e0\u6cd5\u8bfb\u53d6\u7a33\u5b9a\u8bbe\u5907\u6807\u8bc6\uff0c\u6682\u4e0d\u80fd\u5b89\u5168\u786e\u8ba4\u540c\u673a\u6062\u590d\u3002",
        "Installed dependency is missing:": "\u73b0\u6709\u5b89\u88c5\u4e0d\u5b8c\u6574\uff0c\u7f3a\u5c11\u9644\u5c5e\u4f9d\u8d56\uff1a",
        "Installed dependency is broken:": "\u5df2\u5b89\u88c5\u7684\u4f9d\u8d56\u72b6\u6001\u5f02\u5e38\uff0c\u8bf7\u5148\u4fee\u590d\uff1a",
        "APK reports broken package:": "\u4f9d\u8d56\u5b89\u88c5\u4e0d\u5b8c\u6574\uff0c\u8bf7\u5148\u4fee\u590d\uff1a",
        "Package owns a protected path; backup stopped:": "\u4f9d\u8d56\u6d89\u53ca\u53d7\u4fdd\u62a4\u7684\u7cfb\u7edf\u6587\u4ef6\uff0c\u5df2\u505c\u6b62\u5907\u4efd\uff1a",
        "Dependency backup/restore cancelled": "\u64cd\u4f5c\u5df2\u53d6\u6d88\u3002",
        "Dependency backup/restore timed out": "\u64cd\u4f5c\u8d85\u65f6\uff0c\u8bf7\u68c0\u67e5\u8def\u7531\u5668\u8fde\u63a5\u540e\u91cd\u8bd5\u3002",
    }
    messages.update({
        "Affected configuration package version differs:": "\u76f8\u5173\u4f9d\u8d56\u7684\u7248\u672c\u5df2\u53d8\u5316\uff0c\u521d\u59cb\u5316\u811a\u672c\u53ef\u80fd\u6539\u5230\u65b0\u7248\u914d\u7f6e\uff0c\u5df2\u5728\u5b89\u88c5\u524d\u505c\u6b62\u6062\u590d\uff0c\u8bf7\u5148\u91cd\u65b0\u5907\u4efd\uff1a",
        "Exact original unavailable:": "\u627e\u4e0d\u5230\u5df2\u5b89\u88c5\u7248\u672c\u7684\u539f\u59cb\u5b89\u88c5\u5305\uff0c\u65e0\u6cd5\u8865\u9f50\u521d\u59cb\u5316\u811a\u672c\uff0c\u672a\u751f\u6210\u5907\u4efd\uff1a",
        "Exact original unavailable for this firmware:": "\u672c\u673a\u79bb\u7ebf\u5305\u6ca1\u6709\u8be5\u7248\u672c\u539f\u5305\uff0c\u4e14\u4e0d\u80fd\u7528\u5176\u4ed6\u56fa\u4ef6\u7684\u5305\u4ee3\u66ff\uff0c\u672a\u751f\u6210\u5907\u4efd\uff1a",
        "Missing ordinary package payload:": "\u4f9d\u8d56\u7684\u666e\u901a\u7a0b\u5e8f\u6587\u4ef6\u7f3a\u5931\uff0c\u8fd9\u4e0d\u5c5e\u4e8e\u6b63\u5e38\u5220\u9664\u7684\u521d\u59cb\u5316\u811a\u672c\uff0c\u672a\u751f\u6210\u5907\u4efd\uff1a",
        "Original IPK identity mismatch": "\u539f\u59cb\u5b89\u88c5\u5305\u7684\u540d\u79f0\u3001\u7248\u672c\u6216\u67b6\u6784\u4e0d\u5339\u914d\uff0c\u672a\u751f\u6210\u5907\u4efd\u3002",
        "Original initialization file missing or unsafe:": "\u539f\u5305\u4e2d\u4e5f\u627e\u4e0d\u5230\u53ef\u7528\u7684\u521d\u59cb\u5316\u811a\u672c\uff0c\u672a\u751f\u6210\u5907\u4efd\uff1a",
    })
    for prefix, message in messages.items():
        if summary.startswith(prefix):
            return message + summary[len(prefix):].strip()
    if "checksum" in summary.lower() or "Truncated" in summary or "Incomplete" in summary:
        return "\u5907\u4efd\u6216\u4f20\u8f93\u6587\u4ef6\u4e0d\u5b8c\u6574\uff0c\u8bf7\u91cd\u65b0\u5907\u4efd\u6216\u68c0\u67e5\u8fde\u63a5\u540e\u91cd\u8bd5\u3002"
    return "\u64cd\u4f5c\u672a\u5b8c\u6210\uff0c\u672a\u786e\u8ba4\u652f\u6301\u5f53\u524d\u6587\u4ef6\u6216\u56fa\u4ef6\u72b6\u6001\u3002\u8bf7\u67e5\u770b\u4fe1\u606f\u7a97\u53e3\u7684\u5177\u4f53\u539f\u56e0\uff0c\u5e76\u5bfc\u51fa\u65e5\u5fd7\u3002"
