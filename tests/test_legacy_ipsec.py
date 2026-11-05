"""Offline legacy IPsec regressions: never change machine policies."""
import copy
import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch
from types import SimpleNamespace
import sys
from modules.platform_services.legacy_ipsec_plan import IPsecPlan,target_values,verify_dump
from modules.platform_services.legacy_ipsec_store import PolicySnapshot,preserved,read_snapshot,LOCAL
from modules.platform_services.legacy_ipsec import ensure_target
from modules.platform_services import windows_network_services as services
from modules.platform_services.firewall_session_worker import validate_request

ID='ipsecPolicy{12345678-1234-1234-1234-123456789abc}'
PLAN=IPsecPlan('Existing policy',ID,'192.168.1.1',22)

def dump(plan=PLAN):
    return '\n'.join(' '.join((a,b,*(x.split('=',1)[0]+'="'+x.split('=',1)[1]+'"' if ' ' in x else x for x in args)))
                     for a,b,*args in plan.additions())

class Store:
    def __init__(self,directory,fail=0):
        self.directory=directory;self.fail=fail;self.count=0;self.commands=[]
        self.nodes={'':{'activepolicy':(1,LOCAL+'\\'+ID)},ID:{'ipsecname':(1,PLAN.policy),'ipsecnfareference':(7,[])}}
    def snapshot(self):return PolicySnapshot(copy.deepcopy(self.nodes),ID,PLAN.policy)
    def dump(self):return dump()
    def backup(self):return self.directory,'fixture-hash'
    def mutate(self,args):
        self.commands.append(args);self.count+=1
        if self.count==self.fail:raise OSError('fixture command failure')
        op,kind,*items=args;options=dict(x.split('=',1) for x in items)
        if kind=='filter':return
        key={'filterlist':'ipsecFilter{fixture}','filteraction':'ipsecNegotiationPolicy{fixture}','rule':'ipsecNFA{fixture}'}[kind]
        if op=='add':
            self.nodes[key]={'ipsecname':(1,options['name'])}
            if kind=='rule':self.nodes[ID]['ipsecnfareference']=(7,[LOCAL+'\\'+key])
        else:
            self.nodes.pop(key,None)
            if kind=='rule':self.nodes[ID]['ipsecnfareference']=(7,[])

class PlanTests(unittest.TestCase):
    def test_exact_endpoint(self):
        self.assertTrue(verify_dump(PLAN,dump()))
        self.assertIn('dstport=22',PLAN.additions()[1])
        self.assertIn('mirrored=yes',PLAN.additions()[1])
        self.assertFalse(any('assign' in x for cmd in PLAN.additions() for x in cmd))
    def test_reject_broad_or_different_filters(self):
        for old,new in [('dstport=22','dstport=0'),('mirrored=yes','mirrored=no'),('srcaddr=me','srcaddr=any'),('255.255.255.255','255.255.255.0'),('action=permit','action=block')]:
            with self.subTest(new=new),self.assertRaises(ValueError):verify_dump(PLAN,dump().replace(old,new))
    def test_duplicates_rejected(self):
        with self.assertRaises(ValueError):verify_dump(PLAN,dump()+'\n'+dump())
    def test_invalid_targets(self):
        for address,port in [('example.com',22),('::1',22),('0.0.0.0',22),('224.0.0.1',22),('127.0.0.1',22),('192.168.1.1',True),('192.168.1.1',0),('192.168.1.1','22 & whoami')]:
            with self.subTest(address=address,port=port),self.assertRaises(ValueError):target_values(address,port)
    def test_request_boundary(self):
        request=dict(operation='ipsec_ssh_ensure',address='192.168.1.1',port=22)
        self.assertEqual(validate_request(request),request)
        for extra in [dict(policy='other'),dict(command='anything'),dict(backup='C:/x')]:
            with self.assertRaises(ValueError):validate_request(dict(request,**extra))
        with self.assertRaises(ValueError):validate_request(dict(request,port='22'))
    def test_token_distinguishes_ports(self):
        self.assertNotEqual(PLAN.token,IPsecPlan(PLAN.policy,ID,PLAN.address,2222).token)

class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Store(Path(self.temp.name))
    def test_success(self):
        result=ensure_target(PLAN.address,22,self.store)
        self.assertTrue(result['success']);self.assertEqual(result['status'],'appended_verified')
        self.assertEqual(len(self.store.commands),4)
        self.assertTrue((Path(self.temp.name)/'result.json').is_file())
    def test_repeat_does_not_add_again(self):
        ensure_target(PLAN.address,22,self.store)
        result=ensure_target(PLAN.address,22,self.store)
        self.assertEqual(result['status'],'existing_verified');self.assertEqual(len(self.store.commands),4)
    def test_failure_rolls_back_only_owned_objects(self):
        for fail in (1,2,3,4):
            with self.subTest(fail=fail):
                store=Store(Path(self.temp.name),fail)
                result=ensure_target(PLAN.address,22,store)
                self.assertFalse(result['success']);self.assertEqual(result['rollback'],'verified')
                self.assertEqual(set(store.nodes),{'',ID})
                self.assertFalse(any('import' in x or 'assign' in x for cmd in store.commands for x in cmd))
    def test_duplicate_policy_names_prevent_mutation(self):
        self.store.nodes['ipsecPolicy{another}']={'ipsecname':(1,PLAN.policy)}
        with self.assertRaises(ValueError):ensure_target(PLAN.address,22,self.store)
        self.assertEqual(self.store.commands,[])
    def test_backup_failure_prevents_mutation(self):
        with patch.object(self.store,'backup',side_effect=OSError('export failed')):
            with self.assertRaises(OSError):ensure_target(PLAN.address,22,self.store)
        self.assertEqual(self.store.commands,[])
    def test_no_policy_skips_all_mutation(self):
        with patch.object(self.store,'snapshot',return_value=PolicySnapshot({},'','')):
            self.assertEqual(ensure_target(PLAN.address,22,self.store)['status'],'no_assigned_policy')
        self.assertEqual(self.store.commands,[])
    def test_existing_unrelated_edits_block_rollback(self):
        original=self.store.mutate
        def mutate(args):
            original(args)
            if args[:2]==('add','filter'):
                self.store.nodes[ID]['unrelated']=(1,'changed')
                raise OSError('concurrent edit')
        with patch.object(self.store,'mutate',side_effect=mutate):result=ensure_target(PLAN.address,22,self.store)
        self.assertFalse(result['success']);self.assertIn('unconfirmed',result['rollback'])
        self.assertFalse(any(cmd[0]=='delete' for cmd in self.store.commands))
    def test_old_reference_cannot_be_removed(self):
        before=self.store.snapshot();self.store.nodes[ID]['ipsecnfareference']=(7,['old'])
        with self.assertRaises(ValueError):preserved(self.store.snapshot(),before,set(PLAN.names))
    def test_local_active_policy_pointer(self):
        rows=self.store.nodes
        with patch.dict(sys.modules,winreg=SimpleNamespace(REG_SZ=1)),patch('modules.platform_services.windows_network_services.domain_joined',return_value=False),patch('modules.platform_services.legacy_ipsec_store._tree',side_effect=lambda path,registry:rows if path==LOCAL else {}):
            self.assertEqual(read_snapshot().active_id,ID)
    def test_domain_machine_rejected(self):
        with patch.dict(sys.modules,winreg=SimpleNamespace(REG_SZ=1)),patch('modules.platform_services.windows_network_services.domain_joined',return_value=True):
            with self.assertRaises(PermissionError):read_snapshot()

class RouteTests(unittest.TestCase):
    def test_explicit_disabled_only(self):
        for state,start,expected in [(1,4,'ipsec'),(4,2,'firewall'),(1,2,'firewall'),(4,4,'firewall')]:
            with patch('modules.platform_services.runtime.is_windows',return_value=True),patch.object(services,'service_state',return_value=services.ServiceState('MpsSvc',state,start)):
                self.assertEqual(services.firewall_route(),expected)
    def test_cancel_before_authorization(self):
        from modules.platform_services.network_policy import prepare_target
        event=Event();event.set()
        with patch('modules.platform_services.firewall_session.request_firewall') as request:
            with self.assertRaises(InterruptedError):prepare_target(PLAN.address,22,event)
            request.assert_not_called()
    def test_normal_firewall_never_opens_ipsec_session(self):
        from modules.platform_services.network_policy import prepare_target
        with patch('modules.platform_services.network_policy.firewall_route',return_value='firewall'),patch('modules.platform_services.firewall_session.request_firewall') as request:
            self.assertIsNone(prepare_target(PLAN.address,22,Event()));request.assert_not_called()
    def test_authorization_failure_retains_native_code(self):
        from modules.platform_services import network_policy as policy
        states=services.ServiceState('fixture',4,2)
        with patch.object(policy,'firewall_route',return_value='ipsec'),patch.object(policy,'service_state',return_value=states),patch('modules.platform_services.firewall_session.request_firewall',return_value={'success':False,'code':1223,'error':'cancelled'}):
            with self.assertRaises(OSError):policy.prepare_target(PLAN.address,22,Event())
        self.assertEqual(policy.policy_snapshot()[-1]['native_error'],1223)
    def test_policy_diagnostics_bound_and_export_safe(self):
        from modules.platform_services import network_policy as policy
        from modules.capture_services.openwrt.operation_diagnostics import OperationDiagnostics,sanitized_operation_evidence
        for _ in range(12):policy._record(dict(route='ipsec',address='secret-address',error='secret-error',backup='secret-path',port=22,status='existing_verified',success=True))
        rows=policy.policy_snapshot()
        self.assertEqual(len(rows),8)
        self.assertNotIn('secret',str(rows))
        clean=policy.sanitized_policy_rows([dict(rows[0],password='secret')])
        self.assertNotIn('password',str(clean))
        evidence=OperationDiagnostics().snapshot()
        self.assertEqual(len(evidence['network_policy']),8)
        self.assertEqual(len(sanitized_operation_evidence(evidence,lambda value:value)['network_policy']),8)
    def test_missing_policy_agent_prevents_changes(self):
        states={'MpsSvc':services.ServiceState('MpsSvc',1,4),'BFE':services.ServiceState('BFE',4,2),'PolicyAgent':services.ServiceState('PolicyAgent',1,4)}
        with patch.object(services,'service_state',side_effect=states.get):
            with self.assertRaises(OSError):services.require_legacy_services()
    def test_service_query_failure_is_not_disabled(self):
        with patch('modules.platform_services.runtime.is_windows',return_value=True),patch.object(services,'service_state',side_effect=PermissionError('fixture')):
            with self.assertRaises(PermissionError):services.firewall_route()
    def test_disabled_startup_skips_firewall_apis(self):
        from modules.platform_services import startup_firewall as startup
        with patch.object(services,'firewall_route',return_value='ipsec'),patch.object(startup,'outbound_is_blocked') as com,patch.object(startup,'ensure_persistent_allow_rules') as ensure:
            self.assertTrue(startup.ensure_startup_firewall_rules());com.assert_not_called();ensure.assert_not_called()

if __name__=='__main__':unittest.main()
