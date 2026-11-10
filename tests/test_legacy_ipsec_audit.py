"""Independent regressions from the three-round IPsec implementation review."""
import copy
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from modules.platform_services import legacy_ipsec_store as registry
from modules.platform_services.legacy_ipsec_plan import IPsecPlan,verify_dump
from modules.platform_services.legacy_ipsec import ensure_target
from tests.test_legacy_ipsec import Store,PLAN,ID


def independent_dump(plan=PLAN):
    filters,action,rule=plan.names
    return f'''
# IPsec command-language fixture, hand-authored from command syntax, not a live dump.
pushd ipsec static
add filterlist name="{filters}" description="SSH endpoint"
add filter filterlist="{filters}" srcaddr=me dstaddr={plan.address} protocol=6 mirrored=YES srcmask=32 dstmask=32 srcport=0 dstport={plan.port}
add filteraction name="{action}" action=PERMIT
add rule name="{rule}" policy="{plan.policy}" filterlist="{filters}" filteraction="{action}" tunnel=0.0.0.0 conntype=ALL activate=YES kerberos=yes
popd
'''


class RoundOneTests(unittest.TestCase):
    def test_registry_key_case_is_supported(self):
        variant=IPsecPlan(PLAN.policy,ID.upper(),PLAN.address,22)
        self.assertEqual(variant.token,PLAN.token)
    def test_existing_name_case_collision_cannot_mutate_policy(self):
        with TemporaryDirectory() as folder:
            store=Store(Path(folder))
            store.nodes['ipsecFilter{existing}']={'ipsecname':(1,PLAN.names[0].lower())}
            with patch.object(store,'dump',return_value=''):
                with self.assertRaises(ValueError):ensure_target(PLAN.address,22,store)
            self.assertEqual(store.commands,[])
    def test_invalid_target_not_echoed_in_error(self):
        from modules.platform_services.legacy_ipsec_plan import target_values
        with self.assertRaises(ValueError) as error:target_values('private-input',22)
        self.assertNotIn('private-input',str(error.exception))
    def test_numeric_tcp_and_case_variants(self):
        self.assertTrue(verify_dump(PLAN,independent_dump()))
    def test_tunnel_endpoint_rejected(self):
        with self.assertRaises(ValueError):verify_dump(PLAN,independent_dump().replace('tunnel=0.0.0.0','tunnel=192.168.1.2'))
    def test_same_basename_different_reference_store_rejected(self):
        before=registry.PolicySnapshot({'':{},ID:{'ipsecname':(1,PLAN.policy),'ipsecnfareference':(7,[registry.LOCAL+'\\ipsecNFA{old}'])}},ID,PLAN.policy)
        nodes=copy.deepcopy(before.nodes)
        nodes[ID]['ipsecnfareference']=(7,[registry.BASE+'\\Policy\\Persistent\\ipsecNFA{old}'])
        after=registry.PolicySnapshot(nodes,ID,PLAN.policy)
        with self.assertRaises(ValueError):registry.preserved(before,after,set())
    def test_case_only_duplicate_policy_prevents_command(self):
        with TemporaryDirectory() as folder:
            store=Store(Path(folder))
            store.nodes['ipsecPolicy{other}']={'ipsecname':(1,PLAN.policy.upper())}
            with self.assertRaises(ValueError):ensure_target(PLAN.address,22,store)
            self.assertEqual(store.commands,[])
    def test_bad_registry_pointer_kind_rejected(self):
        rows={'':{'activepolicy':(2,registry.LOCAL+'\\'+ID)},ID:{'ipsecname':(1,PLAN.policy)}}
        with patch.dict(sys.modules,winreg=SimpleNamespace(REG_SZ=1)),patch('modules.platform_services.windows_network_services.domain_joined',return_value=False),patch.object(registry,'_tree',side_effect=lambda path,r:rows if path==registry.LOCAL else {}):
            with self.assertRaises(ValueError):registry.read_snapshot()
    def test_read_limit_also_bounds_values_inside_one_node(self):
        read=[]
        def value(key,index):
            read.append(index)
            return 'item'+str(index),'x'*(3*1024*1024),1
        fake=SimpleNamespace(QueryInfoKey=lambda key:(0,20,0),EnumValue=value)
        with self.assertRaises(ValueError):registry._values(object(),fake)
        self.assertEqual(len(read),3)
    def test_read_limit_checked_while_traversing(self):
        class Key:
            def __init__(self,path):self.path=path
            def __enter__(self):return self
            def __exit__(self,*args):pass
        fake=SimpleNamespace(HKEY_LOCAL_MACHINE=1,KEY_READ=1,KEY_WOW64_64KEY=2,
                             OpenKey=lambda h,path,*args:Key(path),
                             QueryInfoKey=lambda key:(9 if key.path=='root' else 0,0,0),
                             EnumKey=lambda key,index:'child'+str(index))
        with patch.object(registry,'_values',return_value={'data':(1,'x'*(3*1024*1024))}) as values:
            with self.assertRaises(ValueError):registry._tree('root',fake)
            self.assertEqual(values.call_count,3)

class RoundTwoTests(unittest.TestCase):
    def test_output_encodings_require_full_semantic_match(self):
        plan=IPsecPlan('Policy-\u6d4b\u8bd5',ID,PLAN.address,22)
        for encoding in ('utf-8','utf-8-sig','utf-16','utf-16-le','gbk'):
            with self.subTest(encoding=encoding),patch('modules.platform_services.legacy_ipsec_output.native_code_pages',return_value=['cp936','cp1252','cp437']):
                self.assertTrue(verify_dump(plan,independent_dump(plan).encode(encoding)))
        with self.assertRaises(ValueError):verify_dump(plan,independent_dump(plan).replace('dstport=22','dstport=0').encode('utf-16'))
    def test_receipt_failure_after_success_rolls_back(self):
        from modules.platform_services import legacy_ipsec as policy
        with TemporaryDirectory() as folder:
            store=Store(Path(folder));real=policy.write_result;calls=[]
            def save(path,result):
                calls.append(dict(result))
                if len(calls)==6:raise OSError('final receipt write failed')
                real(path,result)
            with patch.object(policy,'write_result',side_effect=save):result=ensure_target(PLAN.address,22,store)
            self.assertFalse(result['success']);self.assertEqual(result['rollback'],'verified')
            self.assertEqual(set(store.nodes),{'',ID})
    def test_unwritable_receipt_prevents_mutation_and_retains_backup(self):
        from modules.platform_services import legacy_ipsec as policy
        with TemporaryDirectory() as folder:
            store=Store(Path(folder))
            with patch.object(policy,'write_result',side_effect=OSError('disk full')):result=ensure_target(PLAN.address,22,store)
            self.assertFalse(result['success']);self.assertEqual(result['rollback'],'not_needed')
            self.assertIn('journal_error',result);self.assertTrue(result['backup'])
            self.assertEqual(store.commands,[])
    def test_partial_mutation_then_error_is_rolled_back(self):
        for fail in range(1,5):
            with self.subTest(fail=fail),TemporaryDirectory() as folder:
                store=Store(Path(folder));original=store.mutate
                def mutate(args):
                    original(args)
                    if store.count==fail:raise OSError('reply lost after mutation')
                with patch.object(store,'mutate',side_effect=mutate):result=ensure_target(PLAN.address,22,store)
                self.assertFalse(result['success']);self.assertEqual(result['rollback'],'verified')
                self.assertEqual(set(store.nodes),{'',ID})
    def test_atomic_receipt_keeps_previous_on_replace_failure(self):
        from modules.platform_services import legacy_ipsec_backup as backup
        with TemporaryDirectory() as folder:
            path=Path(folder)/'result.json';backup.write_result(path,{'before':True})
            old=path.read_bytes()
            with patch.object(backup.os,'replace',side_effect=OSError('fixture')):
                with self.assertRaises(OSError):backup.write_result(path,{'after':True})
            self.assertEqual(path.read_bytes(),old)
            self.assertEqual(list(Path(folder).iterdir()),[path])
    def test_native_runner_passes_real_bound_and_preserves_exit_code(self):
        import subprocess
        from modules.platform_services import legacy_ipsec as policy
        with patch.object(policy,'windows_directory',return_value=Path('system-root')):
            store=policy.NativeStore()
        with patch.object(policy,'capture_process',return_value=subprocess.CompletedProcess([],0,b'\xff')) as run:
            self.assertEqual(store.mutate(('add','filterlist','name=fixture')),b'\xff')
            self.assertEqual(run.call_args.kwargs['output_limit'],4*1024*1024)
            self.assertLessEqual(run.call_args.kwargs['timeout'],10)
        with patch.object(policy,'capture_process',return_value=subprocess.CompletedProcess([],87,b'secret output')):
            with self.assertRaises(OSError) as error:store.command(('dump',))
            self.assertEqual(error.exception.ipsec_exit_code,87)
            self.assertNotIn('secret',str(error.exception))
    def test_bounded_runner_reaps_noisy_child(self):
        import sys
        from modules.platform_services.process_capture import capture_process
        with self.assertRaises(ValueError):capture_process([sys.executable,'-c','import sys;sys.stdout.buffer.write(b"x"*262144)'],Event(),timeout=5,output_limit=1024)


class RoundThreeTests(unittest.TestCase):
    def test_cached_normal_audit_invalidated_after_service_roundtrip(self):
        from modules.platform_services import startup_firewall as startup
        with patch.object(startup,'_AUDIT_COMPLETE',True),patch.object(startup,'_AUDIT_ROUTE','firewall'),patch('modules.platform_services.windows_network_services.firewall_route',side_effect=['ipsec','firewall']),patch.object(startup,'startup_network_programs',return_value=[Path(sys.executable)]),patch.object(startup,'persistent_allow_rules_ready',return_value=False),patch.object(startup,'outbound_is_blocked',return_value=True),patch.object(startup,'ensure_persistent_allow_rules',return_value=['ok']) as ensure:
            self.assertEqual(startup.ensure_startup_firewall_rules().route,'ipsec')
            self.assertEqual(startup.ensure_startup_firewall_rules().route,'firewall')
            ensure.assert_called_once()
    def controller(self,route):
        from modules.desktop_shell.startup_firewall_controller import StartupFirewallController
        c=StartupFirewallController();c.applicable=True;c._completed=True;c._ready.set();c._audit_route=route
        return c
    def test_login_rechecks_firewall_after_disabled_startup(self):
        from modules.platform_services.startup_firewall import AuditMessages
        c=self.controller('ipsec')
        with patch('modules.platform_services.windows_network_services.firewall_route',return_value='firewall'),patch('modules.desktop_shell.startup_firewall_controller.ensure_startup_firewall_rules',return_value=AuditMessages(['ok'],'firewall')) as ensure,patch('modules.platform_services.network_policy.prepare_target') as prepare:
            repair=c.prepare_connection(PLAN.address,22,Event())
            ensure.assert_not_called();prepare.assert_not_called()
            repair()
            ensure.assert_called_once();prepare.assert_called_once();self.assertEqual(c._audit_route,'firewall')
    def test_same_mode_login_reuses_audit(self):
        c=self.controller('firewall')
        with patch('modules.platform_services.windows_network_services.firewall_route',return_value='firewall'),patch('modules.desktop_shell.startup_firewall_controller.ensure_startup_firewall_rules') as ensure,patch('modules.platform_services.network_policy.prepare_target'):
            c.prepare_connection(PLAN.address,22,Event());ensure.assert_not_called()
    def test_failed_reaudit_prevents_ssh_policy_preparation(self):
        c=self.controller('ipsec')
        with patch('modules.platform_services.windows_network_services.firewall_route',return_value='firewall'),patch('modules.desktop_shell.startup_firewall_controller.ensure_startup_firewall_rules',return_value=['\u5931\u8d25']),patch('modules.platform_services.network_policy.prepare_target') as prepare:
            with self.assertRaises(OSError):c.prepare_connection(PLAN.address,22,Event())()
            prepare.assert_not_called()
    def test_native_exit_code_and_receipt_error_reach_diagnostics(self):
        from modules.platform_services import network_policy as policy
        state=SimpleNamespace(state=4,start_type=2)
        response=dict(success=False,status='failed',native_error=87,journal_error='disk full',error='command failed',rollback='verified')
        with patch.object(policy,'firewall_route',return_value='ipsec'),patch.object(policy,'service_state',return_value=state),patch('modules.platform_services.firewall_session.request_firewall',return_value=response):
            with self.assertRaises(OSError):policy.prepare_target(PLAN.address,22,Event())
        self.assertEqual(policy.policy_snapshot()[-1]['native_error'],87)
    def test_nonwindows_route_does_not_query_services(self):
        from modules.platform_services import network_policy as policy
        from modules.platform_services import windows_network_services as services
        from modules.platform_services.legacy_ipsec_output import native_code_pages
        with patch('modules.platform_services.runtime.is_windows',return_value=False),patch.object(services,'service_state',side_effect=AssertionError('Windows API called')):
            self.assertIsNone(policy.prepare_target('router.invalid',22,Event()))
            self.assertEqual(native_code_pages(),[])
    def test_nonwindows_startup_does_not_call_windows_rules(self):
        from modules.platform_services import startup_firewall as startup
        with patch('modules.platform_services.runtime.is_windows',return_value=False),patch.object(startup,'persistent_allow_rules_ready') as ready,patch.object(startup,'outbound_is_blocked') as blocked:
            self.assertEqual(startup.ensure_startup_firewall_rules().route,'not_applicable')
            ready.assert_not_called();blocked.assert_not_called()
    def test_admin_selftest_rejects_ipsec_mutation(self):
        from modules.platform_services import firewall_session_worker as worker
        request=dict(operation='ipsec_ssh_ensure',address=PLAN.address,port=22)
        with patch.object(worker,'send_json') as send,patch.object(worker,'receive_json',side_effect=[request,EOFError()]),patch('modules.platform_services.legacy_ipsec.apply_request') as apply:
            self.assertEqual(worker.serve_connection(object(),self_test_only=True),0)
            apply.assert_not_called()
            self.assertFalse(send.call_args_list[-1].args[1]['success'])


if __name__=='__main__':unittest.main()
