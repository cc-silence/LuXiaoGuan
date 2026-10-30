"""\u66f4\u65b0\u4e2d\u5fc3\uff1a\u8de8\u7248\u672c\u8bf4\u660e\u3001\u6587\u4ef6\u8ba1\u5212\u4e0e\u5b89\u5168\u91cd\u542f\u5165\u53e3\u3002"""
from PySide6.QtCore import Qt,QEvent
from modules.release_identity import DISPLAY_NAME
from PySide6.QtWidgets import QApplication,QHBoxLayout,QLabel,QLineEdit,QTextEdit,QWidget,QVBoxLayout
from modules.common_widgets.combo_box import CommonComboBox
from modules.common_widgets.rounded_progress_bar import RoundedProgressBar
from .settings_widgets import create_hint_label,create_settings_button,create_settings_scroll_page,create_settings_section_title,create_tool_button,get_settings_tool_icon


def download_size(value):
    if value<1024:return f"{value} B"
    if value<1024*1024:return f"{value/1024:.1f} KB"
    return f"{value/1024/1024:.2f} MB"


class AppUpdatePage(QWidget):
    """\u754c\u9762\u53ea\u8ba2\u9605\u516c\u5171\u66f4\u65b0\u670d\u52a1\uff0c\u4e0d\u6301\u6709\u7f51\u7edc\u6216\u66ff\u6362\u4efb\u52a1\u3002"""
    def __init__(self,presenter,parent=None):
        super().__init__(parent)
        self.presenter=presenter
        self._notes_key=None
        outer=QVBoxLayout(self);outer.setContentsMargins(0,0,0,0)
        page,container,layout=create_settings_scroll_page(self);outer.addWidget(page)
        layout.addWidget(create_settings_section_title(DISPLAY_NAME + " \u66f4\u65b0",container))
        self.version_label=QLabel(container);self.version_label.setTextFormat(Qt.PlainText);layout.addWidget(self.version_label)
        self.status_label=QLabel(container);self.status_label.setTextFormat(Qt.PlainText);self.status_label.setWordWrap(True);layout.addWidget(self.status_label)
        self.checked_label=create_hint_label("",container);layout.addWidget(self.checked_label)
        self.plan_label=create_hint_label("",container);self.plan_label.setTextFormat(Qt.PlainText);layout.addWidget(self.plan_label)
        self.progress_bar=RoundedProgressBar(container);self.progress_bar.setObjectName("taskDialogProgressBar")
        self.progress_bar.setTextVisible(False);layout.addWidget(self.progress_bar)
        self.progress_label=QLabel(container);layout.addWidget(self.progress_label)
        row=QHBoxLayout()
        self.check_button=create_settings_button("\u68c0\u67e5\u66f4\u65b0",container,"primary")
        self.download_button=create_settings_button("\u4e0b\u8f7d\u66f4\u65b0",container,"secondary")
        self.cancel_button=create_settings_button("\u53d6\u6d88\u4e0b\u8f7d",container,"secondary")
        self.check_button.clicked.connect(self._check);self.download_button.clicked.connect(self._download)
        self.cancel_button.clicked.connect(self._cancel)
        for button in (self.check_button,self.download_button,self.cancel_button):row.addWidget(button)
        row.addStretch()
        self.export_log_button=create_settings_button("\u5bfc\u51fa\u65e5\u5fd7",container,"secondary")
        self.export_log_button.clicked.connect(self._export_logs)
        row.addWidget(self.export_log_button);layout.addLayout(row)
        self.export_hint=create_hint_label("",container)
        self.export_hint.setTextFormat(Qt.PlainText);self.export_hint.hide();layout.addWidget(self.export_hint)
        if presenter is not None:presenter.log_exporter.changed.connect(self._refresh_export)
        self._refresh_export()
        self.package_link_area=QWidget(container)
        package_layout=QVBoxLayout(self.package_link_area);package_layout.setContentsMargins(0,0,0,0)
        package_layout.addWidget(create_hint_label("\u5b8c\u6574\u5305\u4e0b\u8f7d\u94fe\u63a5\uff1a\u8f6f\u4ef6\u5185\u4e0b\u8f7d\u5931\u8d25\u65f6\uff0c\u53ef\u590d\u5236\u5230\u6d4f\u89c8\u5668\u4e0b\u8f7d\u3002",self.package_link_area))
        package_row=QHBoxLayout()
        self.package_link=QLineEdit(self.package_link_area);self.package_link.setObjectName("settingsLineEdit")
        self.package_link.setReadOnly(True)
        self.package_link.setAccessibleName("\u5b8c\u6574\u5305\u4e0b\u8f7d\u94fe\u63a5")
        self.copy_package_link_button=create_settings_button("\u590d\u5236\u94fe\u63a5",self.package_link_area,"secondary")
        self.copy_package_link_button.clicked.connect(self._copy_package_link)
        package_row.addWidget(self.package_link,1);package_row.addWidget(self.copy_package_link_button)
        package_layout.addLayout(package_row);layout.addWidget(self.package_link_area)
        self.view_mode=CommonComboBox(container)
        self.view_mode.addItems(["\u672c\u6b21\u5168\u90e8\u66f4\u65b0\u8bf4\u660e","\u6240\u6709\u5386\u53f2\u7248\u672c"])
        self.view_mode.currentIndexChanged.connect(self.refresh);layout.addWidget(self.view_mode)
        self.notes=QTextEdit(container);self.notes.setObjectName("settingsMessageText");self.notes.setReadOnly(True)
        from modules.ui_scale_manager import UiScaleManager
        UiScaleManager.for_widget(parent).bind_metrics(self.notes,min_height=150)
        layout.addWidget(self.notes,1)
        self.hint=create_hint_label("\u5728\u8f6f\u4ef6\u5185\u4e0b\u8f7d\u5b8c\u6574\u5b89\u88c5\u5305\uff0c\u6821\u9a8c\u6210\u529f\u540e\u81ea\u52a8\u5b89\u88c5\u5e76\u91cd\u542f\u3002\u4fdd\u7559\u7528\u6237\u914d\u7f6e\u548c\u6570\u636e\uff0c\u5b89\u88c5\u5931\u8d25\u4f1a\u56de\u6eda\u3002",container)
        layout.addWidget(self.hint)
        access_row=QHBoxLayout()
        self.test_code_input=QLineEdit(container);self.test_code_input.setObjectName("settingsLineEdit")
        self.test_code_input.setEchoMode(QLineEdit.Password);self.test_code_input.setMaxLength(96)
        self.test_code_input.setPlaceholderText("\u8f93\u5165\u5185\u6d4b\u7801\uff0c\u63a5\u6536 alpha / beta \u66f4\u65b0")
        self.test_code_input.setText(presenter.checker.test_code if presenter else "")
        self.test_code_toggle=create_tool_button(":/icons/images/icons/cil-low-vision.png",container)
        self.test_code_toggle.setToolTip("\u663e\u793a\u5185\u6d4b\u7801")
        self.test_code_toggle.setCheckable(True)
        self.test_code_toggle.setAccessibleName("\u663e\u793a\u6216\u9690\u85cf\u5185\u6d4b\u7801")
        self.test_code_toggle.toggled.connect(self._toggle_test_code)
        self.test_code_button=create_settings_button("\u4fdd\u5b58\u5e76\u9a8c\u8bc1",container,"secondary")
        self.leave_test_button=create_settings_button("\u9000\u51fa\u5185\u6d4b",container,"secondary")
        self.test_code_button.clicked.connect(lambda:self.presenter and self.presenter.set_test_code(self.test_code_input.text(),self))
        self.test_code_input.returnPressed.connect(self.test_code_button.click)
        self.leave_test_button.clicked.connect(self._leave_testing)
        access_row.addWidget(self.test_code_input,1);access_row.addWidget(self.test_code_toggle);access_row.addWidget(self.test_code_button);access_row.addWidget(self.leave_test_button)
        layout.addLayout(access_row)
        self.access_hint=create_hint_label("",container);layout.addWidget(self.access_hint)
        if presenter is not None:presenter.stateChanged.connect(self.refresh)
        self.refresh()

    def _copy_package_link(self):
        if self.presenter is None or self.presenter.release is None:return
        release=self.presenter.release
        QApplication.clipboard().setText(release.url)
        from modules.common_widgets.floating_feedback import FloatingFeedback
        from modules.ui_scale_manager import UiScaleManager
        feedback=getattr(self,"_copy_feedback",None)
        if feedback is None:
            feedback=self._copy_feedback=FloatingFeedback(self)
        feedback.show_feedback("复制成功","success",UiScaleManager.for_widget(self))
        from modules.app_update_services.diagnostics import record,endpoint_host
        record("full_package_link_copied", host=endpoint_host(release.url), version=release.version)

    def _export_logs(self):
        if self.presenter is not None:self.presenter.log_exporter.start(self)

    def _refresh_export(self):
        exporter=self.presenter.log_exporter if self.presenter is not None else None
        self.export_log_button.setEnabled(exporter is not None and not exporter.active)
        self.export_hint.setText(exporter.message if exporter else "")
        self.export_hint.setVisible(bool(exporter and exporter.message))

    def _toggle_test_code(self, visible):
        self.test_code_input.setEchoMode(QLineEdit.Normal if visible else QLineEdit.Password)
        from modules.dialog_theme import detect_light_theme
        icon_path=":/icons/images/icons/cil-lock-locked.png" if visible else ":/icons/images/icons/cil-low-vision.png"
        self.test_code_toggle.setProperty("iconPath",icon_path)
        self.test_code_toggle.setIcon(get_settings_tool_icon(icon_path,detect_light_theme(self)))
        self.test_code_toggle.setToolTip("\u9690\u85cf\u5185\u6d4b\u7801" if visible else "\u663e\u793a\u5185\u6d4b\u7801")

    def hideEvent(self,event):
        self.test_code_toggle.setChecked(False)
        feedback=getattr(self,"_copy_feedback",None)
        if feedback is not None:feedback.hide()
        super().hideEvent(event)

    def _leave_testing(self):
        self.test_code_toggle.setChecked(False)
        self.test_code_input.clear()
        if self.presenter:self.presenter.set_test_code("",self)

    def _apply_progress_style(self):
        """\u8ddf\u968f\u516c\u5171\u4e3b\u9898\u4e0e\u7f29\u653e\uff0c\u53ea\u5728\u6837\u5f0f\u53d8\u5316\u65f6\u66f4\u65b0\u3002"""
        from modules.dialog_theme import detect_light_theme,task_progress_stylesheet
        from modules.ui_scale_manager import UiScaleManager
        style=task_progress_stylesheet(detect_light_theme(self),UiScaleManager.for_widget(self))
        if self.progress_bar.styleSheet()!=style:self.progress_bar.setStyleSheet(style)

    def showEvent(self,event):
        self._apply_progress_style()
        super().showEvent(event)

    def changeEvent(self,event):
        super().changeEvent(event)
        if event.type()==QEvent.StyleChange and hasattr(self,"progress_bar"):self._apply_progress_style()

    def _check(self):
        if self.presenter is not None:self.presenter.check_manually(self,inline=True)

    def _download(self):
        presenter=self.presenter
        if presenter is None:return
        if presenter.state=="ready":presenter.install_update(self)
        else:presenter.prepare_plan(download=True)

    def _cancel(self):
        if self.presenter is not None:self.presenter.cancel_transfer()

    def refresh(self):
        """\u9875\u9762\u91cd\u5f00\u6216\u5207\u6362\u5386\u53f2\u89c6\u56fe\u53ea\u8bfb\u5185\u5b58\uff0c\u4e0d\u91cd\u590d\u8054\u7f51\u3002"""
        p=self.presenter;release=p.release if p else None;plan=p.plan if p else None;busy=bool(p and p.active)
        self.package_link_area.setVisible(release is not None)
        package_url=release.url if release else ""
        if self.package_link.text()!=package_url:
            self.package_link.setText(package_url)
            self.package_link.setCursorPosition(0)
        self.copy_package_link_button.setEnabled(release is not None)
        self.test_code_button.setEnabled(p is not None and not busy)
        self.test_code_input.setEnabled(not busy)
        self.leave_test_button.setEnabled(p is not None and not busy and bool(p.checker.test_code))
        self.access_hint.setText("\u5185\u6d4b\u5df2\u542f\u7528\uff1a\u53ef\u63a5\u6536\u6b63\u5f0f\u7248\u3001alpha \u548c beta \u66f4\u65b0\u3002" if p and p.checker.test_code and p._pending_test_code is None else "\u672a\u542f\u7528\u5185\u6d4b\uff1a\u53ea\u63a5\u6536\u6b63\u5f0f\u7248\u66f4\u65b0\u3002")
        progress_visible=bool(p and p.state in {"planning","downloading","ready"})
        self.progress_bar.setVisible(progress_visible);self.progress_label.setVisible(progress_visible)
        total=p.download_total if p and p.state=="downloading" else 0;done=p.download_completed if p else 0
        if p and p.state=="ready":
            self.progress_bar.setRange(0,1000);self.progress_bar.setValue(1000);self.progress_label.setText("100%")
        else:
            self.progress_bar.setRange(0,1000 if total else 0)
            self.progress_bar.setValue(min(1000,round(done*1000/total)) if total else 0)
            self.progress_label.setText(f"{min(100,round(done*100/total))}%" if total else "")
        self.check_button.setEnabled(p is not None and not busy)
        self.check_button.setText("\u6b63\u5728\u68c0\u67e5\u2026" if p and p.state=="checking" else "\u68c0\u67e5\u66f4\u65b0")
        self.download_button.setVisible(release is not None)
        incremental=False
        queueable=bool(p and p.state=="planning" and plan and not p._download_requested)
        self.download_button.setEnabled(p is not None and (not busy or queueable) and (not incremental or (plan is not None and not plan["conflicts"])))
        action_ready=bool(release is not None and self.download_button.isEnabled())
        if self.download_button.property("updateActionReady") != action_ready:
            self.download_button.setProperty("updateActionReady",action_ready)
            self.download_button.style().unpolish(self.download_button)
            self.download_button.style().polish(self.download_button)
            self.download_button.update()
        self.download_button.setText("\u91cd\u542f\u5e76\u5b89\u88c5\u66f4\u65b0" if p and p.state=="ready" else "\u4e0b\u8f7d\u5e76\u5b89\u88c5\u65b0\u7248")
        self.cancel_button.setVisible(bool(p and p._worker is not None))
        self.version_label.setText("\u5f53\u524d\u7248\u672c\uff1a"+(p.checker.current_version if p else "\u672a\u77e5")+(f"  (\u8d44\u6e90\u66f4\u65b0 {p.checker.resource_revision})" if p and p.checker.resource_revision else ""))
        messages={"idle":"\u5c1a\u672a\u68c0\u67e5\u66f4\u65b0\u3002","checking":"\u6b63\u5728\u68c0\u67e5\u66f4\u65b0\u2026",
                  "planning":"\u6b63\u5728\u540e\u53f0\u6838\u5bf9\u672c\u673a\u6587\u4ef6\u2026","current":"\u5f53\u524d\u5df2\u662f\u6700\u65b0\u7248\u672c\u3002",
                  "available":"\u53d1\u73b0\u65b0\u7248\u672c\uff1a"+((release.version+(f" (\u8d44\u6e90\u66f4\u65b0 {release.resource_revision})" if release.resource_revision else "")) if release else ""),
                  "downloading":"\u6b63\u5728\u4e0b\u8f7d\u5e76\u6821\u9a8c\u66f4\u65b0\uff1a"+(p.progress_text if p else ""),
                  "ready":"\u66f4\u65b0\u5df2\u51c6\u5907\u597d\uff0c\u70b9\u51fb\u91cd\u542f\u540e\u5b89\u88c5\u5e76\u81ea\u52a8\u91cd\u65b0\u6253\u5f00\u3002",
                  "error":p.error if p else ""}
        self.status_label.setText((p.error or messages.get(p.state,"")) if p else "\u8bf7\u4ece RouterManager \u6b63\u5f0f\u5165\u53e3\u542f\u52a8\u3002")
        self.checked_label.setText("\u6700\u8fd1\u6210\u529f\u68c0\u67e5\uff1a"+p.last_checked if p and p.last_checked else "")
        self.checked_label.setVisible(bool(p and p.last_checked))
        summary=(f"\u672c\u6b21\u9700\u4e0b\u8f7d {download_size(plan['download_bytes'])}" if plan else "")
        if plan and plan["conflicts"]:
            summary+="\n\u68c0\u6d4b\u5230\u672c\u5730\u4fee\u6539\u51b2\u7a81\uff0c\u4e3a\u4fdd\u7559\u4f60\u7684\u4fee\u6539\uff0c\u5df2\u6682\u505c\u5b89\u88c5\u3002"
        if plan and plan.get("estimated"):summary="\u9884\u8ba1\u4e0b\u8f7d "+download_size(plan["download_bytes"])+"\uff08\u6b63\u5728\u540e\u53f0\u6838\u5bf9\u672c\u673a\u6587\u4ef6\uff09"
        self.plan_label.setText(summary)
        self.plan_label.setVisible(plan is not None)
        latest=p.latest_release if p else None
        key=(id(latest),self.view_mode.currentIndex())
        if key==self._notes_key:return
        self._notes_key=key
        if latest and latest.catalog:
            from modules.app_update_services.catalog import history_text
            notes=history_text(latest.catalog,p.checker.current_version,self.view_mode.currentIndex()==1,p.checker.resource_revision) or "\u6ca1\u6709\u5f85\u66f4\u65b0\u7684\u7248\u672c\uff0c\u53ef\u5207\u6362\u67e5\u770b\u6240\u6709\u5386\u53f2\u7248\u672c\u3002"
        else:notes=release.notes if release else "\u68c0\u67e5\u66f4\u65b0\u540e\uff0c\u8fd9\u91cc\u4f1a\u663e\u793a\u76f8\u5e94\u7684\u66f4\u65b0\u8bf4\u660e\u3002"
        if notes!=self.notes.toPlainText():self.notes.setPlainText(notes)


def build_app_update_page(window):
    """\u8bbe\u7f6e\u5bfc\u822a\u7684\u60f0\u6027\u6784\u5efa\u5165\u53e3\u3002"""
    page=AppUpdatePage(getattr(QApplication.instance(),"routermanager_app_updates",None),window.pages)
    window.app_update_page=page
    return page
