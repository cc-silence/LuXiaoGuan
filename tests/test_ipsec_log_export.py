"""Exercise real support-ZIP export of host login-policy diagnostics only."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch
import unittest
import zipfile
from modules.log_history import LogHistory, current_history, install_history
from modules.log_services import export_logs
from modules.platform_services import network_policy as policy


class IPsecLogExportTests(unittest.TestCase):
    def test_login_failure_survives_current_and_history_zip_without_private_material(self):
        trace=[dict(operation='dump',outcome='completed',returncode=0,elapsed_ms=4,output_bytes=256)]
        failure=dict(success=False,status='failed',rollback='verified',completed_steps=4,
            error='fixture',commands=trace,
            verification=dict(reason='filter_endpoint',field='dstport',checked=2,rows=4))
        with patch.object(policy,'firewall_route',return_value='ipsec'), \
             patch.object(policy,'service_state',return_value=SimpleNamespace(state=4,start_type=2)), \
             patch('modules.platform_services.firewall_session.request_firewall',return_value=failure), \
             patch.object(policy.LOGGER,'error'):
            with self.assertRaises(OSError):policy.prepare_target('192.0.2.10',22,Event())
        row=policy.policy_snapshot()[-1]
        # Deliberately inject private fields at the final export boundary.
        row.update(address='192.0.2.10',error='PRIVATE_POLICY',backup='PRIVATE_BACKUP')
        row['commands'][0].update(arguments=['PRIVATE_PSK'],raw_output='PRIVATE_DUMP')
        context=json.dumps(dict(schema_version=1,device=None,
            operations=dict(schema_version=1,network_policy=[row])))
        previous=current_history()
        with TemporaryDirectory() as folder:
            history=LogHistory(Path(folder)/'history',version='fixture')
            install_history(history)
            try:
                history.context('router-login',context)
                output=Path(folder)/'feedback.zip'
                export_logs(output,'router-login',[],router_context=context)
                with zipfile.ZipFile(output) as archive:
                    self.assertIsNone(archive.testzip())
                    texts={name:archive.read(name).decode('utf-8') for name in archive.namelist()}
                for private in ('192.0.2.10','PRIVATE_POLICY','PRIVATE_BACKUP','PRIVATE_PSK','PRIVATE_DUMP'):
                    self.assertNotIn(private,'\n'.join(texts.values()))
                for name in ('operation-diagnostics.json','current-run/router-info.json'):
                    document=json.loads(texts[name])
                    exported=document.get('operations',document)['network_policy'][0]
                    self.assertEqual(exported['verification']['field'],'dstport')
                    self.assertEqual(exported['commands'],trace)
                    self.assertEqual(exported['rollback'],'verified')
                indexed={item['file'] for item in json.loads(texts['diagnostic-index.json'])['attachments']}
                self.assertIn('operation-diagnostics.json',indexed)
                self.assertIn('current-run/router-info.json',indexed)
            finally:
                install_history(previous)
                history.close()

    def test_cancelled_export_preserves_previous_feedback_file(self):
        with TemporaryDirectory() as folder:
            output=Path(folder)/'feedback.zip';output.write_bytes(b'previous-feedback')
            cancel=Event();cancel.set()
            with self.assertRaises(InterruptedError):
                export_logs(output,'router-login',[],cancel=cancel)
            self.assertEqual(output.read_bytes(),b'previous-feedback')
            self.assertEqual(list(Path(folder).iterdir()),[output])

if __name__=='__main__':unittest.main()
