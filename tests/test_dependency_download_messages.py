"""Concise rendered download messages retain complete redacted ZIP evidence."""
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from tests.test_standalone import APP
from modules.main_pages.info_panel import InfoPanel
from modules.capture_services.openwrt import signal_package_feed as feed
from modules.capture_services.openwrt.package_download_diagnostics import AttemptDiagnostics
from modules.capture_services.dependency_service import OpenWrtDependencyService,DependencyTask
from modules.capture_services.openwrt.installation_sources import install_with_sources
from modules.capture_services.openwrt.signal_manager import SignalError
from modules.log_services import export_logs


class DownloadMessageTests(unittest.TestCase):
    def panel(self):
        panel=InfoPanel();self.addCleanup(panel.deleteLater);return panel

    def test_success_is_short_but_export_keeps_route_and_byte_evidence(self):
        panel=self.panel();response=Mock(status=200,headers={'Content-Length':'8'})
        response.read1.side_effect=[b'verified',b'']
        opener=Mock();opener.open.return_value.__enter__=Mock(return_value=response);opener.open.return_value.__exit__=Mock(return_value=False)
        with feed.download_session(panel.append_info), \
             patch.object(feed,'_attempt_plans',return_value=[('socks5h://alice:password123@localhost:1080',object())]), \
             patch.object(feed.urllib.request,'proxy_bypass',return_value=False), \
             patch.object(feed.urllib.request,'build_opener',return_value=opener):
            self.assertEqual(feed.fetch_bytes('https://fixture.invalid/private?token=hidden'),b'verified')
        visible=panel.info_box.toPlainText()
        self.assertEqual(visible,'\u6b63\u5728\u901a\u8fc7 SOCKS \u4ee3\u7406 \u4e0b\u8f7d\u2026\n\u4e0b\u8f7d\u5b8c\u6210\uff08SOCKS \u4ee3\u7406\uff09\u3002')
        panel._refresh_privacy();self.assertEqual(panel.info_box.toPlainText(),visible)
        panel.set_light_theme(True);self.assertEqual(panel.info_box.toPlainText(),visible)
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/'logs.zip';export_logs(output,'dependency',panel.export_entries())
            with zipfile.ZipFile(output) as archive:text=archive.read('information.txt').decode('utf-8')
        for field in ('SOCKS','HTTP 200','\u5df2\u6536 8','\u8017\u65f6','\u9636\u6bb5'):
            self.assertIn(field,text)
        for secret in ('alice','password123','token=hidden'):self.assertNotIn(secret,text)
        out=Path('build/download-message-preview');out.mkdir(parents=True,exist_ok=True)
        for light in (False,True):
            panel.set_light_theme(light);panel.resize(660,220);panel.show();APP.processEvents()
            self.assertTrue(panel.grab().save(str(out/('light.png' if light else 'dark.png'))))

    def test_details_stay_hidden_when_storage_is_trimmed_and_cleared(self):
        panel=self.panel()
        for _ in range(2001):panel.append_info('technical detail','diagnostic')
        panel.append_info('visible')
        self.assertEqual(len(panel.export_entries()),2000)
        self.assertEqual(panel.info_box.toPlainText(),'visible')
        panel.clear();panel._refresh_privacy();self.assertFalse(panel.export_entries());self.assertEqual(panel.info_box.toPlainText(),'')

    def test_bypass_summary_identifies_direct_instead_of_proxy(self):
        diagnostic=AttemptDiagnostics('https://fixture','SOCKS \u4ee3\u7406\uff08localhost:1080\uff09',5,0,True)
        self.assertIn('\u76f4\u8fde',diagnostic.short_result())
        self.assertNotIn('SOCKS',diagnostic.short_result())
        self.assertIn('SOCKS',diagnostic.result())

    def test_diagnostic_message_does_not_replace_task_progress(self):
        task=DependencyTask('session','terminal',('tmux',),'install','visible')
        service=SimpleNamespace(owner=SimpleNamespace(settingsMessageGenerated=Mock()),task=task,taskChanged=Mock())
        OpenWrtDependencyService._emit_settings_message(service,'technical detail','diagnostic')
        service.owner.settingsMessageGenerated.emit.assert_called_once_with('technical detail','diagnostic')
        self.assertIs(service.task,task);service.taskChanged.emit.assert_not_called()
        OpenWrtDependencyService._emit_settings_message(service,'visible update','info')
        self.assertEqual(service.task.message,'visible update')

    def test_final_installation_route_does_not_repeat_previous_failure(self):
        def offline():raise SignalError('offline_retry','fixture')
        self.assertEqual(install_with_sources(lambda:False,offline,lambda:None,Mock()),'\u7535\u8111\u4e0b\u8f7d\u540e\u4f20\u5230\u8def\u7531\u5668\u5b89\u88c5')


if __name__=='__main__':unittest.main()
