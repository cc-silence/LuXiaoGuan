"""Regression coverage for identity collisions across persistence and re-export."""
from datetime import datetime,timezone
import json
from pathlib import Path
import re
import tempfile
import unittest
from modules.display_privacy import Masker,masker_for
from modules.log_history import LogHistory,install_history,HistoryMasker
from modules.log_services import sanitized_text,export_logs
from modules.capture_services.openwrt.operation_diagnostics import sanitized_operation_evidence
from modules.capture_services.openwrt.radio_diagnostics import sanitized_radio_evidence

STAMP='2026-10-30T06:48:55.695119+00:00'
CAPTURE='hcxmon1_20261030_144857.pcapng'

class DiagnosticLiteralTests(unittest.TestCase):
    def test_short_identities_do_not_destroy_generated_times_or_file_dates(self):
        for identity in ('202','2026','20261030','144857'):
            masker=masker_for([identity])
            for wrapped in (False,True):
                target=HistoryMasker() if wrapped else masker
                if wrapped:target.identities=masker
                source='time '+STAMP+'; file /root/cap/'+CAPTURE+'; identity '+identity
                result=sanitized_text(source,target)
                self.assertIn(STAMP,result)
                self.assertIn(CAPTURE,result)
                self.assertNotIn('identity '+identity,result)
                self.assertEqual(result,sanitized_text(result,target))

    def test_privacy_remains_effective_for_credentials_keys_addresses_and_exact_identities(self):
        masker=masker_for(['202',STAMP,CAPTURE])
        wrapper=HistoryMasker();wrapper.identities=masker
        for target in (masker,wrapper):
            self.assertNotIn(STAMP,sanitized_text(STAMP,target))
            self.assertNotIn(CAPTURE,sanitized_text(CAPTURE,target))
            self.assertNotIn(CAPTURE,sanitized_text('password='+CAPTURE,target))
            self.assertNotIn('secret-body',sanitized_text('-----BEGIN ' + 'PRIVATE KEY-----\nsecret-body\n-----END PRIVATE KEY-----',target))
            self.assertNotIn('AA:BB:CC:DD:EE:FF',sanitized_text('AA:BB:CC:DD:EE:FF',target))
        self.assertNotIn('10.0.0.9',sanitized_text('10.0.0.9',wrapper))
        self.assertNotIn(STAMP,sanitized_text(STAMP,Masker(overflow=True)))

    def test_invalid_dates_and_unrecognized_filenames_receive_no_exemption(self):
        masker=masker_for(['202'])
        for literal in ('2026-02-30T06:48:55+00:00','hcxmon1_20261330_144857.pcapng',
                        'user_network_20261030_144857.pcapng','20261030'):
            self.assertNotEqual(literal,sanitized_text(literal,masker))

    def test_all_structured_operation_and_radio_timestamps_remain_intact(self):
        masker=masker_for(['202',STAMP])
        row=dict(timestamp=STAMP,operation='sweep_stale',reason='network 202')
        source=dict(schema_version=1,**{key:[dict(row)] for key in
            ('events','failures','workflows','management','management_failures','configuration','configuration_failures')})
        result=sanitized_operation_evidence(source,masker)
        for key in source:
            if key=='schema_version':continue
            self.assertEqual(result[key][0]['timestamp'],STAMP)
            self.assertNotIn('202',result[key][0]['reason'])
        radio=sanitized_radio_evidence(dict(schema_version=1,attempts=[dict(started_at=STAMP,finished_at=STAMP)]),masker)
        self.assertEqual(radio['attempts'][0]['started_at'],STAMP)
        self.assertEqual(radio['attempts'][0]['finished_at'],STAMP)

    def test_full_disk_restart_and_zip_path_preserves_literals(self):
        import zipfile
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'history'
            first=LogHistory(path)
            first.masker.identities=masker_for(['202'])
            first.append('auto-capture',STAMP+' '+CAPTURE+' network 202',channel='radio1')
            first.context('auto-capture',json.dumps(dict(schema_version=1,
                captured_at=STAMP,operations=dict(schema_version=1,events=[dict(timestamp=STAMP)]))))
            first.close()
            second=LogHistory(path);install_history(second)
            try:
                output=Path(folder)/'out.zip'
                export_logs(output,'auto-capture',[],masker=masker_for(['202']))
                with zipfile.ZipFile(output) as archive:
                    text=archive.read('previous-run/radio1.txt').decode()
                    self.assertIn(STAMP,text)
                    self.assertIn(CAPTURE,text)
                    self.assertNotIn('network 202',text)
                    context=json.loads(archive.read('previous-run/router-info.json'))
                    self.assertEqual(context['operations']['events'][0]['timestamp'],STAMP)
                    for name in archive.namelist():
                        if name.endswith('.json'):json.loads(archive.read(name))
            finally:
                install_history(None);second.close()
