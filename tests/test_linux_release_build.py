"""Native admission and candidate retention must fail safely on any build host."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from scripts import build_linux_release as builder, build_cleanup as cleanup
from tests.test_linux_release_layout import elf_bytes


class LinuxBuildAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()
        (self.root/'main.py').write_text('APP_VERSION = "1.0.21_alpha"\n',encoding='utf-8')

    def test_plan_is_read_only_and_does_not_claim_acceptance(self):
        value=builder.plan(self.root)
        self.assertEqual(value['platform'],'linux-x64');self.assertEqual(value['version'],'1.0.21_alpha')
        self.assertFalse(value['publication']);self.assertFalse(value['desktop_validated'])
        self.assertEqual([p.name for p in self.root.iterdir()],['main.py'])

    def test_wrong_runtime_stops_before_interpreter_or_terminal_files_are_read(self):
        with patch.object(builder,'inspect_runtime',return_value={'ready':False,'errors':['wrong OS']}),patch.object(builder.Path,'open') as opened:
            with self.assertRaises(ValueError):builder.preflight(self.root)
            opened.assert_not_called()

    def native_fixture(self):
        executable=self.root/'build/debian12/python-runtime/test/prefix/bin/python3.10'
        executable.parent.mkdir(parents=True);executable.write_bytes(b'not executed')
        pointer=self.root/'build/debian12/python-runtime/current.json'
        pointer.write_text(json.dumps(dict(status='private-runtime-verified',executable=str(executable))),encoding='utf-8')
        library=self.root/'bin/router_terminal/libroutermanager_terminal.so'
        library.parent.mkdir(parents=True);library.write_bytes(elf_bytes())
        value=dict(status='installed-and-native-smoke-verified',native_smoke_passed=True,
                   platform='linux-x64',qt_version='6.3.2',sha256=builder.file_sha256(library),source_sha256={'source.cpp':'a'*64})
        library.with_suffix('.json').write_text(json.dumps(value),encoding='utf-8')
        return executable,library,value

    def test_native_provenance_must_match_current_terminal_bytes_and_sources(self):
        executable,library,value=self.native_fixture()
        with patch.object(builder,'inspect_runtime',return_value={'ready':True}),patch.object(builder.importlib.metadata,'version',return_value='6.20.0'),patch.object(builder.sys,'executable',str(executable)),patch('scripts.build_linux_terminal.source_inputs',return_value=value['source_sha256']),patch.object(builder.subprocess,'check_output',return_value=b''):
            result=builder.preflight(self.root)
            self.assertEqual(result['terminal_sha256'],value['sha256'])
            library.write_bytes(elf_bytes()+b'changed')
            with self.assertRaises(ValueError):builder.preflight(self.root)

    def test_terminal_built_against_old_qt_is_rejected(self):
        executable,library,value=self.native_fixture()
        value['qt_version']='6.3.0'
        library.with_suffix('.json').write_text(json.dumps(value),encoding='utf-8')
        with patch.object(builder,'inspect_runtime',return_value={'ready':True}),patch.object(builder.importlib.metadata,'version',return_value='6.20.0'),patch.object(builder.sys,'executable',str(executable)):
            with self.assertRaisesRegex(ValueError,'matching native smoke evidence'):
                builder.preflight(self.root)

    def test_unreviewed_assets_stop_build_instead_of_being_silently_omitted(self):
        executable,library,value=self.native_fixture()
        with patch.object(builder,'inspect_runtime',return_value={'ready':True}),patch.object(builder.importlib.metadata,'version',return_value='6.20.0'),patch.object(builder.sys,'executable',str(executable)),patch('scripts.build_linux_terminal.source_inputs',return_value=value['source_sha256']),patch.object(builder.subprocess,'check_output',return_value=b'images/new-icon.svg\n'):
            with self.assertRaises(ValueError):builder.preflight(self.root)

    def test_failed_preflight_never_invokes_a_compiler(self):
        with patch.object(cleanup,'ROOT',self.root),patch.object(builder,'ROOT',self.root),patch.object(builder,'preflight',side_effect=ValueError('no Debian')),patch.object(builder,'run_build') as run:
            with self.assertRaises(ValueError):builder.build_candidate()
            run.assert_not_called()
        self.assertFalse((self.root/'build/debian12/releases').exists())

    def test_smoke_environment_uses_private_user_paths_without_session_credentials(self):
        with patch.dict(builder.os.environ,{'PRIVATE_PUBLISH_TOKEN':'private','DBUS_SESSION_BUS_ADDRESS':'private','PYTHONPATH':'private'}):
            env=builder.environment(self.root)
        self.assertNotIn('PRIVATE_PUBLISH_TOKEN',env);self.assertNotIn('DBUS_SESSION_BUS_ADDRESS',env)
        self.assertNotIn('PYTHONPATH',env);self.assertEqual(env['HOME'],str(self.root))
        self.assertEqual(env['QT_QPA_PLATFORM'],'offscreen')


class LinuxCandidateRetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();(self.root/'build/debian12/releases').mkdir(parents=True)
        self.windows=self.root/'build/releases/windows-usable';self.windows.mkdir(parents=True)
        (self.windows/'data').write_bytes(b'Windows retained')

    def stage(self,stamp,status=cleanup.LINUX_COMPLETE):
        stage=self.root/'build/debian12/releases'/('1.0.21_alpha_20261028_'+stamp+'_abcd1234');stage.mkdir()
        archive=stage/'candidate.zip'
        with zipfile.ZipFile(archive,'w') as zipped:zipped.writestr('LuXiaoGuan/test',b'fixture')
        report=dict(status=status,platform='linux-x64',archive=str(archive),sha256=cleanup.digest(archive),zip_crc_verified=True)
        (stage/'report.json').write_text(json.dumps(report),encoding='utf-8')
        return stage,archive

    def test_keep_new_verified_linux_candidate_and_preserve_windows_running_and_unknown(self):
        old,_=self.stage('100000');latest,archive=self.stage('110000');running,_=self.stage('120000','building')
        unknown=self.root/'build/debian12/releases/user-files';unknown.mkdir()
        result=cleanup.cleanup_linux_candidates(self.root,latest)
        self.assertEqual(result['retained'],str(latest));self.assertFalse(old.exists())
        self.assertTrue(archive.is_file());self.assertTrue(running.exists());self.assertTrue(unknown.exists())
        self.assertEqual((self.windows/'data').read_bytes(),b'Windows retained')

    def test_corrupt_new_candidate_prevents_every_deletion(self):
        old,_=self.stage('100000');latest,archive=self.stage('110000');archive.write_bytes(b'corrupt')
        with self.assertRaises(ValueError):cleanup.cleanup_linux_candidates(self.root,latest)
        self.assertTrue(old.exists());self.assertTrue(latest.exists());self.assertTrue(self.windows.exists())

    def test_failure_prunes_only_reproducible_current_data_and_keeps_previous_success(self):
        success,archive=self.stage('100000');failed,_=self.stage('110000','failed')
        for name in ('dist','pyinstaller'):
            (failed/name).mkdir();(failed/name/'temporary').write_bytes(b'reproducible')
        (failed/'freeze.log').write_text('failure evidence',encoding='utf-8')
        incomplete=failed/'LuXiaoGuan_v1.0.21_alpha_linux-x64_20261028_110000.zip'
        incomplete.write_bytes(b'incomplete candidate archive')
        cleanup.cleanup_failed_linux_candidate(self.root,failed)
        self.assertFalse((failed/'dist').exists());self.assertFalse((failed/'pyinstaller').exists())
        self.assertFalse(incomplete.exists())
        self.assertEqual((failed/'freeze.log').read_text(),'failure evidence')
        self.assertTrue(archive.is_file());self.assertTrue(success.exists());self.assertTrue(self.windows.exists())
