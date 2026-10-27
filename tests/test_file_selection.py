"""Native file selection, shared-theme rendering and multi-download regressions."""
import io
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import stat
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from threading import Event
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout
from PySide6.QtTest import QTest
from modules import resources_rc
from modules.ui_scale_manager import UiScaleManager
from modules.main_pages.packet_capture.capture_page_styles import CapturePageStyleManager
from modules.main_pages.base_page_styles import light_base_page_stylesheet, dark_base_page_stylesheet
from modules.main_pages.packet_capture.openwrt.router_files_page import RouterFilesPage
from modules.capture_services.router_file_batch import run_file_batch
from modules.capture_services.openwrt.router_management import RouterManagement

APP = QApplication.instance() or QApplication([])
from PySide6.QtGui import QFontDatabase, QFont
for font_name in ("msyh.ttc", "msyhbd.ttc", "consola.ttf"):
    font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / font_name
    if font_path.is_file():
        QFontDatabase.addApplicationFont(str(font_path))
APP.setFont(QFont("Microsoft YaHei", 10))


class Service(QObject):
    completed = Signal(str, object)
    failed = Signal(str, str)
    progress = Signal(str, object)
    def __init__(self):
        super().__init__()
        self.manager = NS(ssh=NS(connected=True, _client=object()))
        self.calls = []
        self.cancel = Mock()
    def submit(self, action, **args):
        key = str(len(self.calls))
        self.calls.append((key, action, args))
        return key


class FileSelectionTests(unittest.TestCase):
    def setUp(self):
        self._original_font = APP.font()
        APP.setFont(QFont("Microsoft YaHei", 10))
        self.service = Service()
        self.parent = QWidget()
        self.parent.scale_manager = UiScaleManager(100)
        self.parent.light_theme = True
        self.parent.setObjectName("packetCapturePage")
        self.layout = QVBoxLayout(self.parent)
        with patch("modules.main_pages.packet_capture.openwrt.router_files_page.MessageDialog.exec", return_value=1):
            self.page = RouterFilesPage(self.service, parent=self.parent)
        self.layout.addWidget(self.page)
        self.parent.resize(1280, 760)
        self.parent.show()
        self.parent.activateWindow()
        self.page._navigate(True, "/root")
        key = self.service.calls[-1][0]
        entries = [dict(name=name, path="/root/"+name, size=100, mode=stat.S_IFREG|0o644,
                        modified=100, identity=(stat.S_IFREG|0o644, 100, 100, 0, 0))
                   for name in ("a.txt", "b.txt", "c.txt", "d.txt")]
        self.service.completed.emit(key, dict(path="/root", entries=entries))
        APP.processEvents()

    def tearDown(self):
        self.page.dispose()
        self.parent.close()
        self.parent.deleteLater()
        APP.processEvents()
        APP.setFont(self._original_font)

    def theme(self, light):
        self.parent.light_theme = light
        styles = CapturePageStyleManager(self.parent)
        base = light_base_page_stylesheet("", self.parent.scale_manager) if light else dark_base_page_stylesheet(self.parent.scale_manager)
        self.parent.setStyleSheet(base + styles._capture_stylesheet(light))
        self.page.set_light_theme(light)
        APP.processEvents()

    def click_row(self, row, modifiers=Qt.NoModifier):
        tree = self.page.remote_tree
        point = tree.visualItemRect(tree.topLevelItem(row)).center()
        QTest.mouseClick(tree.viewport(), Qt.LeftButton, modifiers, point)
        APP.processEvents()

    def test_ctrl_shift_and_select_all_preserve_real_selection(self):
        self.theme(True)
        self.click_row(0)
        self.click_row(1, Qt.ControlModifier)
        self.assertEqual(len(self.page._selected_entries()), 2)
        self.click_row(3, Qt.ShiftModifier)
        self.assertGreaterEqual(len(self.page._selected_entries()), 3)
        QTest.keyClick(self.page.remote_tree, Qt.Key_A, Qt.ControlModifier)
        self.assertEqual(len(self.page._selected_entries()), 4)

    def test_both_themes_paint_selected_row_and_keep_inactive_selection(self):
        folder = Path(__file__).parent / "file-selection-preview"
        folder.mkdir(exist_ok=True)
        for light in (True, False):
            self.theme(light)
            buttons = (self.page.local_mkdir_button, self.page._remote_actions["mkdir"],
                       *self.page._delete_buttons.values())
            for button in buttons:
                self.assertEqual(button.text(), "")
                self.assertEqual(button.toolButtonStyle(), Qt.ToolButtonIconOnly)
                self.assertTrue(button.toolTip())
                self.assertTrue(button.accessibleName())
                self.assertFalse(button.icon().isNull())
            self.click_row(0)
            tree = self.page.remote_tree
            rect = tree.visualItemRect(tree.topLevelItem(0))
            x, y = tree.viewport().width()-12, rect.center().y()
            expected = "#b8d4f0" if light else "#26364c"
            image = tree.viewport().grab().toImage()
            self.parent.grab().save(str(folder/("light.png" if light else "dark.png")))
            self.assertEqual(image.pixelColor(x,y).name(), expected)
            self.page.local_path.setFocus()
            APP.processEvents()
            image = tree.viewport().grab().toImage()
            self.assertEqual(image.pixelColor(x,y).name(), expected)

    def test_icon_selection_is_shared_and_multiple_downloads_are_submitted(self):
        self.theme(True)
        self.page._toggle_view(True)
        icons = self.page._views[True][2]
        APP.processEvents()
        for row, modifiers in ((0, Qt.NoModifier), (1, Qt.ControlModifier)):
            index = icons.model().index(row, 0)
            QTest.mouseClick(icons.viewport(), Qt.LeftButton, modifiers, icons.visualRect(index).center())
            APP.processEvents()
        self.assertEqual(len(self.page._selected_entries()), 2)
        self.page._toggle_view(True)
        self.assertEqual(len(self.page.remote_tree.selectedItems()), 2)
        with tempfile.TemporaryDirectory() as folder:
            self.page._current_path[False] = folder
            with patch.object(self.page, "_submit_batch") as submit:
                self.page._transfer(True)
            jobs = submit.call_args.args[0]
            self.assertEqual(len(jobs), 2)
            self.assertTrue(all(job["operation"] == "download" for job in jobs))
            self.assertEqual({job["arguments"]["remote"] for job in jobs}, {"/root/a.txt", "/root/b.txt"})

    def test_delete_toolbar_submits_all_selected_entries_after_explicit_confirmation(self):
        self.click_row(0)
        self.click_row(1, Qt.ControlModifier)
        item = self.page.remote_tree.selectedItems()[0]
        entry = dict(item.data(0, Qt.UserRole))
        entry['mode'] = stat.S_IFDIR | 0o755
        item.setData(0, Qt.UserRole, entry)
        self.page._update_controls()
        button = self.page._delete_buttons[True]
        self.assertIn('2', button.toolTip())
        self.assertTrue(button.property('iconPath').endswith('file-browser-delete.svg'))
        with patch.object(self.page, '_confirm', return_value=True) as confirm, \
             patch.object(self.page, '_submit_batch') as submit:
            button.click()
            jobs, remote, title = submit.call_args.args
            self.assertEqual(len(jobs), 2)
            self.assertTrue(remote)
            self.assertTrue(all(job['operation'] == 'delete' for job in jobs))
            for job in jobs:
                self.assertIn(job['arguments']['path'], confirm.call_args.args[0])
            self.assertIn('全部子文件夹和文件', confirm.call_args.args[0])

    def test_single_remote_folder_uses_cancellable_batch_and_cancelled_confirmation_does_nothing(self):
        self.click_row(0)
        with patch.object(self.page, '_confirm', return_value=False), \
             patch.object(self.page, '_submit_batch') as submit:
            self.page._delete_buttons[True].click()
            submit.assert_not_called()
        with patch.object(self.page, '_confirm', return_value=True), \
             patch.object(self.page, '_submit_batch') as submit:
            self.page._delete_buttons[True].click()
            self.assertEqual(len(submit.call_args.args[0]), 1)

    def test_cancelled_delete_reports_partial_state_and_refreshes_directory(self):
        self.click_row(0)
        with patch.object(self.page, '_confirm', return_value=True):
            self.page._delete_buttons[True].click()
        key = self.service.calls[-1][0]
        dialog = self.page._transfers[key]
        with patch.object(dialog, 'finish') as finish, \
             patch.object(self.page, '_navigate') as refresh, \
             patch.object(self.page, '_notify') as notify:
            self.service.completed.emit(key, dict(results=[], count=1, error='', cancelled=True))
            self.assertFalse(finish.call_args.args[0])
            self.assertIn('部分删除', notify.call_args.args[0])
            self.assertEqual(notify.call_args.args[1], 'warning')
            refresh.assert_called_once()
        dialog.finish(True)

    def test_cancelled_local_batch_keeps_recycle_bin_recovery_message(self):
        self.page._submit_batch([dict(operation='local_delete', size=1, name='local', arguments={})], False, 'Delete')
        key = self.service.calls[-1][0]
        dialog = self.page._transfers[key]
        with patch.object(dialog, 'finish'), patch.object(self.page, '_navigate'), patch.object(self.page, '_notify') as notify:
            self.service.completed.emit(key, dict(results=[], count=1, error='', cancelled=True))
            self.assertIn('回收站恢复', notify.call_args.args[0])
            self.assertNotIn('不会恢复', notify.call_args.args[0])
        dialog.finish(True)

    def test_delete_confirmation_refuses_changed_connection_fingerprint_or_disconnect(self):
        self.click_row(0)
        ssh=self.service.manager.ssh
        old_client=ssh._client
        for attribute,new_value in (('_client',object()),('host_fingerprint','new-device'),('connected',False)):
            with self.subTest(attribute=attribute):
                ssh._client=old_client
                ssh.connected=True
                ssh.host_fingerprint='original-device'
                def confirm():
                    setattr(ssh,attribute,new_value)
                    return 1
                with patch('modules.main_pages.packet_capture.openwrt.router_files_page.ConfirmDialog') as dialog, \
                     patch.object(self.page,'_submit_batch') as submit, \
                     patch.object(self.page,'_notify') as notify:
                    dialog.Accepted=1
                    dialog.return_value.exec.side_effect=confirm
                    self.page._delete_entries(True)
                    submit.assert_not_called()
                    self.assertEqual(notify.call_args.args[1],'warning')
        ssh.connected=True
        ssh._client=old_client

    def test_name_column_still_fills_available_width_before_manual_resize(self):
        for light in (True, False):
            self.theme(light)
            self.parent.resize(self.parent.width() + 80, self.parent.height())
            for _ in range(12):
                APP.processEvents()
            for remote in (False, True):
                with self.subTest(light=light, remote=remote):
                    tree = self.page._views[remote][1]
                    other = sum(tree.columnWidth(column)
                                for column in range(1, tree.columnCount())
                                if not tree.isColumnHidden(column))
                    self.assertEqual(tree.columnWidth(0),
                                     max(200, tree.viewport().width() - other))

    def test_manual_name_width_survives_scrollbar_and_window_layout(self):
        from PySide6.QtCore import QPoint
        for light in (True, False):
            self.theme(light)
            for remote in (False, True):
                with self.subTest(light=light, remote=remote):
                    tree = self.page._views[remote][1]
                    header = tree.header()
                    tree.horizontalScrollBar().setValue(0)
                    APP.processEvents()
                    original = tree.columnWidth(0)
                    start = QPoint(header.sectionViewportPosition(0) + original - 1,
                                   header.height() // 2)
                    changes = []
                    def record(column, old, new):
                        if column == 0:
                            changes.append(new)
                    header.sectionResized.connect(record)
                    try:
                        QTest.mousePress(header.viewport(), Qt.LeftButton, Qt.NoModifier, start)
                        QTest.mouseMove(header.viewport(), start + QPoint(80, 0))
                        QTest.mouseRelease(header.viewport(), Qt.LeftButton, Qt.NoModifier,
                                           start + QPoint(80, 0))
                        for _ in range(12):
                            APP.processEvents()
                        self.assertEqual(tree.columnWidth(0), original + 80)
                        count = len(changes)
                        for _ in range(12):
                            APP.processEvents()
                        self.assertEqual(len(changes), count)
                        tree.fit_name_column()
                        self.parent.resize(self.parent.width() + 40, self.parent.height())
                        for _ in range(12):
                            APP.processEvents()
                        self.assertEqual(tree.columnWidth(0), original + 80)
                    finally:
                        header.sectionResized.disconnect(record)

    def test_batch_download_writes_both_files_through_real_executor(self):
        payloads = {"/a": b"one"*100, "/b": b"two"*200}
        sftp = Mock()
        sftp.lstat.side_effect = lambda name: NS(st_mode=stat.S_IFREG|0o644, st_size=len(payloads[name]), st_mtime=100, st_uid=0, st_gid=0)
        sftp.open.side_effect = lambda name, mode: io.BytesIO(payloads[name])
        ssh = NS(transfer=lambda *args, **kwargs: nullcontext(), open_sftp=lambda **kwargs:sftp)
        manager = RouterManagement(ssh, Mock())
        with tempfile.TemporaryDirectory() as folder:
            jobs = [dict(operation="download", name=name[1:], size=len(content),
                         arguments=dict(remote=name, local=str(Path(folder)/name[1:])))
                    for name, content in payloads.items()]
            result = run_file_batch(manager, jobs, Event(), Mock(), lambda: True)
            self.assertEqual(len(result["results"]), 2)
            self.assertFalse(result["error"])
            for name, content in payloads.items():
                self.assertEqual((Path(folder)/name[1:]).read_bytes(), content)
            self.assertEqual(sorted(p.name for p in Path(folder).iterdir()), ["a", "b"])
