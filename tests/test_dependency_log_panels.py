"""Rendered dependency dock and real ZIP export isolation, without router commands."""
import json
from pathlib import Path
from dataclasses import replace
from tempfile import TemporaryDirectory
import unittest
import zipfile
from PySide6.QtCore import Qt
from tests import test_standalone as standalone
from modules.capture_services.dependency_logging import LOGIN_CATEGORY,DEPENDENCY_CATEGORY
from modules.capture_services.openwrt.models import OpenWrtConnectionState
from modules.display_privacy import Masker
from modules.log_services import DiagnosticReference,export_logs
APP=standalone.APP


class DependencyLogPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        standalone.StandaloneTests.setUpClass()
        cls.window=standalone.StandaloneTests.window
        cls.page=cls.window.packet_capture_page
        cls.service=cls.page.capture_service

    @classmethod
    def tearDownClass(cls):
        standalone.StandaloneTests.tearDownClass()

    def test_dependency_and_login_signals_reach_only_their_own_panel(self):
        login=self.page.settings_info_panel;dependency=self.page.dependency_info_panel
        login.clear();dependency.clear()
        self.service.settingsMessageGenerated.emit('LOGIN_EVENT','success')
        self.service.dependency_service._emit_dependency_message('INSTALL_EVENT','warning')
        APP.processEvents()
        self.assertIn('LOGIN_EVENT',login.info_box.toPlainText())
        self.assertNotIn('INSTALL_EVENT',login.info_box.toPlainText())
        self.assertIn('INSTALL_EVENT',dependency.info_box.toPlainText())
        self.assertNotIn('LOGIN_EVENT',dependency.info_box.toPlainText())
        from modules.capture_services.common_bundle_service import CommonBundleDownloadService
        bundle=CommonBundleDownloadService(self.service)
        bundle._stage('OFFLINE_EVENT','info')
        self.assertIn('OFFLINE_EVENT',dependency.info_box.toPlainText())
        self.assertNotIn('OFFLINE_EVENT',login.info_box.toPlainText())
        bundle.deleteLater()

    def test_zip_scopes_include_router_info_without_crossed_install_logs(self):
        login=self.page.settings_info_panel;dependency=self.page.dependency_info_panel
        original=self.service.openwrt_backend.snapshot
        device=replace(original.device,model='router-fixture',version='24.10.0')
        self.service.openwrt_backend.snapshot=replace(original,device=device,connection_state=OpenWrtConnectionState.CONNECTED)
        journal=self.service.openwrt_backend.command_runner.operation_diagnostics
        journal.record('workflow',operation='dependency_request',status='started',stage='install')
        journal.record('workflow',operation='configuration_apply',status='started',stage='apply')
        self.service.settingsMessageGenerated.emit('LOGIN_EXPORT_EVENT','info')
        self.service.dependencyMessageGenerated.emit('INSTALL_EXPORT_EVENT','info')
        try:
            with TemporaryDirectory() as tmp:
                root=Path(tmp);detail=root/'package-manager.log';detail.write_text('INSTALL_DETAIL_EVENT',encoding='utf-8')
                refs=self.service.dependency_service._diagnostics
                refs.append(DiagnosticReference(detail,root))
                try:
                    for panel,category in ((login,LOGIN_CATEGORY),(dependency,DEPENDENCY_CATEGORY)):
                        reference=panel._diagnostic_provider() if panel._diagnostic_provider else ()
                        archive=root/(category+'.zip')
                        export_logs(archive,category,panel.export_entries(),references=[(ref,1) for ref in reference],
                                    router_context=panel._export_context_provider(Masker()))
                        with zipfile.ZipFile(archive) as zipped:
                            text='\n'.join(zipped.read(name).decode('utf-8') for name in zipped.namelist())
                            context=json.loads(zipped.read('router-info.json'))
                        self.assertEqual(context['device']['model'],'router-fixture')
                        if category==LOGIN_CATEGORY:
                            self.assertIn('LOGIN_EXPORT_EVENT',text)
                            self.assertNotIn('INSTALL_EXPORT_EVENT',text)
                            self.assertNotIn('INSTALL_DETAIL_EVENT',text)
                            self.assertNotIn('dependency_request',text)
                        else:
                            self.assertIn('INSTALL_EXPORT_EVENT',text)
                            self.assertIn('INSTALL_DETAIL_EVENT',text)
                            self.assertNotIn('LOGIN_EXPORT_EVENT',text)
                            self.assertIn('dependency_request',text)
                            self.assertNotIn('configuration_apply',text)
                finally:refs.pop()
        finally:self.service.openwrt_backend.snapshot=original

    def test_login_cards_width_can_be_dragged(self):
        from PySide6.QtCore import QPoint
        from PySide6.QtTest import QTest
        login = self.page.openwrt_tab.login_page
        splitter = login.cards_splitter
        self.page.openwrt_tab.tabs.setCurrentIndex(0)
        for theme in ("dark.qss", "light.qss"):
            for scale in (100, 150):
                self.window.apply_appearance(scale, "default", theme)
                self.window.show(); APP.processEvents()
                self.window.resize(1480, 950); APP.processEvents()
                self.assertEqual(splitter.orientation(), Qt.Horizontal)
                self.assertFalse(splitter.childrenCollapsible())
                self.assertIs(splitter.widget(0), login.connection.card)
                self.assertIs(splitter.widget(1), login.device_card)
                splitter.setSizes([600, 600]); APP.processEvents()
                before = splitter.sizes()
                handle = splitter.handle(1)
                position = handle.rect().center()
                QTest.mousePress(handle, Qt.LeftButton, Qt.NoModifier, position)
                QTest.mouseMove(handle, position + QPoint(80, 0), 20)
                QTest.mouseRelease(handle, Qt.LeftButton, Qt.NoModifier, position + QPoint(80, 0))
                APP.processEvents()
                after = splitter.sizes()
                self.assertGreater(after[0], before[0], str((theme, scale, before, after)))
                self.assertGreater(min(after), 0)
                self.window.grab().save("build/dependency-log-preview/login-width-" + theme + "-" + str(scale) + ".png")
        self.window.apply_appearance(100, "default", "dark.qss")

    def test_optional_rows_last_and_installed_background(self):
        from modules.capture_services.openwrt.common_tool_dependencies import OPTIONAL_TOOL_KEYS
        from modules.capture_services.openwrt.models import DependencyInfo, DependencyState
        dep = self.page.openwrt_tab.dependency_page
        keys = list(dep.common_tools.rows)
        flags = [key == "terminal" or key in OPTIONAL_TOOL_KEYS for key in keys]
        self.assertEqual(flags, sorted(flags))
        original = self.service.openwrt_snapshot
        snapshot = replace(original, connection_state=OpenWrtConnectionState.CONNECTED,
            dependencies=(DependencyInfo("sftp", "sftp", DependencyState.READY),
                          DependencyInfo("scan", "scan", DependencyState.READY)))
        try:
            dep.apply_snapshot(snapshot)
            dep.common_tools.apply_snapshot(snapshot)
            self.assertTrue(dep.common_tools.row_widgets["sftp"].property("installed"))
            self.assertTrue(dep.dependencies.tool_buttons["scan"].parentWidget().property("installed"))
            self.assertFalse(bool(dep.common_tools.row_widgets["terminal"].property("installed")))
            dep.common_tools.card.set_expanded(True, animate=False)
            dep.dependencies.card.set_expanded(True, animate=False)
            self.page.openwrt_tab.tabs.setCurrentIndex(1)
            for theme in ("dark.qss", "light.qss"):
                self.window.apply_appearance(100, "default", theme)
                self.window.show(); APP.processEvents()
                self.window.grab().save("build/dependency-log-preview/installed-" + theme + ".png")
            dep.apply_snapshot(replace(snapshot, dependencies=()))
            dep.common_tools.apply_snapshot(replace(snapshot, dependencies=()))
            self.assertFalse(dep.common_tools.row_widgets["sftp"].property("installed"))
            self.assertFalse(dep.dependencies.tool_buttons["scan"].parentWidget().property("installed"))
        finally:
            dep.apply_snapshot(original)
            dep.common_tools.apply_snapshot(original)
            self.window.apply_appearance(100, "default", "dark.qss")

    def test_right_dock_themes_scaling_and_restore(self):
        folder=Path('build/dependency-log-preview');folder.mkdir(parents=True,exist_ok=True)
        container=self.page.openwrt_tab;container.tabs.setCurrentIndex(1)
        container.dependency_page.dependencies.card.set_expanded(True,animate=False)
        container.dependency_page.common_tools.card.set_expanded(True,animate=False)
        root=container.dependency_page.scroll_area.widget().layout()
        self.assertIs(root.itemAt(0).widget(),container.dependency_page.common_tools.card)
        self.assertEqual(container.dependency_page.common_tools.card.title_label.text(),'\u5e38\u7528\u5de5\u5177\u4f9d\u8d56')
        self.assertEqual(container.dependency_page.dependencies.card.title_label.text(),'\u6293\u5305\u4f9d\u8d56')
        dock=container.dependency_page.info_dock
        self.assertEqual(dock.orientation,Qt.Horizontal)
        self.assertIs(dock.panel,self.page.dependency_info_panel)
        for theme in ('dark.qss','light.qss'):
            for scale in (100,150):
                self.window.apply_appearance(scale,'default',theme)
                self.window.resize(1280,850);self.window.show()
                APP.processEvents()
                self.assertGreater(dock.panel.width(),100)
                self.assertGreater(dock.panel.x(),container.dependency_page.scroll_area.x())
                self.assertTrue(dock.panel.export_btn.isVisible())
                scroll=container.dependency_page.scroll_area
                buttons=container.dependency_page.dependencies.tool_buttons.values()
                for button in buttons:
                    point=button.mapTo(scroll.viewport(),button.rect().topRight())
                    if point.x() >= scroll.viewport().width():
                        bar = scroll.horizontalScrollBar()
                        self.assertGreater(bar.maximum(), 0)
                        bar.setValue(bar.maximum()); APP.processEvents()
                        point = button.mapTo(scroll.viewport(), button.rect().topRight())
                    self.assertLess(point.x(), scroll.viewport().width())
                scroll.horizontalScrollBar().setValue(0)
                dock.set_collapsed(True);APP.processEvents()
                self.assertTrue(dock.panel.isHidden())
                dock.set_collapsed(False);APP.processEvents()
                self.assertFalse(dock.panel.isHidden())
                self.window.grab().save(str(folder/(theme.replace('.qss','')+'-'+str(scale)+'.png')))
        self.window.apply_appearance(100,'default','dark.qss')
