"""Second independent three-round IPsec review; all mutations use in-memory stores."""
import copy
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch,Mock
import unittest
from modules.platform_services import legacy_ipsec_store as registry
from modules.platform_services.legacy_ipsec_plan import IPsecPlan,target_values,verify_dump
from tests.test_legacy_ipsec import PLAN,ID,Store
from tests.test_legacy_ipsec_audit import independent_dump


class PolicyIdentificationTests(unittest.TestCase):
    def test_falsey_wrong_active_policy_types_are_not_no_policy(self):
        for kind,value in [(4,0),(7,[]),(3,{'base64':''})]:
            with self.subTest(kind=kind),patch.dict(sys.modules,winreg=SimpleNamespace(REG_SZ=1)),patch('modules.platform_services.windows_network_services.domain_joined',return_value=False),patch.object(registry,'_tree',side_effect=lambda path,r:{'':{'activepolicy':(kind,value)}} if path==registry.LOCAL else {}):
                with self.assertRaises(ValueError):registry.read_snapshot()
    def test_noncanonical_guid_rejected(self):
        with self.assertRaises(ValueError):IPsecPlan(PLAN.policy,'ipsecPolicy{1234567-81234-1234-1234-123456789abc}',PLAN.address,22)
    def test_existing_identifiers_with_different_case_verify(self):
        text=independent_dump()
        for name in PLAN.names:text=text.replace(name,name.lower())
        self.assertTrue(verify_dump(PLAN,text))
    def test_case_variant_extra_reference_is_detected(self):
        text=independent_dump()+f'\nadd rule name="other" policy="other" filterlist="{PLAN.names[0].lower()}" filteraction="different"\n'
        with self.assertRaises(ValueError):verify_dump(PLAN,text)

class BackupBoundaryTests(unittest.TestCase):
    def test_growing_backup_cannot_bypass_read_limit(self):
        import io
        from modules.platform_services import legacy_ipsec as policy
        with TemporaryDirectory() as folder:
            directory=Path(folder);(directory/'before.ipsec').write_bytes(b'old')
            with patch.object(policy,'windows_directory',return_value=directory):store=policy.NativeStore()
            stream=io.BytesIO(b'x'*65)
            with patch.object(policy,'BACKUP_LIMIT',64,create=True),patch.object(policy,'create_backup_directory',return_value=directory),patch.object(store,'mutate'),patch.object(Path,'open',return_value=stream):
                with self.assertRaises(OSError):store.backup()
    def test_changed_size_backup_is_not_used_as_recovery_evidence(self):
        import io
        from modules.platform_services import legacy_ipsec as policy
        with TemporaryDirectory() as folder:
            directory=Path(folder);(directory/'before.ipsec').write_bytes(b'old')
            with patch.object(policy,'windows_directory',return_value=directory):store=policy.NativeStore()
            with patch.object(policy,'create_backup_directory',return_value=directory),patch.object(store,'mutate'),patch.object(Path,'open',return_value=io.BytesIO(b'newer')):
                with self.assertRaises(OSError):store.backup()


class LoginIntegrationTests(unittest.TestCase):
    def service(self):
        from threading import Event
        from modules.capture_services.backends.openwrt_backend import OpenWrtCaptureBackend
        operations=[];event=Event()
        service=SimpleNamespace(_shutting_down=False,_openwrt_password='synthetic-secret',
            task_controller=SimpleNamespace(busy=False,begin=lambda *args:event),
            openwrt_backend=SimpleNamespace(validate_settings=OpenWrtCaptureBackend.validate_settings,connect=Mock()),
            network_prepare=Mock(),_on_openwrt_connect_succeeded=Mock(),_on_openwrt_connect_progress=Mock(),
            _start_openwrt_worker=lambda sid,operation,*args,**kwargs:operations.append(operation))
        return service,operations,event
    def test_invalid_login_does_not_prepare_computer_policy(self):
        from modules.capture_services.capture_service import CaptureService
        from modules.capture_services.backends.base import CaptureBackendError
        service,operations,event=self.service()
        CaptureService.connect_openwrt_async(service,dict(host=PLAN.address,port=22,user=''))
        with self.assertRaises(CaptureBackendError):operations[-1](event)
        service.network_prepare.assert_not_called();service.openwrt_backend.connect.assert_not_called()
    def test_cancelled_login_does_not_prepare_policy(self):
        from modules.capture_services.capture_service import CaptureService
        service,operations,event=self.service();service.openwrt_backend.disconnect=Mock();event.set()
        CaptureService.connect_openwrt_async(service,dict(host=PLAN.address,port=22,user='root'))
        self.assertEqual(operations[-1](event).status, 'cancelled')
        service.openwrt_backend.disconnect.assert_called_once()
        service.network_prepare.assert_not_called();service.openwrt_backend.connect.assert_not_called()
    def test_diagnostic_enum_type_does_not_break_export(self):
        from modules.platform_services.network_policy import sanitized_policy_rows
        self.assertEqual(sanitized_policy_rows([{'status':[],'rollback':{}}]),[{}])
    def test_closed_console_cannot_turn_verified_policy_into_failure(self):
        from threading import Event
        from modules.platform_services import network_policy as policy
        response=dict(success=True,status='existing_verified',route='ipsec',address=PLAN.address,port=22)
        state=SimpleNamespace(state=4,start_type=2)
        with patch.object(policy,'firewall_route',return_value='ipsec'),patch.object(policy,'service_state',return_value=state),patch('modules.platform_services.firewall_session.request_firewall',return_value=response),patch('builtins.print',side_effect=BrokenPipeError('closed console')):
            self.assertEqual(policy.prepare_target(PLAN.address,22,Event()),response)
    def test_success_response_must_match_requested_target(self):
        from threading import Event
        from modules.platform_services import network_policy as policy
        response=dict(success=True,status='existing_verified',route='ipsec',address='192.168.1.99',port=2222)
        state=SimpleNamespace(state=4,start_type=2)
        with patch.object(policy,'firewall_route',return_value='ipsec'),patch.object(policy,'service_state',return_value=state),patch('modules.platform_services.firewall_session.request_firewall',return_value=response):
            with self.assertRaises(ValueError):policy.prepare_target(PLAN.address,22,Event())


class DocumentationCrossCheckTests(unittest.TestCase):
    def test_system_directory_must_be_absolute_and_handles_drive_root(self):
        from modules.platform_services import legacy_ipsec_backup as backup
        for source,expected in [('C:',Path('C:\\')),('relative-path',None)]:
            def directory(buffer,size):
                buffer.value=source
                return len(source)
            kernel=SimpleNamespace(GetSystemWindowsDirectoryW=Mock(side_effect=directory))
            with self.subTest(source=source),patch.object(backup.ctypes,'WinDLL',return_value=kernel,create=True):
                if expected is None:
                    with self.assertRaises(OSError):backup.windows_directory()
                else:self.assertEqual(backup.windows_directory(),expected)
    def test_disconnected_client_does_not_interrupt_worker_transaction(self):
        from multiprocessing import Pipe
        from threading import Thread,Event
        from modules.platform_services import firewall_session_worker as worker
        from modules.platform_services.firewall_session_transport import send_json,receive_json
        parent,child=Pipe(duplex=True)
        entered=Event();release=Event();finished=[];outcome=[]
        def apply(request):
            entered.set()
            if not release.wait(3):raise TimeoutError('fixture did not release transaction')
            finished.append('transaction completed')
            return dict(success=True,status='appended_verified')
        def serve():
            try:outcome.append(worker.serve_connection(child,apply=lambda request:None))
            finally:child.close()
        thread=Thread(target=serve,daemon=True)
        try:
            with patch('modules.platform_services.legacy_ipsec.apply_request',side_effect=apply):
                thread.start()
                self.assertTrue(receive_json(parent,3)['ready'])
                send_json(parent,dict(operation='ipsec_ssh_ensure',address=PLAN.address,port=22))
                self.assertTrue(entered.wait(3))
                parent.close();release.set();thread.join(3)
                self.assertFalse(thread.is_alive())
                self.assertEqual(finished,['transaction completed'])
                self.assertEqual(outcome,[0])
        finally:
            parent.close();release.set()
            if thread.ident is not None:thread.join(3)
    def test_native_tool_path_uses_shared_system_windows_directory(self):
        from modules.platform_services import legacy_ipsec_backup as backup
        def system_directory(buffer,size):
            buffer.value='C:\\SystemWindows'
            return len(buffer.value)
        system=Mock(side_effect=system_directory)
        private=Mock(side_effect=AssertionError('Per-user Windows directory must not select privileged tools'))
        kernel=SimpleNamespace(GetSystemWindowsDirectoryW=system,GetWindowsDirectoryW=private)
        with patch.object(backup.ctypes,'WinDLL',return_value=kernel,create=True):
            result=backup.windows_directory()
        self.assertEqual(result,Path('C:\\SystemWindows'))
        private.assert_not_called();system.assert_called_once()


if __name__=='__main__':unittest.main()
