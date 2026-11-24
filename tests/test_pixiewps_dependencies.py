"""Dependency maintenance coverage; never execute a wireless operation."""
from copy import deepcopy
from types import SimpleNamespace as NS
from unittest import TestCase
from unittest.mock import Mock, patch
from modules.capture_services.openwrt.pixiewps_probe import read_version
from modules.capture_services.openwrt.dependency_manager import OpenWrtDependencyManager
from modules.capture_services.openwrt.dependency_groups import CAPTURE_PACKAGES, CAPTURE_REMOVABLE_PACKAGES
from modules.capture_services.openwrt.bundle_specs import CAPTURE_ROOTS
from modules.capture_services.openwrt.models import DependencyState
from scripts.openwrt_packages.supplement_capture_pixiewps import extend_profile


def result(text='', code=0, **flags):
    return NS(stdout=text, stderr='', exit_code=code, cancelled=flags.get('cancelled',False), timed_out=flags.get('timed_out',False))


def package(name, depends=(), arch='mipsel_24kc', version='1'):
    return dict(name=name, architecture=arch, version=version, depends=list(depends), provides=[], sha256='a'*64, size=12)


class PixiewpsDependencyTests(TestCase):
    def test_upstream_version_exit_three_is_valid_and_only_read_only_command_runs(self):
        runner=Mock();runner.run.return_value=result('Pixiewps 1.4.2',3)
        self.assertEqual(read_version(runner),'1.4.2')
        self.assertEqual(runner.run.call_args.args[1],'pixiewps --version 2>&1')

    def test_loader_error_timeout_cancel_and_false_banner_do_not_pass(self):
        for value in (result('Error loading shared library libpthread.so',127),result('Pixiewps 1.4.2',127),result('usage',3),result(timed_out=True),result(cancelled=True)):
            with self.subTest(value=value),self.assertRaises(RuntimeError):read_version(Mock(run=Mock(return_value=value)))

    def test_shared_install_remove_and_future_collector_include_pixie(self):
        for names in (CAPTURE_PACKAGES['wps'],CAPTURE_REMOVABLE_PACKAGES['wps'],OpenWrtDependencyManager.FEATURE_PACKAGES['wps'],CAPTURE_ROOTS):
            self.assertIn('pixiewps',names)
        self.assertIn('pixiewps',OpenWrtDependencyManager.FEATURE_COMMANDS['wps'])

    def test_existing_reaver_with_missing_pixie_is_missing_not_ready(self):
        manager=OpenWrtDependencyManager(Mock())
        def probe(command,event):return ('missing','') if command=='pixiewps' else ('ready','1.4.2')
        with patch.object(manager,'_probe_command',side_effect=probe):
            rows=manager.check({'wps':True},True,None)
        row=next(r for r in rows if r.key=='wps')
        self.assertEqual(row.state,DependencyState.MISSING)
        self.assertIn('pixiewps',row.missing_packages)
        self.assertNotIn('iw',row.missing_packages)
        self.assertNotIn('ip-tiny',row.missing_packages)

    def test_runtime_failure_is_not_ready(self):
        runner=Mock();runner.run.side_effect=[result('OK:/usr/bin/pixiewps'),result('Error loading shared library libpthread.so',127)]
        state,detail=OpenWrtDependencyManager(runner)._probe_command('pixiewps',None)
        self.assertEqual(state,'broken')
        self.assertIn('libpthread',detail)

    def test_extend_preserves_original_libraries_and_profile(self):
        profile=dict(architecture='mipsel_24kc',roots=['reaver'],packages=[package('reaver'),package('libpthread')],firmware_required=['libc'])
        original=deepcopy(profile);candidate=package('pixiewps',['libc','libpthread'])
        extended=extend_profile(profile,candidate)
        self.assertEqual(profile,original)
        self.assertEqual(extended['packages'][:2],original['packages'])
        self.assertEqual(extended['packages'][-1]['name'],'pixiewps')
        self.assertEqual(extended['firmware_required'],['libc'])

    def test_missing_or_wrong_runtime_and_wrong_arch_are_rejected(self):
        profile=dict(architecture='mipsel_24kc',roots=['reaver'],packages=[package('reaver'),package('libpthread',version='1')],firmware_required=['libc'])
        for candidate in (package('pixiewps',['libpthread (= 2)']),package('pixiewps',['absent']),package('pixiewps',arch='aarch64_generic')):
            with self.subTest(candidate=candidate),self.assertRaises(ValueError):extend_profile(profile,candidate)
