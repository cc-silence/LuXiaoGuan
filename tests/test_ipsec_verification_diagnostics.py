"""Semantic failures retain safe reasons across decoding, rollback and log export."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from tests.test_legacy_ipsec import PLAN, Store, dump
from modules.platform_services.legacy_ipsec_plan import verify_dump
from modules.platform_services.legacy_ipsec import ensure_target
from modules.platform_services.ipsec_verification import IPsecVerificationError, safe_evidence
from modules.platform_services.network_policy import sanitized_policy_rows

class VerificationDiagnosticTests(unittest.TestCase):
    def test_semantic_failure_is_not_mislabeled_as_encoding(self):
        for encoding in ('utf-8','utf-16','utf-16-le','gbk'):
            with self.subTest(encoding=encoding),self.assertRaises(IPsecVerificationError) as failure:
                verify_dump(PLAN,dump().replace('dstport=22','dstport=0').encode(encoding))
            details=failure.exception.verification
            self.assertEqual(details['reason'],'filter_endpoint');self.assertEqual(details['field'],'dstport')
            self.assertGreater(details['decoded_candidates'],0)
            self.assertNotIn(PLAN.address,str(failure.exception));self.assertNotIn(PLAN.policy,str(failure.exception))

    def test_undecodable_and_unrecognized_output_are_distinct(self):
        for payload,reason in [(b'\xff\xfe\x00','output_decode'),(b'command returned no policy script','output_unrecognized')]:
            with self.assertRaises(IPsecVerificationError) as failure:verify_dump(PLAN,payload)
            self.assertEqual(failure.exception.verification['reason'],reason)

    def test_documented_disabled_tunnel_alias_is_accepted_but_real_tunnel_is_not(self):
        self.assertTrue(verify_dump(PLAN,dump()+' tunnel=no'))
        with self.assertRaises(IPsecVerificationError) as failure:verify_dump(PLAN,dump()+' tunnel=203.0.113.8')
        self.assertEqual(failure.exception.verification['reason'],'rule_tunnel')

    def test_documented_repeated_certificates_in_existing_policy_do_not_block_login(self):
        certificates=' rootca="CN=Authority A certmap:yes" rootca="CN=Authority B certmap:no" psk="private-key"'
        text=dump()+'\nadd rule name="unrelated" policy="other-policy"'+certificates
        self.assertTrue(verify_dump(PLAN,text.encode()))
        from modules.platform_services.legacy_ipsec_plan import dump_rows
        self.assertNotIn('rootca',dump_rows(text)[-1][1])
        self.assertNotIn('psk',dump_rows(text)[-1][1])
        with self.assertRaises(IPsecVerificationError):
            verify_dump(PLAN,text.replace('dstport=22','dstport=22 dstport=0'))

    def test_certificate_policy_supports_first_and_repeat_login_without_extra_rules(self):
        with TemporaryDirectory() as folder:
            store=Store(Path(folder))
            text=dump()+'\nadd rule name="unrelated" rootca="CN=A" rootca="CN=B"'
            with patch.object(store,'dump',return_value=text):
                first=ensure_target(PLAN.address,22,store)
                second=ensure_target(PLAN.address,22,store)
            self.assertEqual(first['status'],'appended_verified')
            self.assertEqual(second['status'],'existing_verified')
            self.assertEqual(len(store.commands),4)

    def test_wrong_policy_and_duplicate_options_remain_rejected(self):
        for text,reason in [(dump().replace(PLAN.policy,'different-policy'),'rule_association'),
                            (dump().replace('dstport=22','dstport=22 dstport=0'),'output_parse')]:
            with self.assertRaises(IPsecVerificationError) as failure:verify_dump(PLAN,text.encode())
            self.assertEqual(failure.exception.verification['reason'],reason)

    def test_failure_evidence_survives_rollback_and_protected_receipt(self):
        with TemporaryDirectory() as folder:
            store=Store(Path(folder))
            with patch.object(store,'dump',return_value=dump().replace('dstport=22','dstport=0').encode()):
                result=ensure_target(PLAN.address,22,store)
            self.assertFalse(result['success']);self.assertEqual(result['rollback'],'verified')
            self.assertEqual(result['verification']['field'],'dstport')
            saved=json.loads((Path(folder)/'result.json').read_text())
            self.assertEqual(saved['verification'],result['verification'])
            exported=sanitized_policy_rows([dict(result,backup_created=True)])[0]
            self.assertEqual(exported['verification']['reason'],'filter_endpoint')
            for private in (PLAN.address,PLAN.policy,folder):self.assertNotIn(private,json.dumps(exported))

    def test_login_retains_reason_in_export_and_shows_specific_failure(self):
        from threading import Event
        from types import SimpleNamespace
        from modules.platform_services import network_policy as policy
        result=dict(success=False,status='failed',route='ipsec',rollback='verified',completed_steps=4,
                    error='IPsec verification failed: filter_endpoint (dstport)',
                    verification=dict(reason='filter_endpoint',field='dstport',checked=2,rows=4))
        state=SimpleNamespace(state=4,start_type=2)
        with patch.object(policy,'firewall_route',return_value='ipsec'), \
             patch.object(policy,'service_state',return_value=state), \
             patch('modules.platform_services.firewall_session.request_firewall',return_value=result), \
             patch.object(policy.LOGGER,'error'):
            with self.assertRaises(OSError) as error:policy.prepare_target(PLAN.address,22,Event())
        self.assertIn('dstport',str(error.exception))
        self.assertNotIn('encodings',str(error.exception))
        exported=policy.policy_snapshot()[-1]
        self.assertEqual(exported['verification']['field'],'dstport')
        self.assertEqual(exported['rollback'],'verified')
        self.assertNotIn(PLAN.address,json.dumps(exported))

    def test_admin_worker_preserves_existing_rule_failure_evidence(self):
        from modules.platform_services import firewall_session_worker as worker
        error=IPsecVerificationError('rule_association',field='policy',checked=5,rows=4)
        sent=[]
        request=dict(operation='ipsec_ssh_ensure',address=PLAN.address,port=22)
        with patch.object(worker,'receive_json',side_effect=[request,EOFError()]), \
             patch.object(worker,'send_json',side_effect=lambda _connection,value:sent.append(value)), \
             patch('modules.platform_services.legacy_ipsec.apply_request',side_effect=error):
            self.assertEqual(worker.serve_connection(object(),apply=lambda request:None),0)
        self.assertFalse(sent[-1]['success'])
        self.assertEqual(sent[-1]['verification']['reason'],'rule_association')
        self.assertEqual(sent[-1]['verification']['field'],'policy')

    def test_diagnostics_accept_only_whitelisted_codes_fields_and_bounded_counts(self):
        value=dict(reason='filter_endpoint',field='dstport',checked=2,rows=4,decoded_candidates=2,
                   raw_dump='private policy',address=PLAN.address,secret='private')
        clean=safe_evidence(value)
        self.assertEqual(set(clean),{'reason','field','checked','rows','decoded_candidates'})
        for value in ({'reason':['bad']},{'reason':'private-policy-name'},None):self.assertEqual(safe_evidence(value),{})
        self.assertEqual(safe_evidence({'reason':'output_parse','field':[], 'rows':999999}),{'reason':'output_parse'})

if __name__=='__main__':unittest.main()
