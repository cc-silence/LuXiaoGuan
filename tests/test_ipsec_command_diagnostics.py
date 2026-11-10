"""Command diagnostics use fake capture only; never modify host policies."""
from pathlib import Path
from subprocess import CompletedProcess
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch
import unittest
from modules.platform_services import legacy_ipsec as policy
from modules.platform_services.ipsec_command_diagnostics import safe_commands
from modules.platform_services.network_policy import sanitized_policy_rows
from tests.test_legacy_ipsec import PLAN, Store, dump


class CommandDiagnosticTests(unittest.TestCase):
    def native(self):
        with patch.object(policy,'windows_directory',return_value=Path('system-fixture')):
            return policy.NativeStore()

    def test_only_metadata_survives_capture_and_export(self):
        store=self.native()
        with patch.object(policy,'capture_process',return_value=CompletedProcess([],0,b'private-policy-PSK')):
            store.mutate(('add','rule','policy=private-policy','psk=private-key'))
        row=sanitized_policy_rows([dict(commands=store.command_diagnostics)])[0]['commands'][0]
        self.assertEqual(row['operation'],'add_rule');self.assertEqual(row['returncode'],0)
        self.assertEqual(row['output_bytes'],18)
        self.assertNotIn('private',str(row));self.assertNotIn('psk',str(row))

    def test_nonzero_exit_timeout_and_capture_error_remain_distinct(self):
        for response,expected,exception in [(CompletedProcess([],87,b'private'), 'failed',OSError),
                (TimeoutError('fixture'),'timed_out',TimeoutError),
                (OSError('fixture'),'capture_failed',OSError)]:
            with self.subTest(expected=expected):
                store=self.native()
                options={'side_effect':response} if isinstance(response,Exception) else {'return_value':response}
                with patch.object(policy,'capture_process',**options),self.assertRaises(exception):store.command(('dump',))
                self.assertEqual(store.command_diagnostics[-1]['outcome'],expected)
                if expected=='failed':self.assertEqual(store.command_diagnostics[-1]['returncode'],87)
                else:self.assertNotIn('returncode',store.command_diagnostics[-1])

    def test_expired_deadline_never_spawns_and_is_recorded(self):
        store=self.native();store.deadline=0
        with patch.object(policy,'capture_process') as capture,self.assertRaises(TimeoutError):store.command(('dump',))
        capture.assert_not_called();self.assertEqual(store.command_diagnostics[0]['outcome'],'timed_out')

    def test_trace_is_bounded_and_has_no_untrusted_fields(self):
        store=self.native()
        with patch.object(policy,'capture_process',return_value=CompletedProcess([],0,b'')):
            for _ in range(30):store.command(('dump',))
        self.assertEqual(len(store.command_diagnostics),12)
        valid=dict(operation='dump',outcome='completed',returncode=0,elapsed_ms=1,output_bytes=0,
                   raw_dump='secret',arguments=['secret'])
        self.assertEqual(set(safe_commands([valid])[0]),{'operation','outcome','returncode','elapsed_ms','output_bytes'})
        for invalid in ([valid]*13,[dict(operation=[],outcome={})],{'operation':'dump'}):
            self.assertEqual(safe_commands(invalid),[])
        self.assertNotIn('returncode',safe_commands([dict(valid,returncode=True)])[0])

    def test_prebackup_error_reaches_admin_ipc_with_exit_code(self):
        from modules.platform_services import firewall_session_worker as worker
        store=self.native();failure=OSError('fixture');failure.ipsec_exit_code=5
        trace=[dict(operation='exportpolicy',outcome='failed',returncode=5)]
        store.command_diagnostics=trace
        with patch.object(store,'snapshot',side_effect=failure):
            with self.assertRaises(OSError) as caught:policy.ensure_target(PLAN.address,22,store)
        sent=[]
        request=dict(operation='ipsec_ssh_ensure',address=PLAN.address,port=22)
        with patch.object(worker,'receive_json',side_effect=[request,EOFError()]), \
             patch.object(worker,'send_json',side_effect=lambda _c,value:sent.append(value)), \
             patch.object(policy,'apply_request',side_effect=caught.exception):
            worker.serve_connection(object(),apply=lambda request:None)
        self.assertEqual(sent[-1]['native_error'],5);self.assertEqual(sent[-1]['commands'],trace)

    def test_final_verification_and_rollback_keep_trace_for_next_export(self):
        with TemporaryDirectory() as folder:
            store=Store(Path(folder));store.command_diagnostics=[dict(operation='dump',outcome='completed',returncode=0)]
            with patch.object(store,'dump',return_value=dump().replace('dstport=22','dstport=0')):
                result=policy.ensure_target(PLAN.address,22,store)
            self.assertFalse(result['success']);self.assertEqual(result['rollback'],'verified')
            self.assertEqual(result['verification']['field'],'dstport')
            self.assertEqual(result['commands'],store.command_diagnostics)

    def test_login_export_keeps_command_trace(self):
        from modules.platform_services import network_policy as login
        trace=[dict(operation='dump',outcome='completed',returncode=0,output_bytes=128)]
        result=dict(success=False,error='fixture',commands=trace)
        with patch.object(login,'firewall_route',return_value='ipsec'), \
             patch.object(login,'service_state',return_value=SimpleNamespace(state=4,start_type=2)), \
             patch('modules.platform_services.firewall_session.request_firewall',return_value=result), \
             patch.object(login.LOGGER,'error'):
            with self.assertRaises(OSError):login.prepare_target(PLAN.address,22,Event())
        self.assertEqual(login.policy_snapshot()[-1]['commands'],trace)

if __name__=='__main__':unittest.main()
