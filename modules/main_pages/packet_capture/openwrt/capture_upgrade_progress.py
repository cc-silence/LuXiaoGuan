"""Explicit upgrade and install requests using the shared capture-task progress binding."""
from .capture_dependency_progress import CaptureDependencyProgress


class CaptureUpgradeProgress(CaptureDependencyProgress):
    def __init__(self, page, service):
        self.requesting = False
        super().__init__(page, service, self._accepts,
                         title="升级抓包工具")

    def _accepts(self, task):
        return self.requesting and task.group == "capture" and task.items == ("pmkid",)

    def start(self):
        if self.dialog is not None:
            self.dialog.show()
            self.dialog.raise_()
            self.dialog.activateWindow()
            return True
        self.requesting = True
        try:
            return self.service.upgrade_hcx_async()
        finally:
            self.requesting = False


class CaptureInstallProgress(CaptureDependencyProgress):
    def __init__(self, page, service):
        self.requesting = False
        super().__init__(page, service, self._accepts,
                         title="安装自动抓包依赖")

    def _accepts(self, task):
        return self.requesting and task.group in ("capture", "auto_capture") and (
            "pmkid" in getattr(task, "items", ()) or "sftp" in getattr(task, "items", ())
        )

    def start(self):
        if self.dialog is not None:
            self.dialog.show()
            self.dialog.raise_()
            self.dialog.activateWindow()
            return True
        self.requesting = True
        try:
            return self.service.install_async({"pmkid": True, "signal": True, "iw": True}, scope="auto_capture")
        finally:
            self.requesting = False
