import io
import tarfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from modules.capture_services.openwrt.curl_repair import candidate_url,extract_candidate,verify_plan,repair
from modules.capture_services.openwrt.signal_manager import SignalError


def archive(entries):
    out=io.BytesIO()
    with tarfile.open(fileobj=out,mode='w:gz') as t:
        for name,data in entries.items():
            info=tarfile.TarInfo(name);info.size=len(data);t.addfile(info,io.BytesIO(data))
    return out.getvalue()


def package(version='8.10.1-r1',arch='mipsel_24kc'):
    control=('Package: curl\nVersion: '+version+'\nArchitecture: '+arch+'\n').encode()
    return archive({'./control.tar.gz':archive({'./control':control}), './data.tar.gz':archive({'./usr/bin/curl':b'executable'})})


class CurlRepairTests(unittest.TestCase):
    def setUp(self):
        self.device=SimpleNamespace(version='24.10.0',distribution='OpenWrt',package_manager='opkg',package_architecture='mipsel_24kc',sftp_available=True)
    def test_url_and_package_metadata(self):
        self.assertIn('/24.10.0/packages/mipsel_24kc/',candidate_url(self.device,'8.10.1-r1')[1])
        self.assertEqual(extract_candidate(package(),'8.10.1-r1','mipsel_24kc'),b'executable')
        with self.assertRaises(ValueError):extract_candidate(package(),'8.10.1-r2','mipsel_24kc')
        with self.assertRaises(ValueError):candidate_url(self.device,'../bad')
    def test_plan_rejects_other_changes(self):
        verify_plan('Downgrading curl on root from 8.22.0-r1 to 8.10.1-r1...\nConfiguring curl.','8.10.1-r1')
        verify_plan('Installing curl (8.10.1-r1) to root...','8.10.1-r1',installed=False)
        for plan in ('Downgrading curl on root from 8.22.0-r1 to 8.10.1-r1...\nUpgrading libcurl4 on root from 1 to 2...', 'Package curl is already installed on root.', 'Installing other (1) to root...'):
            with self.subTest(plan=plan),self.assertRaises(ValueError):verify_plan(plan,'8.10.1-r1')
    def test_repair_checks_candidate_and_previews_before_install(self):
        m=Mock();m.command_runner=Mock();m._preflight.return_value=SimpleNamespace(name='opkg');m._package_uploader.return_value=Mock()
        def execute(stage,*args):
            if stage=='curl_repair_trial':return 'curl 8.10.1 (test)\nProtocols: http https\n'
            if stage=='curl_repair_plan':return 'Downgrading curl on root from 8.22.0-r1 to 8.10.1-r1...\n'
            return ''
        m._execute_stage.side_effect=execute
        with patch('modules.capture_services.openwrt.curl_version_diagnostics.query_versions',return_value={'curl':'8.22.0-r1','libcurl4':'8.10.1-r1'}), patch('modules.capture_services.openwrt.package_manager.compare_opkg_versions',return_value=True), patch('modules.capture_services.openwrt.signal_package_feed.fetch_bytes',return_value=package()),patch('modules.capture_services.openwrt.offline_cleanup.cleanup_remote'):
            repair(m,self.device,None,Mock())
        names=[x.args[0] for x in m._execute_stage.call_args_list]
        self.assertLess(names.index('curl_repair_trial'),names.index('curl_repair_plan'))
        self.assertLess(names.index('curl_repair_plan'),names.index('curl_repair_install'))
    def test_local_candidate_avoids_network_and_keeps_trial(self):
        m=Mock();m.command_runner=Mock();m._preflight.return_value=SimpleNamespace(name='opkg');m._package_uploader.return_value=Mock()
        def execute(stage,*args):
            if stage=='curl_repair_trial': return 'curl 8.10.1 x\nProtocols: https\n'
            if stage=='curl_repair_plan': return 'Installing curl (8.10.1-r1) to root...'
            return ''
        m._execute_stage.side_effect=execute
        with patch('modules.capture_services.openwrt.curl_version_diagnostics.query_versions',return_value={'libcurl4':'8.10.1-r1'}), patch('modules.capture_services.openwrt.curl_repair._local_candidate',return_value=package()), patch('modules.capture_services.openwrt.signal_package_feed.fetch_bytes') as fetch, patch('modules.capture_services.openwrt.offline_cleanup.cleanup_remote'):
            repair(m,self.device,None,Mock())
        fetch.assert_not_called()
        self.assertIn('curl_repair_trial',[x.args[0] for x in m._execute_stage.call_args_list])

    def test_missing_archive_is_recoverable_and_local_can_be_disabled(self):
        from modules.capture_services.openwrt.signal_package_feed import PackageDownloadUnavailable
        from modules.capture_services.openwrt.dependency_http import DownloadHTTPError
        m=Mock();m.command_runner=Mock()
        with patch('modules.capture_services.openwrt.curl_version_diagnostics.query_versions',return_value={'libcurl4':'8.10.1-r1'}), patch('modules.capture_services.openwrt.curl_repair._local_candidate') as local, patch('modules.capture_services.openwrt.signal_package_feed.fetch_bytes',side_effect=DownloadHTTPError(404)):
            with self.assertRaises(PackageDownloadUnavailable):
                repair(m,self.device,None,Mock(),allow_local_bundle=False)
        local.assert_not_called()

    def test_unsafe_plan_never_installs(self):
        m=Mock();m.command_runner=Mock();m._preflight.return_value=SimpleNamespace(name='opkg');m._package_uploader.return_value=Mock()
        m._execute_stage.side_effect=lambda stage,*args: 'curl 8.10.1 x\nProtocols: https\n' if stage=='curl_repair_trial' else 'Upgrading libcurl4 on root from 1 to 2...' if stage=='curl_repair_plan' else ''
        with patch('modules.capture_services.openwrt.curl_version_diagnostics.query_versions',return_value={'curl':'8.22.0-r1','libcurl4':'8.10.1-r1'}),patch('modules.capture_services.openwrt.package_manager.compare_opkg_versions',return_value=True),patch('modules.capture_services.openwrt.signal_package_feed.fetch_bytes',return_value=package()),patch('modules.capture_services.openwrt.offline_cleanup.cleanup_remote'):
            with self.assertRaises(SignalError):repair(m,self.device,None,Mock())
        self.assertNotIn('curl_repair_install',[x.args[0] for x in m._execute_stage.call_args_list])


class CurlEntryPointTests(unittest.TestCase):
    def test_recheck_runs_repair_then_verifies(self):
        from tests.test_dependency_diagnostic_paths import Owner
        from modules.capture_services.dependency_service import OpenWrtDependencyService
        from modules.capture_services.openwrt.models import DependencyInfo, DependencyState
        owner=Owner(); service=OpenWrtDependencyService(owner)
        broken=DependencyInfo('curl','curl',DependencyState.UNKNOWN,detail='curl_multi_get_offt')
        ready=DependencyInfo('curl','curl',DependencyState.READY)
        with patch('modules.capture_services.dependency_service.check_tools',side_effect=[(broken,),(ready,)]), patch('modules.capture_services.openwrt.curl_repair.repair') as fix:
            self.assertTrue(service.check_tool_async('curl'))
            operation,event=owner.pending; result=operation(event,Mock())
            fix.assert_called_once()
        self.assertFalse(result['error'])
        self.assertEqual(result['checked'][0].state,DependencyState.READY)
        service._complete(service.task.session_id,result)

    def test_new_install_with_existing_lib_uses_matching_candidate(self):
        from threading import Event
        from modules.capture_services.openwrt.dependency_batch_install import install_packages
        from modules.capture_services.openwrt.models import DependencyInfo,DependencyState
        missing=DependencyInfo('curl','curl',DependencyState.MISSING,('curl',))
        ready=DependencyInfo('curl','curl',DependencyState.READY)
        manager=Mock(); manager.command_runner.ssh_client.connected=True
        manager.command_runner.ssh_client._client=object()
        manager._missing_packages.side_effect=lambda rows: [p for r in rows if r.state==DependencyState.MISSING for p in r.missing_packages]
        checked=Mock(side_effect=[(missing,),(ready,)])
        device=SimpleNamespace(distribution='OpenWrt',package_manager='opkg')
        with patch('modules.capture_services.openwrt.curl_version_diagnostics.query_versions',return_value={'libcurl4':'8.10.1-r1'}), patch('modules.capture_services.openwrt.curl_repair.repair') as fix, patch('modules.capture_services.openwrt.common_bundle_installer.install_from_local') as regular:
            result=install_packages(manager,checked,device,'.',True,Event(),Mock())
            fix.assert_called_once();regular.assert_not_called()
        self.assertEqual(result,(ready,))
