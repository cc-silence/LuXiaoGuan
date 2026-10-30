"""Automatic capture export button using the common export actions."""
from PySide6.QtCore import QObject
from modules.log_export_actions import LogExportActions
from modules.log_services import DiagnosticReferences
from modules.display_privacy import controller_for
from modules.capture_services.local_capture_diagnostics import CATEGORY, context_for

class AutoCaptureLogExport(LogExportActions, QObject):
    def __init__(self, page, button):
        super().__init__(page)
        self.page = page
        self.export_btn = button
        self._log_category = CATEGORY
        self._privacy = controller_for(page)
        self._diagnostic_provider = None
        self._diagnostic_references = DiagnosticReferences()
        self._export_context_provider = lambda masker: context_for(page.service,masker)
        self._export_service = None
        button.clicked.connect(self._export_logs)

    def window(self):
        return self.page.window()

    def display_text(self, text):
        return self._privacy.text(text) if self._privacy else str(text)

    def export_entries(self):
        return ()

    def add_diagnostic_reference(self, path, root=None, kind='text'):
        self._diagnostic_references.add(path,root,kind)

    def _persist_diagnostics(self, force=False):
        recorder = getattr(self.page.auto_capture_service,'diagnostics',None)
        if recorder:
            recorder.capture_context(force=True)
