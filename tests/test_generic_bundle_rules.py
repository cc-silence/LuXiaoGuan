import base64, hashlib, json, tempfile, unittest, zipfile
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock, patch
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from modules.app_update_services.manifest import canonical
from modules.capture_services.openwrt.common_bundle_download import prepare_archive
from modules.capture_services.openwrt.common_bundle_repository import CommonBundleRepository
from modules.capture_services.openwrt.common_bundle_installer import install_from_local
from tests.test_common_offline_bundle import fixture

class GenericBundleTests(unittest.TestCase):
    def test_both_bundle_types_choose_rule_candidate(self):
        for kind,name in [('common','tmux'),('capture','reaver')]:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);f=fixture(root/'source',kind=kind);data=b'generic variant';sha=hashlib.sha256(data).hexdigest();p=f.profile
                row=dict(release=p['release'],architecture=p['architecture'],package_manager='opkg',name=name,version='2',filename=name+'_2_'+p['architecture']+'.ipk',sha256=sha,blob='objects/'+sha,size=len(data),match=dict(installed_package='libexample',operator='equals',version='1'),depends=[],provides=[])
                f.catalog['package_variants']=[row];f.blobs[sha]=data
                raw=json.dumps(f.catalog,separators=(',',':')).encode()
                with zipfile.ZipFile(f.archive,'w') as z:
                    z.writestr('catalog.json',raw)
                    for key,value in f.blobs.items():z.writestr('objects/'+key,value)
                digest=hashlib.sha256(f.archive.read_bytes()).hexdigest()
                payload=dict(f.payload,sha256=digest,size=f.archive.stat().st_size,catalog_sha256=hashlib.sha256(raw).hexdigest(),catalog_size=len(raw),unpacked_size=len(raw)+sum(map(len,f.blobs.values())),url=f.payload['url'].replace(f.payload['sha256'],digest))
                key=Ed25519PrivateKey.generate();public=base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)).decode();envelope=canonical(dict(payload=payload,signature=base64.b64encode(key.sign(canonical(payload))).decode()))
                target=root/'resources';prepare_archive(f.archive,envelope,target,Event(),public_key=public,kind=kind)
                repo=CommonBundleRepository(target,public,kind);manager=SimpleNamespace(command_runner=Mock(),_execute_stage=Mock(return_value='libexample - 1\n'),_install_offline=Mock())
                with patch('modules.capture_services.openwrt.package_manager.compare_opkg_versions',return_value=True):
                    self.assertTrue(install_from_local(manager,(name,),f.device,root/'cache',True,Event(),Mock(),repository=repo))
                self.assertEqual(manager._install_offline.call_args.kwargs['local_packages'][name],'2')

    def test_apk_both_bundles_install_matched_variant(self):
        for kind,name in [('common','tmux'),('capture','reaver')]:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);f=fixture(root/'source',apk=True,kind=kind);data=b'generic apk variant';sha=hashlib.sha256(data).hexdigest();p=f.profile
                row=dict(release=p['release'],architecture=p['architecture'],package_manager='apk',name=name,version='2',filename=name+'-2.apk',sha256=sha,blob='objects/'+sha,size=len(data),match=dict(installed_package='libexample',operator='equals',version='1'),depends=[])
                f.catalog['package_variants']=[row];f.blobs[sha]=data
                raw=json.dumps(f.catalog,separators=(',',':')).encode()
                with zipfile.ZipFile(f.archive,'w') as z:
                    z.writestr('catalog.json',raw)
                    for key,value in f.blobs.items():z.writestr('objects/'+key,value)
                digest=hashlib.sha256(f.archive.read_bytes()).hexdigest()
                payload=dict(f.payload,sha256=digest,size=f.archive.stat().st_size,catalog_sha256=hashlib.sha256(raw).hexdigest(),catalog_size=len(raw),unpacked_size=len(raw)+sum(map(len,f.blobs.values())),url=f.payload['url'].replace(f.payload['sha256'],digest))
                key=Ed25519PrivateKey.generate();public=base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)).decode();envelope=canonical(dict(payload=payload,signature=base64.b64encode(key.sign(canonical(payload))).decode()))
                target=root/'resources';prepare_archive(f.archive,envelope,target,Event(),public_key=public,kind=kind)
                repo=CommonBundleRepository(target,public,kind)
                def stage(stage,*args):
                    if stage=='dependency_apk_versions':return 'libexample\t1\n'
                    if stage=='apk_variant_plan':return '(1/1) Installing '+name+' (2)'
                    return ''
                manager=SimpleNamespace(command_runner=Mock(),_execute_stage=Mock(side_effect=stage),_execute=Mock(),_package_uploader=Mock(return_value=Mock()))
                with patch('modules.capture_services.openwrt.offline_cleanup.cleanup_remote'):
                    self.assertTrue(install_from_local(manager,(name,),f.device,root/'cache',True,Event(),Mock(),repository=repo))
                self.assertIn('apk_variant_install',[x.args[0] for x in manager._execute_stage.call_args_list])
