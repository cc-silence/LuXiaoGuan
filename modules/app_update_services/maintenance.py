"""One cancellable startup cleanup task, owned by the application update service."""
import logging
from PySide6.QtCore import QThread
from .cache import prune_completed


class UpdateCacheMaintenance(QThread):
    def __init__(self, root, version, parent=None):
        super().__init__(parent)
        self.root=root
        self.version=version

    def run(self):
        try:
            prune_completed(self.root,current_version=self.version,cancel=self.isInterruptionRequested)
        except (OSError,ValueError,KeyError,TypeError):
            logging.getLogger("routermanager.update").warning("Startup update cache cleanup was deferred")
