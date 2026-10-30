"""Shared export actions for information panels and standalone buttons."""

from modules.display_privacy import Masker


class LogExportActions:
    def _export_logs(self):
        from PySide6.QtWidgets import QFileDialog
        from modules.common_widgets.common_dialogs import MessageDialog
        from modules.config_manager import user_downloads_dir
        from modules.log_services import LogExportService, build_export_filename
        owner = self.window()
        runtime = getattr(owner, 'runtime', None)
        if runtime is not None:
            if runtime._exiting:
                return
            service = runtime.log_export_service
        else:
            service = getattr(owner, 'log_export_service', None)
            if service is None:
                service = LogExportService(owner)
                owner.log_export_service = service
        if service.active:
            MessageDialog('\u5bfc\u51fa\u65e5\u5fd7', '\u5df2\u6709\u65e5\u5fd7\u6b63\u5728\u5bfc\u51fa\uff0c\u8bf7\u7b49\u5f85\u5b8c\u6210\u3002', owner).exec()
            return
        picked, _ = QFileDialog.getSaveFileName(owner, '\u5bfc\u51fa\u65e5\u5fd7',
            str(user_downloads_dir() / build_export_filename(self._log_category)), 'ZIP (*.zip)')
        if not picked:
            return
        if callable(self._diagnostic_provider):
            for ref in self._diagnostic_provider():
                self.add_diagnostic_reference(ref.path, ref.root, ref.kind)
        self._persist_diagnostics(force=True)
        references, omitted = self._diagnostic_references.snapshot()
        masker=self._privacy.snapshot() if self._privacy else Masker()
        try:
            router_context=self._export_context_provider(masker) if self._export_context_provider else ""
        except Exception as exc:
            MessageDialog("导出日志", "无法获取当前路由器诊断信息："+str(exc), owner).exec()
            return
        self._export_service = service
        service.completed.connect(self._export_completed)
        service.failed.connect(self._export_failed)
        service.activityChanged.connect(self._export_activity_changed)
        self.export_btn.setEnabled(False)
        version_label = getattr(getattr(owner, 'ui', None), 'version', None)
        from PySide6.QtWidgets import QApplication
        version = QApplication.instance().applicationVersion()
        if not version and version_label is not None:
            version = version_label.text().lstrip('v')
        started = service.start(destination=picked, category=self._log_category,
                      entries=self.export_entries(),
                      masker=masker, router_context=router_context,
                      references=references, omitted=omitted, app_version=version)
        if not started:
            service.completed.disconnect(self._export_completed)
            service.failed.disconnect(self._export_failed)
            service.activityChanged.disconnect(self._export_activity_changed)
            self._export_service = None
            self.export_btn.setEnabled(True)
            MessageDialog('\u5bfc\u51fa\u65e5\u5fd7', '\u53e6\u4e00\u4e2a\u5bfc\u51fa\u4efb\u52a1\u5df2\u5f00\u59cb\uff0c\u8bf7\u7b49\u5f85\u5b8c\u6210\u540e\u91cd\u8bd5\u3002', owner).exec()

    def _export_completed(self, result):
        if getattr(getattr(self.window(), 'runtime', None), '_exiting', False) or getattr(self.window(), '_export_close_pending', False):
            return
        from modules.common_widgets.common_dialogs import MessageDialog
        detail = ('\u5df2\u5bfc\u51fa\u5230\uff1a\n' + result['zip_path'] +
                  f"\n\u4fe1\u606f {result['info_lines']} \u884c\uff0c\u8bca\u65ad {result['diagnostics_included']} \u4e2a\u3002")
        if result['skipped']:
            detail += '\n\u8df3\u8fc7\uff1a\n' + '\n'.join(f"{v['file']}\uff1a{v['reason']}" for v in result['skipped'][:20]) + ('\n\u5176\u4f59\u8bb0\u5f55\u8be6\u89c1\u538b\u7f29\u5305\u7684\u9644\u4ef6\u7d22\u5f15\u3002' if len(result['skipped']) > 20 else '')
        MessageDialog('\u5bfc\u51fa\u65e5\u5fd7\u5b8c\u6210', self.display_text(detail), self.window()).exec()

    def _export_failed(self, reason):
        if getattr(getattr(self.window(), 'runtime', None), '_exiting', False) or getattr(self.window(), '_export_close_pending', False):
            return
        from modules.common_widgets.common_dialogs import MessageDialog
        MessageDialog('\u5bfc\u51fa\u65e5\u5fd7\u5931\u8d25',
            self.display_text(reason) + '\n\u8bf7\u68c0\u67e5\u76ee\u5f55\u6743\u9650\u548c\u78c1\u76d8\u7a7a\u95f4\u3002', self.window()).exec()

    def _export_activity_changed(self):
        if self._export_service and not self._export_service.active:
            self.export_btn.setEnabled(True)
            self._export_service.completed.disconnect(self._export_completed)
            self._export_service.failed.disconnect(self._export_failed)
            self._export_service.activityChanged.disconnect(self._export_activity_changed)
            self._export_service = None
