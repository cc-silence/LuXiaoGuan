"""Customer ERW dependency regression; no router or public network mutations."""
from dataclasses import replace
import gzip
import json
from types import SimpleNamespace as NS
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import Mock,patch
import unittest
from modules.capture_services.openwrt import signal_package_feed as feed
from modules.capture_services.openwrt.dependency_resolution import (
    InstalledPackages,resolve_packages,UnavailablePackage,UnsatisfiedDependency,KernelModuleRequired)
from modules.capture_services.openwrt.package_conflicts import PackageConflict
from modules.capture_services.openwrt.kernel_package_feed import kernel_sources,fetch_tun_index
from modules.capture_services.openwrt.dependency_http import DownloadHTTPError
from modules.capture_services.openwrt.package_manager import package_error_reason
from scripts.openwrt_packages.opkg_version import satisfies

ABI='5.15.162-1-03ba5b5fee47f2232a088e3cd9832aec'
PROFILE=feed.DeviceProfile('23.05.4','aarch64_cortex-a53')
PAYLOAD=b'fixture-package'


def package(name,depends=(),conflicts=()):
    return feed.FeedPackage(name,'1',name+'.ipk',hashlib.sha256(PAYLOAD).hexdigest(),
        len(PAYLOAD),'https://fixture/'+name+'.ipk',depends,(),conflicts)


def index_blob(abi=ABI,arch='aarch64_cortex-a53'):
    text=('Package: kmod-tun\nVersion: 5.15.162-1\nDepends: kernel (='+abi+')\n'
        'Architecture: '+arch+'\nFilename: kmod-tun.ipk\nSHA256sum: '+hashlib.sha256(PAYLOAD).hexdigest()+
        '\nSize: '+str(len(PAYLOAD))+'\n\n')
    return gzip.compress(text.encode())


class CompatibilityTests(unittest.TestCase):
    def test_official_conflicts_parsed_and_reverse_installed_conflict_stops_ui(self):
        text=('Package: dnscrypt-proxy2\nVersion: 2\nFilename: dnscrypt2.ipk\n'
            'Size: 1\nSHA256sum: '+'a'*64+'\nConflicts: dnscrypt-proxy\n\n')
        index=feed.parse_packages_index(text,'https://fixture/Packages')
        index.update({'dnscrypt-proxy':package('dnscrypt-proxy'),
            'luci-app-dnscrypt-proxy':package('luci-app-dnscrypt-proxy',('dnscrypt-proxy',))})
        installed=InstalledPackages({'dnscrypt-proxy2':'2'})
        with self.assertRaises(PackageConflict) as error:
            resolve_packages(index,('luci-app-dnscrypt-proxy',),installed)
        self.assertEqual(error.exception.other,'dnscrypt-proxy2')
        self.assertIn('\u4fdd\u7559',str(error.exception))
        self.assertNotIn('\\u',str(error.exception))

    def test_old_core_and_new_core_conflict_in_both_install_orders(self):
        index={'old':package('old'),'new':package('new',conflicts=('old',))}
        for wanted,installed in [(('new',),{'old'}),(('old',),{'new'}),(('old','new'),set()),(('new','old'),set())]:
            with self.subTest(wanted=wanted),self.assertRaises(PackageConflict):resolve_packages(index,wanted,installed)

    def test_unknown_installed_conflict_version_is_not_taken_from_feed(self):
        index={'installed':replace(package('installed'),version='3'),
            'tool':package('tool',conflicts=('installed (< 2)',))}
        for installed in ({'installed'},InstalledPackages({'installed':''})):
            with self.subTest(installed=type(installed).__name__),self.assertRaises(PackageConflict):
                resolve_packages(index,('tool',),installed,satisfies)

    def test_known_installed_conflict_uses_live_version_not_feed(self):
        index={'installed':replace(package('installed'),version='3'),
            'tool':package('tool',conflicts=('installed (< 2)',))}
        with self.assertRaises(PackageConflict):
            resolve_packages(index,('tool',),InstalledPackages({'installed':'1'}),satisfies)
        result=resolve_packages(index,('tool',),InstalledPackages({'installed':'3'}),satisfies)
        self.assertEqual([p.name for p in result],['tool'])

    def test_planned_conflict_uses_verified_candidate_version(self):
        index={'planned':replace(package('planned'),version='3'),
            'tool':package('tool',conflicts=('planned (< 2)',))}
        result=resolve_packages(index,('planned','tool'),set(),satisfies)
        self.assertEqual([p.name for p in result],['planned','tool'])
        index['planned']=replace(index['planned'],version='1')
        with self.assertRaises(PackageConflict):
            resolve_packages(index,('planned','tool'),set(),satisfies)

    def test_new_ui_with_matching_backend_is_allowed(self):
        index={'ui':package('ui',('dnscrypt-proxy2',)),'dnscrypt-proxy2':package('dnscrypt-proxy2',conflicts=('dnscrypt-proxy',))}
        result=resolve_packages(index,('ui',),{'dnscrypt-proxy2'})
        self.assertEqual([p.name for p in result],['ui'])

    def test_conflicting_alternative_can_select_valid_provider(self):
        index={'tool':package('tool',('bad | good',)),'bad':package('bad',conflicts=('installed',)),
               'good':package('good')}
        self.assertEqual([p.name for p in resolve_packages(index,('tool',),{'installed'})],['good','tool'])

    def test_missing_and_existing_version_mismatch_are_distinct(self):
        with self.assertRaises(UnavailablePackage):resolve_packages({},('luci-app-filebrowser',),set())
        with self.assertRaises(UnsatisfiedDependency) as error:
            resolve_packages({'tool':package('tool')},('tool (>= 2)',),InstalledPackages({'tool':'1'}),satisfies)
        self.assertNotIsInstance(error.exception,UnavailablePackage)

    def test_native_opkg_and_apk_errors_are_not_reported_as_network_failure(self):
        for text in ('check_data_file_clashes: dnscrypt-proxy wants file; already provided by package dnscrypt-proxy2',
                     'dnscrypt-proxy2 conflicts with dnscrypt-proxy'):
            reason=package_error_reason(text)
            self.assertIn('dnscrypt-proxy2',reason)
            self.assertIn('\u4fdd\u7559',reason)
        self.assertIn('\u76f8\u540c\u6587\u4ef6',package_error_reason('trying to overwrite /file owned by another package'))


class KernelIdentityTests(unittest.TestCase):
    def setUp(self):self.installed=InstalledPackages({'kernel':ABI,'libc':'1'})

    def test_all_stable_series_and_targets_keep_exact_identity(self):
        targets=('mediatek/filogic','mediatek/mt7622','ramips/mt7621','ipq40xx/generic',
            'ipq806x/generic','ipq807x/generic','qualcommax/ipq807x','ath79/generic','ath79/nand',
            'bcm53xx/generic','bcm27xx/bcm2711','mvebu/cortexa9','mvebu/cortexa53','sunxi/cortexa53','x86/64')
        for version in ('19.07.10','21.02.7','22.03.7','23.05.4','24.10.8'):
            for target in targets:
                with self.subTest(version=version,target=target):
                    abi,urls=kernel_sources(replace(PROFILE,release=version),target,self.installed,'5.15.162')
                    self.assertEqual(abi,ABI)
                    self.assertTrue(all('/'+version+'/targets/'+target+'/' in u for u in urls))
                    self.assertTrue(any('/kmods/'+ABI+'/' in u for u in urls))
                    self.assertTrue(any('/packages/Packages' in u for u in urls))
                    self.assertTrue(all(u.startswith(('https://downloads.openwrt.org/','https://archive.openwrt.org/')) for u in urls))

    def test_unknown_stale_or_unsafe_identity_stops_before_any_fetch(self):
        for target,installed,kernel,profile in [('',self.installed,'5.15.162',PROFILE),
                ('../filogic',self.installed,'5.15.162',PROFILE),
                ('mediatek/filogic',InstalledPackages({'kernel':'5.15.162'}),'5.15.162',PROFILE),
                ('mediatek/filogic',self.installed,'5.15.163',PROFILE),
                ('mediatek/filogic',self.installed,'5.15.162',replace(PROFILE,compatibility_trial='kwrt')),
                ('mediatek/filogic',self.installed,'5.15.162',replace(PROFILE,release='24.10-SNAPSHOT'))]:
            fetch=Mock()
            with self.subTest(target=target,kernel=kernel),self.assertRaises(ValueError):
                fetch_tun_index(profile,target,installed,kernel,fetch)
            fetch.assert_not_called()

    def test_correct_source_and_archive_flat_layout_are_supported(self):
        for flat in (False,True):
            calls=[]
            def fetch(url,**kwargs):
                calls.append(url)
                if flat and '/kmods/' in url:raise DownloadHTTPError(404)
                return index_blob()
            result=fetch_tun_index(PROFILE,'mediatek/filogic',self.installed,'5.15.162',fetch)
            self.assertEqual(result['kmod-tun'].depends,('kernel (='+ABI+')',))
            if flat:self.assertTrue(any('/filogic/packages/' in url for url in calls))

    def test_matching_uname_without_exact_build_or_architecture_is_rejected(self):
        for blob in (index_blob(ABI.replace('03ba','ffff')),index_blob(arch='mipsel_24kc')):
            with self.assertRaises(ValueError):
                fetch_tun_index(PROFILE,'mediatek/filogic',self.installed,'5.15.162',Mock(return_value=blob))

    def test_network_failure_is_not_reported_as_package_absence(self):
        with self.assertRaises(RuntimeError):
            fetch_tun_index(PROFILE,'mediatek/filogic',self.installed,'5.15.162',Mock(side_effect=RuntimeError('network unavailable')))
        with self.assertRaises(ValueError) as absent:
            fetch_tun_index(PROFILE,'mediatek/filogic',self.installed,'5.15.162',Mock(side_effect=DownloadHTTPError(404)))
        self.assertNotIsInstance(absent.exception,RuntimeError)

    def test_package_revision_is_not_a_running_kernel_suffix(self):
        fetch=Mock()
        for running in ('5.15.162-1','5.15','5',ABI):
            with self.subTest(running=running),self.assertRaises(ValueError):
                fetch_tun_index(PROFILE,'mediatek/filogic',self.installed,running,fetch)
        fetch.assert_not_called()

    def test_invalid_success_response_is_unreadable_not_absent(self):
        for body in (b'',b'<html>gateway unavailable</html>',b'\xff\xfe'):
            with self.subTest(body=body),self.assertRaises(RuntimeError) as failure:
                fetch_tun_index(PROFILE,'mediatek/filogic',self.installed,'5.15.162',
                    Mock(return_value=gzip.compress(body)))
            self.assertIn('Invalid',str(failure.exception))

    def test_verified_index_without_tun_reports_absence(self):
        other=gzip.decompress(index_blob()).replace(b'Package: kmod-tun',b'Package: kmod-other')
        with self.assertRaises(ValueError) as failure:
            fetch_tun_index(PROFILE,'mediatek/filogic',self.installed,'5.15.162',
                Mock(return_value=gzip.compress(other)))
        self.assertNotIsInstance(failure.exception,RuntimeError)

    def test_unreadable_primary_index_can_use_verified_fallback(self):
        fetch=Mock(side_effect=[gzip.compress(b'<html>unavailable</html>'),index_blob()])
        result=fetch_tun_index(PROFILE,'mediatek/filogic',self.installed,'5.15.162',fetch)
        self.assertEqual(result['kmod-tun'].depends,('kernel (='+ABI+')',))
        self.assertEqual(fetch.call_count,2)

    def test_cancelled_or_corrupt_index_never_passes(self):
        cancel=Event();cancel.set();fetch=Mock()
        with self.assertRaises(RuntimeError):fetch_tun_index(PROFILE,'mediatek/filogic',self.installed,'5.15.162',fetch,cancel)
        fetch.assert_not_called()
        with self.assertRaises(RuntimeError):fetch_tun_index(PROFILE,'mediatek/filogic',self.installed,'5.15.162',Mock(return_value=b'corrupt'))

    def test_resolver_keeps_all_other_kernel_protections(self):
        tun=package('kmod-tun',('kernel (='+ABI+')',))
        with self.assertRaises(KernelModuleRequired):resolve_packages({'kmod-tun':tun},('kmod-tun',),self.installed,satisfies)
        self.assertEqual(resolve_packages({'kmod-tun':tun},('kmod-tun',),self.installed,satisfies,allowed_kernel=('kmod-tun',)),[tun])
        wrong=replace(tun,depends=('kernel (=wrong-build)',))
        with self.assertRaises(KernelModuleRequired):resolve_packages({'kmod-tun':wrong},('kmod-tun',),self.installed,satisfies,allowed_kernel=('kmod-tun',))
        other=package('kmod-mac80211',('kernel (='+ABI+')',))
        with self.assertRaises(KernelModuleRequired):resolve_packages({other.name:other},(other.name,),self.installed,satisfies,allowed_kernel=(other.name,))

    def test_complete_pc_download_adds_only_matching_tun_before_zerotier(self):
        index={'zerotier':package('zerotier',('libc','kmod-tun'))}
        def fetch(url,**kwargs):return index_blob() if '/Packages' in url else PAYLOAD
        with TemporaryDirectory() as folder,patch.object(feed,'fetch_index',return_value=index):
            paths=feed.download_profile_packages(PROFILE,self.installed,folder,fetch=fetch,wanted=('zerotier',),
                version_check=satisfies,target='mediatek/filogic',kernel_probe=lambda:'5.15.162')
            self.assertEqual([p.name for p in paths],['kmod-tun.ipk','zerotier.ipk'])
            self.assertTrue(all(p.read_bytes()==PAYLOAD for p in paths))

    def test_existing_tun_and_normal_tools_do_not_probe_kernel(self):
        index={'zerotier':package('zerotier',('kmod-tun',))}
        probe=Mock(side_effect=AssertionError('unnecessary kernel query'))
        with TemporaryDirectory() as folder,patch.object(feed,'fetch_index',return_value=index):
            paths=feed.download_profile_packages(PROFILE,InstalledPackages({'kmod-tun':'1'}),folder,
                fetch=Mock(return_value=PAYLOAD),wanted=('zerotier',),kernel_probe=probe)
            self.assertEqual([p.name for p in paths],['zerotier.ipk'])
        probe.assert_not_called()

    def test_official_customer_index_fixture_verifies_real_tun_metadata(self):
        source=Path(__file__).parent/'fixtures/official_other_dependency_packages_23_05_4.json'
        fixture=json.loads(source.read_text(encoding='utf-8'))
        blob=gzip.compress(fixture['kernel_stanza'].encode())
        result=fetch_tun_index(PROFILE,'mediatek/filogic',self.installed,'5.15.162',Mock(return_value=blob))
        self.assertEqual(result['kmod-tun'].version,'5.15.162-1')
        self.assertGreater(result['kmod-tun'].size,0)
        for sample in fixture['legacy_kernel_stanzas']:
            with self.subTest(release=sample['release']):
                profile=feed.DeviceProfile(sample['release'],sample['architecture'])
                installed=InstalledPackages({'kernel':sample['kernel_abi']})
                def legacy_fetch(url,**kwargs):
                    if '/kmods/' in url:raise DownloadHTTPError(404)
                    return gzip.compress(sample['stanza'].encode())
                verified=fetch_tun_index(profile,sample['target'],installed,sample['kernel_abi'].split('-')[0],legacy_fetch)
                self.assertIn('kmod-tun',verified)

        records=feed.parse_packages_index('\n\n'.join(fixture['dnscrypt_stanzas']),fixture['dnscrypt_index_url'][:-3])
        with self.assertRaises(PackageConflict):resolve_packages(records,('dnscrypt-proxy',),{'dnscrypt-proxy2'})

    def test_manager_passes_live_kernel_and_target_then_validates_no_feed_plan(self):
        from modules.capture_services.openwrt import dependency_manager as manager_module
        from modules.capture_services.openwrt.package_manager import PackageManager
        manager=manager_module.OpenWrtDependencyManager(Mock())
        manager._preflight=Mock(return_value=PackageManager('opkg'))
        manager._execute=Mock(return_value='')
        def stage(name,*args):
            if name=='dependency_list_installed':return 'kernel - '+ABI+'\nlibc - 1\n'
            if name=='dependency_running_kernel':return '5.15.162\n'
            if name=='dependency_local_plan':return 'Installing kmod-tun (5.15.162-1) to root...\nInstalling zerotier (1) to root...'
            return ''
        manager._execute_stage=Mock(side_effect=stage)
        device=NS(version=PROFILE.release,distribution='OpenWrt',target='mediatek/filogic',
            package_architecture=PROFILE.package_arch,package_manager='opkg')
        def download(*args,**kwargs):
            kwargs['version_check']=satisfies
            return feed.download_profile_packages(*args,fetch=lambda url,**k:index_blob() if '/Packages' in url else PAYLOAD,**kwargs)
        with TemporaryDirectory() as folder,patch.object(feed,'fetch_index',return_value={'zerotier':package('zerotier',('kmod-tun',))}), \
                patch.object(manager_module,'download_profile_packages',side_effect=download), \
                patch('modules.capture_services.openwrt.package_space.ipk_installed_size',return_value=1), \
                patch('modules.capture_services.openwrt.package_space.ipk_package_versions',return_value={'kmod-tun':'5.15.162-1','zerotier':'1'}):
            uploader=Mock()
            manager._install_offline(('zerotier',),device,folder,False,Event(),Mock(),uploader=uploader)
        self.assertEqual(uploader.call_count,2)
        names=[c.args[0] for c in manager._execute_stage.call_args_list]
        self.assertEqual(names.count('dependency_running_kernel'),1)
        self.assertIn('dependency_offline_install',names)
        plan=next(c.args[1] for c in manager._execute_stage.call_args_list if c.args[0]=='dependency_local_plan')
        self.assertIn('OPKG_CONF_DIR=',plan)
        self.assertIn('--noaction',plan)
        self.assertNotIn('--force',plan)

    def test_kernel_probe_cancellation_preserves_original_error(self):
        from modules.capture_services.openwrt import dependency_manager as manager_module
        from modules.capture_services.openwrt.signal_manager import SignalError
        manager=manager_module.OpenWrtDependencyManager(Mock())
        manager._execute_stage=Mock(return_value='kernel - '+ABI+'\n')
        manager._preflight=Mock()
        device=NS(version=PROFILE.release,distribution='OpenWrt',target='mediatek/filogic',
            package_architecture=PROFILE.package_arch,package_manager='opkg')
        cancelled=SignalError('cancelled','kernel query cancelled')
        with patch.object(manager_module,'download_profile_packages',side_effect=cancelled):
            with self.assertRaises(SignalError) as error:
                manager._install_offline(('zerotier',),device,None,False,Event(),Mock(),uploader=Mock())
        self.assertIs(error.exception,cancelled)
        manager._preflight.assert_not_called()

    def test_conflict_and_absent_plugin_stop_before_any_package_download(self):
        for wanted,index,installed,error in [
                (('luci-app-filebrowser',),{},set(),UnavailablePackage),
                (('ui',),{'ui':package('ui',('old',)),'old':package('old'),
                    'new':package('new',conflicts=('old',))},{'new'},PackageConflict)]:
            fetch=Mock(side_effect=AssertionError('package download must not start'))
            with TemporaryDirectory() as folder,patch.object(feed,'fetch_index',return_value=index):
                with self.assertRaises(error):feed.download_profile_packages(PROFILE,installed,folder,fetch=fetch,wanted=wanted)
            fetch.assert_not_called()


class SourceReportingTests(unittest.TestCase):
    def test_proxy_skip_does_not_claim_router_network_failure(self):
        from modules.capture_services.openwrt.temporary_package_sources import domestic_sources
        from modules.capture_services.openwrt.installation_sources import install_with_sources
        from modules.capture_services.openwrt.package_manager import PackageManager
        from modules.capture_services.openwrt.signal_manager import SignalError
        manager=Mock();tell=Mock();computer=Mock()
        def online():
            with domestic_sources(manager,PackageManager('opkg'),Event(),tell):
                self.fail('proxy path must not run native updates')
        with patch('modules.network_policy.configured_proxy_channels',return_value=('proxy',)):
            self.assertIn('\u7535\u8111',install_with_sources(None,online,computer,tell))
        manager._execute.assert_not_called();computer.assert_called_once()
        messages='\n'.join(call.args[0] for call in tell.call_args_list)
        self.assertIn('\u8df3\u8fc7',messages)
        self.assertNotIn('\u8def\u7531\u5668\u8054\u7f51\u8def\u7ebf\u5931\u8d25',messages)
        tell.reset_mock();computer.side_effect=SignalError('environment','missing plugin')
        with patch('modules.network_policy.configured_proxy_channels',return_value=('proxy',)):
            with self.assertRaises(SignalError) as failure:install_with_sources(None,online,computer,tell)
        self.assertIn('router_online_skipped',failure.exception.package_diagnostic)
        self.assertNotIn('\u8def\u7531\u5668\u8054\u7f51\u5b89\u88c5\u5931\u8d25',str(failure.exception))

if __name__=='__main__':unittest.main()
