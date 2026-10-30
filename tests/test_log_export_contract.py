"""Regression contracts from the real automatic-capture support ZIP."""
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from modules.display_privacy import Masker
from modules.log_history import LogHistory,install_history
from modules.log_services import build_export_filename,export_logs

class LogExportContractTests(unittest.TestCase):
    def test_all_log_names_include_local_username_and_auto_capture_is_chinese(self):
        with patch('modules.log_services.getpass.getuser',return_value='CC'):
            for category in ('application-update','router-terminal','page'):
                self.assertTrue(build_export_filename(category).startswith('CC_LuXiaoGuan_'+category+'_'))
            name=build_export_filename('auto-capture')
        self.assertTrue(name.startswith('CC_LuXiaoGuan_\u81ea\u52a8\u6293\u5305_'))
        self.assertNotIn('auto-capture',name)

    def test_username_is_safe_for_paths_and_has_failure_fallback(self):
        with patch('modules.log_services.getpass.getuser',return_value='..bad/user:*'):
            self.assertTrue(build_export_filename('page').startswith('bad_user__'))
        with patch('modules.log_services.getpass.getuser',side_effect=OSError()):
            self.assertTrue(build_export_filename('page').startswith('user_'))

    def test_structured_redaction_preserves_json_times_and_safe_auth_flags(self):
        with tempfile.TemporaryDirectory() as folder:
            store=LogHistory(Path(folder)/'history',version='test',clock=lambda:1793338634.)
            install_history(store)
            # A short network identity previously corrupted 2026 timestamps.
            masker=Masker(re.compile('202'))
            store.masker.identities=masker
            context=dict(schema_version=1,captured_at='2026-10-30T06:37:34+00:00',
                device=dict(model='router'),dependencies=[dict(feature='password_verify',state='ready',detail='available')],
                operations=dict(schema_version=1,login_attempts=[dict(status='completed',stage='complete',
                    reason='authenticated',auth_method='password',password_present=True,
                    password_boundary_whitespace=False,private_key_selected=False)]))
            try:
                store.context('auto-capture',json.dumps(context))
                store.append('auto-capture','password=credential-never-export\nnormal event',channel='radio1')
                output=Path(folder)/'result.zip'
                export_logs(output,'auto-capture',[],router_context=json.dumps(context),masker=masker)
                with zipfile.ZipFile(output) as archive:
                    payloads={name:archive.read(name).decode() for name in archive.namelist()}
                    for name,text in payloads.items():
                        if name.endswith('.json'):
                            json.loads(text)
                    current=json.loads(payloads['current-run/router-info.json'])
                    login=current['operations']['login_attempts'][0]
                    self.assertTrue(login['password_present'])
                    self.assertEqual(login['auth_method'],'password')
                    self.assertEqual(current['captured_at'],context['captured_at'])
                    self.assertEqual(current['dependencies'][0]['feature'],'password_verify')
                    self.assertIn('[2026-',payloads['current-run/radio1.txt'])
                    self.assertNotIn('credential-never-export',''.join(payloads.values()))
                    metadata=json.loads(payloads['run-history.json'])
                    self.assertTrue(metadata['runs'][0]['started'].startswith('2026-'))
                    index=json.loads(payloads['diagnostic-index.json'])
                    listed={row['file'] for row in index['attachments']}
                    self.assertIn('run-history.json',listed)
                    self.assertIn('current-run/radio1.txt',listed)
                    self.assertIn('current-run/router-info.json',listed)
                    self.assertIn('\u81ea\u52a8\u6293\u5305',payloads['README.txt'])
            finally:
                install_history(None)
                store.close()

if __name__=='__main__':unittest.main()
