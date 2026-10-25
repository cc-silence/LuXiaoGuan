"""Update evidence, persistence, privacy and real shared async ZIP export."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from collections import deque
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
import json
import logging
import ssl
import unittest
from unittest.mock import patch
import zipfile
from PySide6.QtCore import QObject,QEventLoop,QTimer
from PySide6.QtWidgets import QApplication
from modules.app_update_services import diagnostics as d
from modules.app_update_services.diagnostics_export import export_update_logs
from modules.error_logs import ErrorLogStore, ErrorFileHandler
from modules.log_services import LogExportService
from modules.desktop_shell.app_update_log_export import UpdateLogExport

APP=QApplication.instance() or QApplication([])

class UpdateDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.events=patch.object(d,'_events',deque(maxlen=256));self.events.start()
        self.omitted=patch.object(d,'_omitted',0);self.omitted.start()
        self.logger=logging.getLogger('routermanager.update')
        self.propagate=self.logger.propagate;self.logger.propagate=False
        self.null=logging.NullHandler();self.logger.addHandler(self.null)

    def tearDown(self):
        self.logger.removeHandler(self.null);self.logger.propagate=self.propagate
        self.events.stop();self.omitted.stop()

    def test_private_values_never_enter_event_or_exception_details(self):
        error=ssl.SSLError(8,'private-testing-code proxy-password')
        d.record('request_finished',host=d.endpoint_host('https://user:password@example.com/manifest?code=private-testing-code'),
                 chain=d.exception_chain(error),test_code='private-testing-code',proxy_url='http://user:proxy-password@host',headers='Authorization: private')
        payload=str(d.snapshot())
        self.assertIn('example.com',payload);self.assertIn('SSLError',payload)
        for secret in ('private-testing-code','proxy-password','Authorization','https://user'):
            self.assertNotIn(secret,payload)

    def test_events_have_bounded_retention(self):
        for n in range(300):d.record('blob_verified',received=n)
        self.assertEqual(len(d._events),256)
        self.assertIn("session_records_omitted",d.snapshot()[-1][0])
        self.assertEqual(json.loads(d.snapshot()[0][0])['received'],44)

    def test_failed_route_survives_restart_and_export_excludes_other_logs(self):
        with TemporaryDirectory() as folder:
            root=Path(folder);store=ErrorLogStore(root/'logs')
            handler=ErrorFileHandler(store);self.logger.addHandler(handler)
            try:
                d.record('request_started',stage='manifest',route='direct',host='updates.example')
                d.record('check_failed',error='timeout')
                store.append('unrelated private text')
                d._events.clear()
                target=root/'export.zip'
                with patch('modules.error_log_hooks.current_error_logs',return_value=store):
                    export_update_logs(target,d.snapshot(),app_version='1.0.11_alpha')
                with zipfile.ZipFile(target) as archive:
                    text=archive.read('information.txt').decode('utf-8')
                    self.assertIn('check_failed',text);self.assertIn('updates.example',text)
                    self.assertNotIn('unrelated private text',text)
            finally:self.logger.removeHandler(handler)

    def test_export_cancel_keeps_existing_destination(self):
        with TemporaryDirectory() as folder:
            root=Path(folder);target=root/'export.zip';target.write_bytes(b'original')
            cancel=Event();cancel.set();store=ErrorLogStore(root/'logs')
            with patch('modules.error_log_hooks.current_error_logs',return_value=store):
                with self.assertRaises(InterruptedError):
                    export_update_logs(target,d.snapshot(),cancel=cancel)
            self.assertEqual(target.read_bytes(),b'original')

    def test_page_adapter_runs_shared_worker_and_cleans_connections(self):
        with TemporaryDirectory() as folder:
            root=Path(folder);runtime=QObject();runtime._exiting=False
            runtime.log_export_service=LogExportService(runtime)
            presenter=QObject();presenter.runtime=runtime
            presenter.checker=SimpleNamespace(current_version='1.0.11_alpha')
            controller=UpdateLogExport(presenter)
            store=ErrorLogStore(root/'logs');target=root/'export.zip'
            loop=QEventLoop();guard=QTimer();guard.setSingleShot(True);guard.timeout.connect(loop.quit)
            runtime.log_export_service.activityChanged.connect(lambda:loop.quit() if not runtime.log_export_service.active else None)
            with patch('modules.desktop_shell.app_update_log_export.QFileDialog.getSaveFileName',return_value=(str(target),'ZIP')),patch('modules.error_log_hooks.current_error_logs',return_value=store):
                controller.start(None)
                self.assertTrue(controller.active)
                guard.start(4000);loop.exec();guard.stop()
            self.assertFalse(runtime.log_export_service.active)
            self.assertFalse(controller.active);self.assertIsNone(controller._service)
            self.assertTrue(zipfile.is_zipfile(target))
            runtime.deleteLater();presenter.deleteLater();APP.processEvents()

    def test_rotated_history_does_not_prevent_current_session_export(self):
        with TemporaryDirectory() as folder:
            root=Path(folder);store=ErrorLogStore(root/'logs');gone=root/'logs'/'gone.log'
            d.record('request_started',host='updates.example')
            with patch('modules.error_log_hooks.current_error_logs',return_value=store),patch.object(store,'cleanup'),patch.object(store,'files',return_value=[(gone,SimpleNamespace(st_size=10))]):
                export_update_logs(root/'export.zip',d.snapshot())
            with zipfile.ZipFile(root/'export.zip') as archive:
                text=archive.read('information.txt').decode('utf-8')
                self.assertIn('history_files_skipped',text);self.assertIn('updates.example',text)

    def test_cancel_save_dialog_does_not_start_worker(self):
        runtime=QObject();runtime._exiting=False;runtime.log_export_service=LogExportService(runtime)
        presenter=QObject();presenter.runtime=runtime;presenter.checker=SimpleNamespace(current_version='1.0.11_alpha')
        controller=UpdateLogExport(presenter)
        with patch('modules.desktop_shell.app_update_log_export.QFileDialog.getSaveFileName',return_value=('','')):
            controller.start(None)
        self.assertFalse(runtime.log_export_service.active);self.assertFalse(controller.active)
        runtime.deleteLater();presenter.deleteLater();APP.processEvents()

if __name__=='__main__':unittest.main()
