import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from modules.capture_services.openwrt.bundle_variants import validate_variants, matching_variant
from modules.capture_services.openwrt.curl_apk_variant import install_local, verify_plan

class VariantTests(unittest.TestCase):
    def test_match_and_reject_wrong_rule(self):
        profile=dict(release='25.12.0',architecture='mipsel_24kc',package_manager='apk')
        profile['packages'] = [dict(name='curl'), dict(name='reaver')]
        sha='a'*64
        row=dict(**profile,name='curl',version='8.10.1-r1',filename='curl-8.10.1-r1.apk',sha256=sha,blob='objects/'+sha,size=100,match=dict(installed_package='libcurl4',operator='equals',version='8.10.1-r1'))
        self.assertEqual(validate_variants([row],[profile])[sha],row)
        device=SimpleNamespace(version='25.12.0',package_architecture='mipsel_24kc',package_manager='apk')
        self.assertEqual(matching_variant({'package_variants':[row]},device,'curl','8.10.1-r1'),row)
        self.assertIsNone(matching_variant({'package_variants':[row]},device,'curl','8.9-r1'))
        row['match']['operator']='execute'
        with self.assertRaises(ValueError):validate_variants([row],[profile])

    def test_other_tool_rule_reads_its_declared_library(self):
        from modules.capture_services.openwrt.bundle_variants import select_variants
        profile=dict(release='25.12.0',architecture='mipsel_24kc',package_manager='apk',packages=[dict(name='reaver')])
        sha='b'*64
        row=dict(release='25.12.0',architecture='mipsel_24kc',package_manager='apk',name='reaver',version='2-r1',filename='reaver-2-r1.apk',sha256=sha,blob='objects/'+sha,size=200,match=dict(installed_package='libexample',operator='equals',version='1-r3'))
        validate_variants([row],[profile])
        device=SimpleNamespace(version='25.12.0',package_architecture='mipsel_24kc',package_manager='apk')
        self.assertEqual(select_variants({'package_variants':[row]},device,'reaver',{'libexample':'1-r3'}),row)
        self.assertIsNone(select_variants({'package_variants':[row]},device,'reaver',{'libexample':'1-r2'}))

    def test_generic_ipk_rule_changes_only_declared_package(self):
        from modules.capture_services.openwrt.bundle_variants import apply_ipk_rules
        d=SimpleNamespace(version='24.10.0',package_architecture='mipsel_24kc',package_manager='opkg')
        profile=dict(packages=[dict(name='reaver',version='3'),dict(name='iw',version='1')])
        candidate=dict(release=d.version,architecture=d.package_architecture,package_manager='opkg',name='reaver',version='2',depends=['libexample'],match=dict(installed_package='libexample',operator='equals',version='1'))
        result=apply_ipk_rules(profile,dict(package_variants=[candidate]),d,('reaver',),{'libexample':'1'})
        self.assertEqual(result['packages'][0]['version'],'2')
        self.assertEqual(result['packages'][1],profile['packages'][1])
        self.assertIs(apply_ipk_rules(profile,dict(package_variants=[candidate]),d,('reaver',),{'libexample':'0'}),profile)

    def test_generic_apk_plan_never_changes_library(self):
        from modules.capture_services.openwrt.apk_variant_installer import install
        from modules.capture_services.openwrt.signal_manager import SignalError
        import hashlib
        m=Mock();m._package_uploader.return_value=Mock();data=b'candidate';sha=hashlib.sha256(data).hexdigest()
        row=dict(name='reaver',version='2-r1',filename='reaver-2-r1.apk',sha256=sha)
        repo=Mock()
        def materialize(row,directory,cancel):
            path=__import__('pathlib').Path(directory)/row['filename'];path.write_bytes(data);return path
        repo.materialize.side_effect=materialize
        def stage(name,*args):
            if name=='apk_variant_plan':return '(1/2) Installing reaver (2-r1)\n(2/2) Upgrading libexample (1-r3)'
            return ''
        m._execute_stage.side_effect=stage
        with patch('modules.capture_services.openwrt.offline_cleanup.cleanup_remote'):
            with self.assertRaises(SignalError):install(m,SimpleNamespace(sftp_available=True),row,repo,None,Mock())
        self.assertNotIn('apk_variant_install',[x.args[0] for x in m._execute_stage.call_args_list])

    def test_apk_plan_rejects_library_changes(self):
        verify_plan('(1/1) Installing curl (8.10.1-r1)', '8.10.1-r1')
        with self.assertRaises(ValueError):verify_plan('(1/2) Installing curl (8.10.1-r1)\n(2/2) Upgrading libcurl4 (8.10.1-r1)', '8.10.1-r1')

    def test_apk_local_candidate_requires_trial_and_plan(self):
        m=Mock();m._package_uploader.return_value=Mock()
        def stage(name,*args):
            if name=='curl_apk_trial':return 'curl 8.10.1 x\nProtocols: https\n'
            if name=='curl_apk_plan':return '(1/1) Installing curl (8.10.1-r1)'
            return ''
        m._execute_stage.side_effect=stage
        with patch('modules.capture_services.openwrt.curl_repair._local_candidate',return_value=b'candidate'), patch('modules.capture_services.openwrt.offline_cleanup.cleanup_remote'):
            self.assertTrue(install_local(m,SimpleNamespace(sftp_available=True),'8.10.1-r1',None,Mock()))
        names=[x.args[0] for x in m._execute_stage.call_args_list]
        self.assertLess(names.index('curl_apk_trial'),names.index('curl_apk_plan'))
        self.assertLess(names.index('curl_apk_plan'),names.index('curl_apk_install'))
