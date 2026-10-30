"""Automatic recovery reports use the existing redacted ZIP export."""
import json,zipfile
from pathlib import Path,PurePosixPath
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
from unittest.mock import Mock,patch
import unittest
from tests.test_hcx_upgrade import APP
from modules.capture_services.openwrt.auto_capture_service import AutoCaptureService
from modules.capture_services.openwrt.workspace import CaptureWorkspace
from modules.log_services import export_logs
from modules.main_pages.packet_capture_page import PacketCapturePage


class AutoRecoveryExportTests(unittest.TestCase):
    def test_zip_contains_recovery_audit_but_not_journal_snapshot_or_packets(self):
        owner=Mock();service=AutoCaptureService(owner)
        with TemporaryDirectory() as folder:
            root=Path(folder)/'capture-work';directory=root/('a'*32);directory.mkdir(parents=True)
            (directory/'takeover-restore-audit.txt').write_text('restore check failed\npassword=do-not-share',encoding='utf-8')
            (directory/'takeover-journal.json').write_text(json.dumps({'config_token':'private-recovery-token'}),encoding='utf-8')
            (directory/'wireless-snapshot.txt').write_text('backup-private',encoding='utf-8')
            (directory/'capture.pcapng').write_bytes(b'private-packets')
            workspace=CaptureWorkspace('a'*32,directory,PurePosixPath('/tmp/owned'))
            service.record_recovery_diagnostic(workspace)
            service.record_recovery_diagnostic(workspace)
            refs=service.diagnostic_references();self.assertEqual(len(refs),1)
            destination=Path(folder)/'diagnostics.zip'
            with patch('modules.config_manager.CAPTURE_WORK_DIR',root):
                result=export_logs(destination,'capture',[],references=tuple((ref,1) for ref in refs))
            self.assertEqual(result['diagnostics_included'],1)
            with zipfile.ZipFile(destination) as archive:
                content='\n'.join(archive.read(name).decode('utf-8') for name in archive.namelist())
                self.assertIn('restore check failed',content)
                for secret in ('do-not-share','private-recovery-token','backup-private','private-packets'):
                    self.assertNotIn(secret,content)
        service.deleteLater()

    def test_reference_count_is_bounded(self):
        service=AutoCaptureService(Mock())
        for i in range(40):
            service.record_recovery_diagnostic(CaptureWorkspace(str(i),Path('/root')/str(i),PurePosixPath('/tmp/owned')))
        self.assertEqual(len(service.diagnostic_references()),32)
        service.deleteLater()

    def test_existing_info_panel_export_provider_includes_auto_recovery(self):
        ref=object()
        page=NS(light_theme=False,scale_manager=Mock(),_save_info_font_delta=Mock(),_router_export_context=Mock(),
                capture_service=NS(dependency_service=NS(diagnostic_references=lambda:()),
                                   auto_capture=NS(diagnostic_references=lambda:(ref,))))
        with patch('modules.main_pages.packet_capture_page.InfoPanel') as panel:
            PacketCapturePage._create_info_panel(page,0,'','')
            provider=panel.call_args.kwargs['diagnostic_references']
        self.assertEqual(provider(),(ref,))
