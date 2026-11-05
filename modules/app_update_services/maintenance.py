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
            from modules.platform_services.runtime import is_linux
            if is_linux():
                from .linux_update_cache import prune_completed as cleanup
            else:cleanup=prune_completed
            cleanup(self.root,current_version=self.version,cancel=self.isInterruptionRequested)
        except (OSError,ValueError,KeyError,TypeError):
            logging.getLogger("routermanager.update").warning("Startup update cache cleanup was deferred")
