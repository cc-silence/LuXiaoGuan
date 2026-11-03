"""Batch buttons continue independent work and retain original failure evidence."""
import tempfile
import threading
from types import SimpleNamespace as NS
from unittest.mock import Mock,patch
import unittest
from modules.capture_services.openwrt.dependency_manager import OpenWrtDependencyManager
from modules.capture_services.openwrt.signal_manager import SignalError
from modules.capture_services.openwrt.models import DependencyInfo,DependencyState

class PartialInstallTests(unittest.TestCase):
    def exercise(self,names,*,cancel=False,disconnect=False):
        runner=Mock();runner.ssh_client.connected=True;runner.ssh_client._client=object()
        manager=OpenWrtDependencyManager(runner);installed=set();event=threading.Event();calls=[]
        def check(*args,**kwargs):
            return tuple(DependencyInfo(n,n,DependencyState.READY if n in installed else DependencyState.MISSING,
                         () if n in installed else (n,)) for n in names)
        manager.check=Mock(side_effect=check)
        def local(_manager,batch,*args):
            calls.append(batch[0])
            if len(calls)==1:
                if cancel:event.set()
                if disconnect:runner.ssh_client.connected=False
                error=SignalError('environment','Package unavailable for this firmware')
                error.package_diagnostic='native error evidence'
                raise error
            installed.update(batch);return True
        with tempfile.TemporaryDirectory() as folder,patch(
                'modules.capture_services.openwrt.common_bundle_installer.install_from_local',side_effect=local):
            try:manager.install_all({},False,NS(),folder,event,Mock(),include_sftp=False)
            except Exception as error:return error,calls,installed
        self.fail('Expected partial failure')

    def test_capture_and_common_requests_continue_and_report_partial_failure(self):
        for names in [('aircrack-ng','reaver','iw'),('curl','libuci-lua','wpa-cli')]:
            error,calls,installed=self.exercise(names)
            self.assertEqual(error.code,'partial_install')
            self.assertEqual(calls,list(names));self.assertEqual(installed,set(names[1:]))
            self.assertIn(names[0],str(error));self.assertIn('native error evidence',error.package_diagnostic)

    def test_cancel_or_disconnect_stops_following_transactions(self):
        for options in [dict(cancel=True),dict(disconnect=True)]:
            error,calls,installed=self.exercise(('curl','wpa-cli'),**options)
            self.assertEqual(calls,['curl']);self.assertFalse(installed)
            self.assertNotEqual(getattr(error,'code',''),'partial_install')
