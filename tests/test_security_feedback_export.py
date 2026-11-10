import json,unittest,zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from modules.log_history import LogHistory,current_history,install_history
from modules.log_services import export_logs
from modules.capture_services.openwrt.operation_diagnostics import OperationDiagnostics
from modules.capture_services.openwrt.wireless_saved_diagnostics import saved_check_detail
from modules.capture_services.openwrt.initial_security_evidence import record_initial_selection,ITEM_KEYS
from types import SimpleNamespace


class SecurityFeedbackExportTests(unittest.TestCase):
    def test_selection_and_failure_survive_refresh_and_actual_zip_export(self):
        journal=OperationDiagnostics();journal.bind_connection('fixture-router')
        review=dict(kind='initial',options={'items':['ssh','replace_ap','identity'],'ap_password':'PRIVATE_PASSWORD'},
                    operations=[{'config':'network','values':{'macaddr':'PRIVATE_MAC'}}],new_ap={'ssid':'PRIVATE_SSID'},files=[])
        with journal.management_scope(42,journal.epoch):record_initial_selection(journal,review)
        saved_check_detail({'verification':'iface.network','actual_type':'table','expected_type':'string'},SimpleNamespace(operation_diagnostics=journal),'save_only_commit')
        for _ in range(200):journal.record('workflow',operation='signal_refresh',status='completed',reason='verified')
        evidence=journal.snapshot()
        self.assertEqual(evidence['saved_verification_failures'][0]['verification_field'],'iface.network')
        self.assertEqual(evidence['initial_security'][0]['task_id'],42)
        evidence['initial_security'][0]['password']='PRIVATE_INJECTED'
        context=json.dumps(dict(schema_version=1,device=None,operations=evidence))
        previous=current_history()
        with TemporaryDirectory() as folder:
            history=LogHistory(Path(folder)/'history',version='fixture');install_history(history)
            try:
                for category in ('router-login','common-settings','signal-management'):
                    history.context(category,context)
                    output=Path(folder)/(category+'.zip')
                    export_logs(output,category,[],router_context=context)
                    with zipfile.ZipFile(output) as z:
                        self.assertIsNone(z.testzip())
                        for name in ('operation-diagnostics.json','router-info.json','current-run/router-info.json'):
                            raw=z.read(name).decode('utf-8');data=json.loads(raw).get('operations',json.loads(raw))
                            self.assertEqual(data['saved_verification_failures'][0]['verification_field'],'iface.network')
                            self.assertEqual(data['saved_verification_failures'][0]['verification_actual_type'],'table')
                            self.assertEqual(data['saved_verification_failures'][0]['verification_expected_type'],'string')
                            row=data['initial_security'][0]
                            self.assertTrue(row['initial_selected_ssh']);self.assertFalse(row['initial_selected_lan'])
                            self.assertTrue(row['initial_new_ap']);self.assertTrue(row['initial_config_network'])
                            self.assertNotIn('PRIVATE_',raw)
            finally:install_history(previous);history.close()
        journal.bind_connection('other-router')
        self.assertEqual(journal.snapshot()['saved_verification_failures'],[])
        self.assertEqual(journal.snapshot()['initial_security'],[])

    def test_real_policy_apply_records_after_validation_before_deferred_apply(self):
        from tests.test_router_policy import PolicyDevice
        from threading import Event
        from unittest.mock import patch
        device=PolicyDevice();journal=OperationDiagnostics()
        device.manager.runner.operation_diagnostics=journal
        review=dict(kind='initial',signature='fixture',options={'items':['lan']},
                    operations=[{'config':'network'}],files=[],new_ap=None,await_reconnect=True)
        with patch.object(device.policy,'read',return_value={'signature':'fixture'}), patch(
                'modules.capture_services.openwrt.initial_reconnect.apply_initial',return_value={'verified':True}) as apply:
            result=device.policy._apply_review(review,None,None,Event(),lambda _:None)
        self.assertTrue(result['verified']);apply.assert_called_once()
        self.assertTrue(journal.snapshot()['initial_security'][0]['initial_selected_lan'])

    def test_retention_is_bounded_and_option_catalog_matches(self):
        from modules.capture_services.openwrt.router_policy_plan import ITEMS
        self.assertEqual(set(ITEM_KEYS),set(ITEMS))
        journal=OperationDiagnostics()
        for _ in range(40):
            record_initial_selection(journal,dict(kind='initial',options={},operations=[]))
            saved_check_detail({'verification':'iface.ssid'},SimpleNamespace(operation_diagnostics=journal),'save_only_commit')
        self.assertEqual(len(journal.snapshot()['initial_security']),8)
        self.assertEqual(len(journal.snapshot()['saved_verification_failures']),16)
