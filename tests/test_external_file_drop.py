"""Native URL drops reuse file upload confirmation and async inspection."""
import stat
import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import Mock, patch
from PySide6.QtCore import Qt, QMimeData, QUrl, QPoint, QPointF
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QApplication
from tests import test_file_selection as fixture
from modules.platform_services.local_upload_selection import inspect_upload_paths
from modules.main_pages.packet_capture.openwrt.file_drop_actions import handle_file_drop


class ExternalFileDropTests(unittest.TestCase):
    setUp = fixture.FileSelectionTests.setUp
    tearDown = fixture.FileSelectionTests.tearDown
    theme = fixture.FileSelectionTests.theme

    def send_drop(self, view, urls, position=None):
        mime = QMimeData()
        mime.setUrls(urls)
        position = position or QPoint(view.viewport().width()-10, view.viewport().height()-10)
        enter = QDragEnterEvent(position, Qt.CopyAction|Qt.MoveAction, mime, Qt.LeftButton, Qt.NoModifier)
        QApplication.sendEvent(view.viewport(), enter)
        drop = QDropEvent(QPointF(position), Qt.CopyAction|Qt.MoveAction, mime, Qt.LeftButton, Qt.NoModifier)
        QApplication.sendEvent(view.viewport(), drop)
        return enter, drop

    def test_native_url_drop_in_list_and_icons_submits_copy_upload(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'file.txt'
            path.write_bytes(b'payload')
            for icons in (False, True):
                with self.subTest(icons=icons):
                    if icons:self.page._toggle_view(True)
                    fixture.APP.processEvents()
                    view = self.page._views[True][0].currentWidget()
                    enter, drop = self.send_drop(view, [QUrl.fromLocalFile(str(path))])
                    self.assertTrue(enter.isAccepted())
                    self.assertTrue(drop.isAccepted())
                    self.assertEqual(drop.dropAction(), Qt.CopyAction)
                    key, action, args = self.service.calls[-1]
                    self.assertEqual(action, 'local_upload_selection')
                    entries = inspect_upload_paths(args['paths'], Event())
                    with patch.object(self.page, '_transfer') as transfer:
                        self.service.completed.emit(key, entries)
                    transfer.assert_called_once_with(False, entries, destination='/root')
                    self.assertEqual(path.read_bytes(), b'payload')

    def test_reject_web_urls_local_pane_and_disconnected_router(self):
        for view, urls in ((self.page.remote_tree, [QUrl('https://example.org/file')]),
                           (self.page.remote_tree, [QUrl.fromLocalFile('C:/file'), QUrl('https://example.org/file')]),
                           (self.page.local_tree, [QUrl.fromLocalFile('C:/file')])):
            count = len(self.service.calls)
            enter, drop = self.send_drop(view, urls)
            self.assertFalse(enter.isAccepted())
            self.assertFalse(drop.isAccepted())
            self.assertEqual(len(self.service.calls), count)
        self.service.manager.ssh.connected = False
        enter, _ = self.send_drop(self.page.remote_tree, [QUrl.fromLocalFile('C:/file')])
        self.assertFalse(enter.isAccepted())

    def test_folder_destination_is_frozen_and_connection_change_discards_upload(self):
        item = self.page.remote_tree.topLevelItem(0)
        item.setData(0, Qt.UserRole, dict(name='target', path='/root/target', mode=stat.S_IFDIR|0o755))
        position = self.page.remote_tree.visualItemRect(item).center()
        _, drop = self.send_drop(self.page.remote_tree, [QUrl.fromLocalFile('C:/file')], position)
        self.assertTrue(drop.isAccepted())
        key = self.service.calls[-1][0]
        self.page._current_path[True] = '/tmp'
        with patch.object(self.page, '_transfer') as transfer:
            self.service.completed.emit(key, [dict(path='C:/file')])
        self.assertEqual(transfer.call_args.kwargs['destination'], '/root/target')
        self.send_drop(self.page.remote_tree, [QUrl.fromLocalFile('C:/file')])
        key = self.service.calls[-1][0]
        self.service.manager.ssh._client = object()
        with patch.object(self.page, '_transfer') as transfer:
            self.service.completed.emit(key, [])
        transfer.assert_not_called()

    def test_existing_file_still_requires_overwrite_confirmation(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'a.txt'
            path.write_bytes(b'new')
            self.send_drop(self.page.remote_tree, [QUrl.fromLocalFile(str(path))])
            key, _, args = self.service.calls[-1]
            entries = inspect_upload_paths(args['paths'], Event())
            with patch.object(self.page, '_confirm', return_value=False) as confirm, patch.object(self.page, '_submit') as submit:
                self.service.completed.emit(key, entries)
            confirm.assert_called_once()
            submit.assert_not_called()
            self.send_drop(self.page.remote_tree, [QUrl.fromLocalFile(str(path))])
            key = self.service.calls[-1][0]
            with patch.object(self.page, '_confirm', return_value=True), patch.object(self.page, '_submit', return_value=None) as submit:
                self.service.completed.emit(key, entries)
            self.assertEqual(submit.call_args.args, ('upload', 'transfer'))
            self.assertEqual(submit.call_args.kwargs['remote'], '/root/a.txt')
            self.assertIsNotNone(submit.call_args.kwargs['expected'])

    def test_internal_cross_pane_drag_keeps_transfer_route(self):
        entries = [dict(path='C:/file', mode=stat.S_IFREG)]
        with patch.object(self.page, '_transfer') as transfer:
            handle_file_drop(self.page, False, True, entries)
        transfer.assert_called_once_with(False, entries, destination=None)


class UploadInspectionTests(unittest.TestCase):
    def test_multiple_files_directory_and_duplicates(self):
        with tempfile.TemporaryDirectory() as folder:
            a = Path(folder)/'one.txt'
            b = Path(folder)/'sub'
            a.write_bytes(b'abc')
            b.mkdir()
            entries = inspect_upload_paths([str(a), str(b), str(a)], Event())
            self.assertEqual(len(entries), 2)
            self.assertEqual(entries[0]['size'], 3)
            self.assertTrue(stat.S_ISDIR(entries[1]['mode']))
            self.assertEqual(len(entries[1]['identity']), 5)
            with self.assertRaises(OSError):inspect_upload_paths([str(a/'missing')], Event())
        for paths in ([], ['relative.txt'], ['C:/'], ['C:/file']*10001):
            with self.subTest(paths=paths[:1]), self.assertRaises(ValueError):
                inspect_upload_paths(paths, Event())
        cancel = Event()
        cancel.set()
        with self.assertRaises(InterruptedError):inspect_upload_paths(['C:/file'], cancel)

    def test_reparse_point_is_rejected(self):
        info = Mock(st_mode=stat.S_IFDIR, st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
        with patch.object(Path, 'lstat', return_value=info), self.assertRaises(ValueError):
            inspect_upload_paths(['C:/junction'], Event())


class UploadWorkerQueueTests(unittest.TestCase):
    def test_inspection_uses_readonly_worker_and_completes_without_write_slot(self):
        from types import SimpleNamespace as NS
        from PySide6.QtCore import QObject, Signal
        from modules.capture_services.router_management_service import RouterManagementService

        class Owner(QObject):
            activityChanged = Signal()
            openwrtSnapshotChanged = Signal(object)

        owner = Owner()
        owner.is_busy = True
        owner._shutting_down = False
        owner.task_controller = Mock()
        owner._start_openwrt_worker = Mock()
        owner.openwrt_backend = NS(ssh_client=NS(connected=True, host_fingerprint='fp', _client=object()), command_runner=Mock())
        with patch('modules.capture_services.openwrt.compatible_tool.CompatibleToolInstaller'):
            service = RouterManagementService(owner)
        completed = Mock()
        service.completed.connect(completed)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'file'
            path.write_bytes(b'data')
            key = service.submit('local_upload_selection', paths=(str(path),))
            args = owner._start_openwrt_worker.call_args.args
            self.assertEqual(args[0], key)
            result = args[1](args[2], Mock())
            self.assertEqual(result[0]['size'], 4)
            args[3](result)
            completed.assert_called_once_with(key, result)
            self.assertIsNone(service.current)
            owner.task_controller.begin.assert_not_called()
            owner.task_controller.finish.assert_not_called()
            service.submit('local_upload_selection', paths=(str(path),))
            args = owner._start_openwrt_worker.call_args.args
            service.cancel(args[0])
            with self.assertRaises(InterruptedError):args[1](args[2], Mock())
        service.shutdown()
        owner.deleteLater()
        fixture.APP.processEvents()
