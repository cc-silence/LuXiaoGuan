import threading,unittest,json,copy
from dataclasses import asdict,replace
from unittest.mock import Mock
from tests.test_save_only import PersistenceDevice,LuaRuntime
from modules.capture_services.openwrt.wireless_setup_manager import WirelessSetupManager
from modules.capture_services.openwrt.wireless_setup_models import WirelessRequest
from modules.capture_services.openwrt.signal_manager import SignalError
from modules.capture_services.openwrt.operation_diagnostics import OperationDiagnostics
from modules.capture_services.openwrt.wireless_saved_diagnostics import FIELDS,saved_check_detail


@unittest.skipUnless(LuaRuntime,'Lua runtime unavailable')
class SavedVerificationEvidenceTests(unittest.TestCase):
    def test_mismatches_identify_fields_without_values_or_writes(self):
        device=PersistenceDevice();device.operation_diagnostics=OperationDiagnostics()
        manager=WirelessSetupManager(device);cancel=threading.Event()
        request=WirelessRequest('ap','radio7','private-ssid','private-password',options={'encryption':'psk2','network':'lan'})
        first=manager.save(request,cancel,Mock(),apply=False)
        base=copy.deepcopy(device.config)
        for field,changed in (
            ('iface.ssid',replace(request,ssid='another-private-ssid')),
            ('iface.key',replace(request,password='another-private-password')),
            ('iface.hidden',replace(request,options=dict(request.options,hidden='1'))),
            ('iface.network',replace(request,options=dict(request.options,network='wan'))),
            ('radio.country',replace(request,options=dict(request.options,country='US'))),
        ):
            with self.assertRaises(SignalError) as caught:
                manager._call('saved_status',cancel,values=asdict(changed),save_only=True,section=first['section'],network='lan')
            self.assertEqual(caught.exception.code,'unconfirmed')
            self.assertIn(field,str(caught.exception))
            self.assertNotIn('private-',str(caught.exception))
            self.assertEqual(device.config,base)
        evidence=device.operation_diagnostics.snapshot()
        self.assertNotIn('private-',json.dumps(evidence))
        self.assertTrue(any(row.get('verification_field')=='radio.country' for row in evidence['workflows']))
        row=evidence['saved_verification_failures'][-1]
        self.assertEqual(row['verification_actual_type'],'nil')
        self.assertEqual(row['verification_expected_type'],'string')

    def test_unknown_or_sensitive_response_field_is_not_logged(self):
        journal=Mock();runner=Mock(operation_diagnostics=journal)
        for value in ('password=private-secret',{},None,'iface.key.private-secret'):
            self.assertEqual(saved_check_detail({'verification':value},runner,'save_only_commit'),'')
        journal.record.assert_not_called()
