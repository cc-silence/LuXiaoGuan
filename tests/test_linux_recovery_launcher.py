"""Execute the stable launcher in /bin/sh or explicit Git Bash, never WSL."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock,patch
from modules.platform_services import linux_recovery_launcher as launcher,linux_update_install as install,update_dispatch as dispatch
import main as application


from tests.posix_shell import local_shell as shell


class RecoveryDispatchTests(unittest.TestCase):
    def setUp(self):
        migration=patch("modules.platform_services.user_data_migration.prepare_user_directories")
        migration.start();self.addCleanup(migration.stop)

    def test_worker_flags_dispatch_before_loading_desktop(self):
        for flag,recover in [('--linux-update-worker',False),('--linux-update-recover',True)]:
            with patch.object(application,'sys',NS(argv=['app',flag,'job.json'])),patch('modules.app_update_services.linux_update_worker.main',return_value=7) as worker:
                self.assertEqual(application.main(),7)
                worker.assert_called_once_with([flag,'job.json'],recover=recover)

    def test_gui_startup_smoke_does_not_reenter_recovery(self):
        bootstrap=NS(run=Mock(return_value=3))
        with patch.object(application,'sys',NS(argv=['app','--smoke-test'],platform='linux',frozen=True)),patch.dict(sys.modules,{'router_app.bootstrap':bootstrap}),patch.object(install,'recover_at_startup') as recover:
            self.assertEqual(application.main(),3);recover.assert_not_called()

    def test_normal_frozen_linux_startup_redirects_before_desktop(self):
        bootstrap=NS(run=Mock())
        with patch.object(application,'sys',NS(argv=['app'],platform='linux',frozen=True,executable='/app/LuXiaoGuan')),patch.dict(sys.modules,{'router_app.bootstrap':bootstrap}),patch.object(dispatch,'is_linux',return_value=True),patch.object(install,'recover_at_startup',return_value=True) as recover:
            self.assertEqual(application.main(),0);recover.assert_called_once_with(Path('/app'));bootstrap.run.assert_not_called()

    def test_no_update_marker_keeps_normal_startup_and_unknown_active_record_blocks(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with patch.object(install,'is_linux',return_value=True),patch.object(install,'sys',NS(frozen=True)),patch.object(install.os,'execve') as execute:
                self.assertFalse(install.recover_at_startup(root));execute.assert_not_called()
                cache=root/'.routermanager-update';cache.mkdir();(cache/'active.json').write_bytes(b'{}')
                with self.assertRaises(ValueError):install.recover_at_startup(root)
                execute.assert_not_called()

    def test_windows_startup_keeps_its_existing_desktop_path(self):
        bootstrap=NS(run=Mock(return_value=4))
        with patch.object(application,'sys',NS(argv=['app'],platform='win32',frozen=True,executable='C:/app/LuXiaoGuan.exe')),patch.dict(sys.modules,{'router_app.bootstrap':bootstrap}),patch.object(install,'recover_at_startup') as recover,patch.object(dispatch,'recover_before_gui',return_value=False) as selected,patch('modules.desktop_shell.application_start.run_desktop',return_value=4) as desktop:
            self.assertEqual(application.main(),4);recover.assert_not_called()
            selected.assert_called_once_with(Path('C:/app'))
            desktop.assert_called_once_with(application.APP_VERSION)
            bootstrap.run.assert_not_called()

    def test_windows_recovery_uses_only_the_windows_backend(self):
        backend=NS(recover_interrupted_update=Mock(return_value=False))
        with patch.object(dispatch,'is_linux',return_value=False),patch.dict(sys.modules,{'modules.platform_services.app_update_install':backend}),patch.object(install,'recover_at_startup') as linux:
            self.assertFalse(dispatch.recover_before_gui(Path('/application')))
            backend.recover_interrupted_update.assert_called_once_with(Path('/application'))
            linux.assert_not_called()

    def test_frozen_linux_shortcut_selects_the_stable_launcher(self):
        from contextlib import ExitStack
        from modules.platform_services.linux_desktop_shortcut import shortcut_spec
        with tempfile.TemporaryDirectory() as directory,ExitStack() as stack:
            root=Path(directory);script=root/launcher.LAUNCHER;script.write_bytes(launcher.SCRIPT);script.chmod(0o755)
            stack.enter_context(patch.dict(sys.modules,{'modules.config_manager':NS(BASE_DIR=root)}))
            if sys.platform=='win32':
                verify=launcher.verify_launcher
                stack.enter_context(patch.object(launcher,'verify_launcher',side_effect=lambda p:verify(p,mode=0o755)))
            spec=shortcut_spec(desktop=root,base=root,executable=root/'LuXiaoGuan',frozen=True)
            self.assertEqual(spec.target,script);self.assertEqual(spec.arguments,'')


@unittest.skipUnless(shell() is not None,'POSIX shell or explicit Git Bash is unavailable')
class StableLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='router manager shell-\u6d4b\u8bd5-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.script=self.root/launcher.LAUNCHER
        self.script.write_bytes(launcher.SCRIPT);self.script.chmod(0o755)
        self.program=self.root/'LuXiaoGuan';self.stub(self.program,'NORMAL')
        self.cache=self.root/'.routermanager-update';self.cache.mkdir()

    def stub(self,path,tag):
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(('#!/bin/sh\nprintf "%s\\n" "'+tag+'" "${1-unset}" "${2-unset}" "${PYINSTALLER_RESET_ENVIRONMENT-unset}" "${_PYI_APPLICATION_HOME_DIR-unset}"\n').encode('utf-8'))
        path.chmod(0o755)

    def run_launcher(self,*args):
        env=os.environ.copy();env['_PYI_APPLICATION_HOME_DIR']='old-runtime'
        return subprocess.run([str(shell()),self.script.as_posix(),*args],stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env,timeout=15)

    def test_shell_syntax_and_normal_start_preserve_arguments_and_reset_bundle_environment(self):
        check=subprocess.run([str(shell()),'-n',self.script.as_posix()],capture_output=True,timeout=15)
        self.assertEqual(check.returncode,0,check.stderr)
        value=self.run_launcher('argument with spaces')
        self.assertEqual(value.returncode,0,value.stderr)
        self.assertEqual(value.stdout.decode().splitlines(),['NORMAL','argument with spaces','unset','1','unset'])

    def test_recovery_still_starts_when_main_executable_is_missing(self):
        self.program.unlink();revision='a'*64;stage=self.cache/revision
        self.stub(stage/'runtime/LuXiaoGuan','RECOVERY');(self.cache/launcher.PENDING).write_bytes((revision+'\n').encode('ascii'))
        value=self.run_launcher();self.assertEqual(value.returncode,0,value.stderr)
        lines=value.stdout.decode().splitlines();self.assertEqual(lines[0:2],['RECOVERY','--linux-update-recover-ui'])
        self.assertTrue(lines[2].endswith('/linux-job.json'))

    def test_malformed_pointer_never_runs_normal_main(self):
        for data in [b'../elsewhere\n',b'f'*64,b'f'*64+b'\nextra',b'Z'*64+b'\n']:
            (self.cache/launcher.PENDING).write_bytes(data)
            value=self.run_launcher();self.assertNotEqual(value.returncode,0);self.assertNotIn(b'NORMAL',value.stdout)

    def test_active_update_without_pointer_stops_normal_main(self):
        (self.cache/'active.json').write_bytes(b'{}')
        value=self.run_launcher();self.assertNotEqual(value.returncode,0);self.assertNotIn(b'NORMAL',value.stdout)

    def test_modified_launcher_is_rejected_by_content_validation(self):
        self.script.write_bytes(launcher.SCRIPT+b'\n')
        with self.assertRaises(ValueError):launcher.verify_launcher(self.script,mode=0o755)
