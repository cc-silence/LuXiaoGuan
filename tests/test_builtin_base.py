"""Real built-in archive and native-plan boundaries; no live router mutation."""
import hashlib
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
import zipfile
from modules.capture_services.openwrt.builtin_base_repository import BuiltinBaseRepository, resource_path
from modules.capture_services.openwrt.builtin_base_installer import install_builtin, install_apk_files
from modules.capture_services.openwrt.common_bundle_installer import install_from_local
from modules.capture_services.openwrt.package_manager import PackageManager

def device(apk=False):
    return NS(distribution='OpenWrt',version='25.12.0' if apk else '21.02.1',
              target='ath79/generic',package_architecture='mips_24kc',
              package_manager='apk' if apk else 'opkg')

class BuiltinBaseTests(unittest.TestCase):
    def test_actual_resource_has_only_two_roots_and_valid_objects(self):
        with zipfile.ZipFile(resource_path()) as z:
            d=json.loads(z.read('catalog.json'))
            self.assertEqual(len(d['profiles']),549)
            objects={}
            for p in d['profiles']:
                self.assertEqual({r['name'] for r in p['packages']},{'libuci-lua','wpa-cli'})
                for r in p['packages']:objects[r['blob']]=r
            self.assertEqual(len(objects),108)
            for name,row in objects.items():
                raw=z.read(name)
                self.assertEqual(len(raw),row['size'])
                self.assertEqual(hashlib.sha256(raw).hexdigest(),row['sha256'])
        self.assertLess(resource_path().stat().st_size,2200000)

    def test_release_asset_collection_and_archive_preserve_builtin(self):
        import shutil
        from scripts import release_layout
        from modules.release_identity import PACKAGE_DIRECTORY
        from modules.capture_services.openwrt.builtin_base_identity import SIZE,SHA256
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/"source"
            (root/"tools/openwrt_base").mkdir(parents=True)
            (root/"config").mkdir()
            shutil.copy2(resource_path(),root/"tools/openwrt_base/base.zip")
            shutil.copy2(release_layout.ROOT/"config/app_update.json",root/"config/app_update.json")
            target=Path(directory)/"package"
            with patch.object(release_layout,"ROOT",root):
                release_layout.seed_assets(target)
            built=target/"bin/tools/openwrt_base/base.zip"
            self.assertEqual((built.stat().st_size,hashlib.sha256(built.read_bytes()).hexdigest()),(SIZE,SHA256))
            with patch("modules.config_manager.BASE_DIR",target),patch("sys.frozen",True,create=True):
                self.assertEqual(resource_path(),built)
                self.assertIsNotNone(BuiltinBaseRepository().profile(device()))
            output=Path(directory)/"assets.zip"
            release_layout.make_zip(target,output)
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(hashlib.sha256(archive.read(PACKAGE_DIRECTORY+"/bin/tools/openwrt_base/base.zip")).hexdigest(),SHA256)
            built.write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError,"Built-in base resource checksum mismatch"):
                release_layout.verify_files(target,"test")

    def test_exact_identity_and_no_foreign_firmware_guess(self):
        repo=BuiltinBaseRepository()
        self.assertIsNotNone(repo.profile(device()))
        self.assertIsNotNone(repo.profile(device(True)))
        changed=device();changed.target='unavailable/target'
        self.assertIsNone(repo.profile(changed))
        changed=device();changed.distribution='other'
        self.assertIsNone(repo.profile(changed))

    def test_downloaded_bundle_wins_and_other_roots_never_search_builtin(self):
        args=(Mock(),('wpa-cli',),device(),None,False,threading.Event(),Mock())
        with patch('modules.capture_services.openwrt.common_bundle_installer._install_downloaded',return_value=True), patch(
                'modules.capture_services.openwrt.builtin_base_installer.install_builtin') as builtin:
            self.assertTrue(install_from_local(*args));builtin.assert_not_called()
        with patch('modules.capture_services.openwrt.common_bundle_installer._install_downloaded',return_value=False), patch(
                'modules.capture_services.openwrt.builtin_base_installer.install_builtin',return_value=True) as builtin:
            self.assertTrue(install_from_local(*args));builtin.assert_called_once()
            builtin.reset_mock()
            self.assertFalse(install_from_local(args[0],('curl',),*args[2:]));builtin.assert_not_called()
            self.assertFalse(install_from_local(args[0],('wpa-cli','curl'),*args[2:]));builtin.assert_not_called()

    def test_ipk_uses_closed_existing_transaction_without_network_download(self):
        manager=Mock();repo=BuiltinBaseRepository();profile=repo.profile(device())
        names={'libc'}
        for r in profile['packages']:
            for expression in r['depends']:names.add(expression.split()[0])
        manager._execute_stage.return_value='\n'.join(name+' - 9999' for name in names)
        seen=[]
        def install(*args,**kwargs):
            seen.extend(Path(p).read_bytes() for p in kwargs['local_paths'])
            self.assertEqual(set(kwargs['local_packages']),{'libuci-lua','wpa-cli'})
        manager._install_offline.side_effect=install
        with tempfile.TemporaryDirectory() as folder, patch(
                'modules.capture_services.openwrt.builtin_base_installer.compare_opkg_versions',return_value=True):
            self.assertTrue(install_builtin(manager,('libuci-lua','wpa-cli'),device(),folder,False,threading.Event(),Mock()))
        self.assertEqual(len(seen),2)
        manager._install_online.assert_not_called()

    def test_apk_plan_keeps_signature_checks_and_blocks_library_replacement(self):
        repo=BuiltinBaseRepository();profile=repo.profile(device(True));row=profile['packages'][0]
        for replacing in (False,True):
            manager=Mock();manager._preflight.return_value=PackageManager('apk')
            plan='(1/1) Installing '+row['name']+' ('+row['version']+')\nOK: 1 MiB'
            if replacing:plan='(1/1) Upgrading libc (bad)\nOK: 1 MiB'
            manager._execute_stage.side_effect=lambda name,*args: plan if name=='builtin_apk_plan' else ''
            with tempfile.TemporaryDirectory() as folder:
                path=repo.materialize(row,folder)
                if replacing:
                    with self.assertRaises(ValueError):
                        install_apk_files(manager,[row],[path],False,threading.Event(),Mock(),Mock())
                else:install_apk_files(manager,[row],[path],False,threading.Event(),Mock(),Mock())
            commands=[c.args[1] for c in manager._execute_stage.call_args_list if c.args[0].startswith('builtin_apk')]
            self.assertTrue(all('--no-network --no-cache --repositories-file /dev/null' in c for c in commands))
            self.assertFalse(any('--allow-untrusted' in c for c in commands))
            self.assertEqual(sum(c.args[0]=='builtin_apk_install' for c in manager._execute_stage.call_args_list),int(not replacing))
            manager._execute.assert_called_once()

    def test_corrupt_archive_rejected_before_mutation(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'bad.zip';path.write_bytes(b'bad')
            with self.assertRaisesRegex(ValueError,'checksum'):
                BuiltinBaseRepository(path).profile(device())


    def test_signal_installation_consults_local_for_only_the_actual_missing_root(self):
        from modules.capture_services.openwrt.signal_manager import RouterSignalManager
        manager=RouterSignalManager(Mock())
        before=dict(lua=True,uci=False,ubus=True,jsonc=True,root=True,sha256sum=True)
        after=dict(before,uci=True)
        manager.probe_dependencies=Mock(side_effect=[before,after])
        with patch('modules.capture_services.openwrt.common_bundle_installer.install_from_local',return_value=True) as local:
            self.assertEqual(manager.install_auto(device(),None,threading.Event()),after)
            self.assertEqual(local.call_args.args[1],('libuci-lua',))

    def test_builtin_source_is_retained_in_final_reporting(self):
        from modules.capture_services.openwrt.installation_sources import install_with_sources
        online=Mock();computer=Mock()
        self.assertEqual(install_with_sources(lambda:'builtin',online,computer,Mock()),'builtin')
        online.assert_not_called();computer.assert_not_called()
