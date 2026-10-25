"""Audit gaps: operation identity, installer persistence, rollback and safe causes."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from collections import deque
import json
import logging
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile
from modules.app_update_services import diagnostics as d, installer
from modules.app_update_services.transaction import apply
from modules.app_update_services.diagnostics_export import export_update_logs
from modules.error_logs import ErrorLogStore
from tests.test_update_cache_lifecycle import fixture, PUBLIC
from modules.app_update_services.locations import CACHE_DIRECTORY

class UpdateDiagnosticAuditTests(unittest.TestCase):
    def setUp(self):
        self.events=patch.object(d,'_events',deque(maxlen=256));self.events.start()
        self.omitted=patch.object(d,'_omitted',0);self.omitted.start()
        self.logger=logging.getLogger('routermanager.update');self.old=self.logger.propagate
        self.logger.propagate=False;self.null=logging.NullHandler();self.logger.addHandler(self.null)

    def tearDown(self):
        self.events.stop();self.omitted.stop();self.logger.removeHandler(self.null);self.logger.propagate=self.old

    def test_operation_id_and_version_survive_long_file_sequence(self):
        d.begin_operation('download','1.0.12_alpha')
        d.record('download_started')
        for n in range(300):d.record('blob_verified',received=n)
        row=json.loads(d.snapshot()[0][0])
        self.assertEqual(row['operation_version'],'1.0.12_alpha')
        first=row['operation_id']
        d.begin_operation('check','1.0.11_alpha');d.record('check_started')
        current=json.loads(d.snapshot()[-2][0])
        self.assertNotEqual(current['operation_id'],first)

    def test_known_failure_code_does_not_disclose_path_suffix(self):
        text=d.exception_chain(ValueError('Staged file is missing or damaged: private-user-file'))
        self.assertIn('staged_file_damaged',text);self.assertNotIn('private-user-file',text)
        self.assertIn('errno=13',d.exception_chain(PermissionError(13,'private-path')))

    def test_startup_trace_keeps_missing_module_without_private_frames(self):
        evidence=d.startup_evidence(b'File "C:/private-user/source.py", line 1\nModuleNotFoundError: No module named \'missing_package\'')
        self.assertEqual(evidence['module'],'missing_package')
        self.assertNotIn('private-user',str(evidence))

    def test_route_id_compares_endpoints_without_hashing_credentials(self):
        self.assertEqual(d.route_identity('http://user:first@localhost:1081'),d.route_identity('http://user:second@localhost:1081'))
        self.assertNotEqual(d.route_identity('http://localhost:1081'),d.route_identity('http://localhost:1082'))
        self.assertEqual(d.route_identity(''),'direct')

    def test_only_valid_cloudflare_ray_is_recorded(self):
        self.assertEqual(d.response_trace(b'0123456789abcdef-HKG'),'0123456789abcdef-HKG')
        self.assertEqual(d.response_trace('password=private'),'')

    def test_persisted_context_cannot_exceed_error_record_size(self):
        row={'data':'x'*5000}
        text=d._encoded_context([row]*32)
        self.assertLessEqual(len(text.encode('utf-8')),49*1024)
        payload=json.loads(text[len(d.MARKER):])
        self.assertGreater(payload['context_records_omitted'],0)

    def test_independent_installer_restart_failure_is_exportable_after_restart(self):
        with TemporaryDirectory() as folder:
            root=Path(folder);stage=fixture(root,directory=CACHE_DIRECTORY)
            (root/'LuXiaoGuan.exe').write_bytes(b'application')
            job=stage/'job.json';job.write_text(json.dumps({'parent_pid':1}),encoding='utf-8')
            with patch.object(installer,'PUBLIC_KEY',PUBLIC),patch('modules.platform_services.app_update_install.wait_for_parent'),patch.object(installer,'apply'),patch.object(installer.subprocess,'Popen',side_effect=PermissionError(13,'private-path')):
                result=installer.run_install(job)
            self.assertEqual(result['status'],'failed')
            d._events.clear()
            store=ErrorLogStore(root/'RouterManagerData'/'error-logs')
            with patch('modules.error_log_hooks.current_error_logs',return_value=store):
                export_update_logs(root/'export.zip',d.snapshot())
            with zipfile.ZipFile(root/'export.zip') as archive:
                text=archive.read('information.txt').decode('utf-8')
                self.assertIn('restart_failed',text);self.assertIn('errno=13',text)
                self.assertNotIn('private-path',text)

    def test_automatic_rollback_keeps_original_validation_failure_stage(self):
        with TemporaryDirectory() as folder:
            root=Path(folder);stage=fixture(root,state=None,directory=CACHE_DIRECTORY)
            catalog=json.loads((stage/'catalog.json').read_text(encoding='utf-8'))
            d.begin_operation('install',revision=stage.name)
            def invalid():raise RuntimeError('private failure text')
            with patch('modules.app_update_services.transaction.migrate_defaults'),patch('modules.config_services.config_store.user_config_dir',return_value=root/'user'):
                with self.assertRaises(RuntimeError):apply(root,stage,catalog,invalid)
            rows=[json.loads(text) for text,level in d.snapshot()]
            failed=next(row for row in rows if row.get('event')=='apply_failed')
            self.assertEqual(failed['stage'],'validating')
            self.assertTrue(any(row.get('event')=='rollback_completed' for row in rows))
            self.assertNotIn('private failure text',str(d.snapshot()))

if __name__=='__main__':unittest.main()
