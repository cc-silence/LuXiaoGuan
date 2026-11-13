import copy
import unittest
from scripts.openwrt_packages.supplement_runtime_bundle import extend_runtime


def row(name, depends=(), architecture='mipsel_24kc'):
    return dict(name=name,version='1',architecture=architecture,filename=name+'_1_mipsel_24kc.ipk',sha256='a'*64,size=100,depends=list(depends),provides=[],url='https://downloads.openwrt.org/'+name)


class RuntimeSupplementTests(unittest.TestCase):
    def profile(self):
        return dict(package_manager='opkg',architecture='mipsel_24kc',packages=[row('tool',('librt',))],firmware_required=['libc','librt'])

    def libraries(self):
        return [row('librt',('libpthread',)),row('libpthread',('libgcc1',)),row('libgcc1',('libc',))]

    def test_standard_build_and_audit_include_declared_runtimes(self):
        from modules.capture_services.openwrt.bundle_dependencies import closure
        selected, provided=closure(self.profile()['packages']+self.libraries(),['tool'],runtime_supplements=('librt','libpthread','libgcc1'))
        self.assertEqual({r['name'] for r in selected},{'tool','librt','libpthread','libgcc1'})
        self.assertEqual(provided,['libc'])

    def test_exact_runtime_closure_preserves_existing_package_and_input(self):
        profile=self.profile();before=copy.deepcopy(profile)
        result=extend_runtime(profile,self.libraries())
        self.assertEqual(profile,before)
        self.assertEqual(result['packages'][0],profile['packages'][0])
        self.assertEqual(result['runtime_supplements'],['libgcc1','libpthread','librt'])
        self.assertEqual(result['firmware_required'],['libc'])

    def test_never_replaces_installed_catalog_library_version(self):
        profile=self.profile();existing=row('libgcc1',('libc',));existing['version']='old'
        profile['packages'].append(existing)
        result=extend_runtime(profile,self.libraries())
        self.assertEqual(next(r for r in result['packages'] if r['name']=='libgcc1'),existing)
        self.assertNotIn('libgcc1',result['runtime_supplements'])

    def test_no_cross_architecture_library(self):
        libraries=self.libraries();libraries[0]['architecture']='x86_64'
        with self.assertRaisesRegex(ValueError,'architecture'):extend_runtime(self.profile(),libraries)

    def test_incomplete_target_libraries_are_rejected(self):
        with self.assertRaisesRegex(ValueError,'missing'):extend_runtime(self.profile(),self.libraries()[:-1])

    def test_apk_profiles_remain_byte_semantically_unchanged(self):
        profile=self.profile();profile['package_manager']='apk'
        self.assertEqual(extend_runtime(profile,[]),profile)

    def test_additional_dependency_cannot_be_silently_lost(self):
        libraries=self.libraries();libraries[0]['depends'].append('missing-runtime')
        with self.assertRaises(ValueError):extend_runtime(self.profile(),libraries)
