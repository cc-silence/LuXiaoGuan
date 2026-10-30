"""Explicit upgrade request using the shared capture-task progress binding."""
from .capture_dependency_progress import CaptureDependencyProgress


class CaptureUpgradeProgress(CaptureDependencyProgress):
    def __init__(self, page, service):
        self.requesting = False
        super().__init__(page, service, self._accepts,
                         title="\u5347\u7ea7\u6293\u5305\u5de5\u5177")

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
