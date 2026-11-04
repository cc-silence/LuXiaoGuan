"""Real Ed25519 input plus filesystem transaction integration; no ELF is executed."""
import base64
from contextlib import contextmanager,ExitStack,nullcontext
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from modules.app_update_services import linux_update_jobs as jobs,linux_update_worker as worker
from modules.platform_services import linux_recovery_launcher as launcher
from modules.app_update_services.manifest import canonical
from tests.test_linux_transaction import LinuxInstallFixture,WindowsFilesModel,host_storage,SimulatedPowerLoss
from tests.test_linux_update_snapshot import model_linux_modes_on_windows
from tests.test_linux_update_process import TICKET

KEY=Ed25519PrivateKey.generate()
PUBLIC=base64.b64encode(KEY.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)).decode()


class LinuxUpdateWorkerTests(LinuxInstallFixture,unittest.TestCase):
    def setUp(self):
        self.setup_install_fixture()
        self.new['history']=[dict(version=self.new['version'],notes='test fixture',published_at='2026-10-28T12:00:00+08:00',resource_revision=0)]
        self.stage=self.stage.parent/hashlib.sha256(canonical(self.new)).hexdigest();self.stage.mkdir();self.stage_sources()
        self.write(self.root/launcher.LAUNCHER,launcher.SCRIPT,0o755)
        payload=dict(schema=1,product='RouterManager',platform='linux-x64',channel='alpha',version=self.new['version'],
                     notes='test fixture',published_at='2026-10-28T12:00:00+08:00',url='https://example.invalid/files/linux-x64/app.zip',
                     size=1,sha256='a'*64,catalog_url='https://example.invalid/catalog/'+self.stage.name+'.json',
                     catalog_sha256=self.stage.name,catalog_size=len(canonical(self.new)))
        envelope=dict(payload=payload,signature=base64.b64encode(KEY.sign(canonical(payload))).decode())
        self.write(self.stage/'manifest.json',canonical(envelope));self.write(self.stage/'catalog.json',canonical(self.new))

    @contextmanager
    def model(self):
        with ExitStack() as stack:
            stack.enter_context(host_storage());stack.enter_context(model_linux_modes_on_windows())
            stack.enter_context(patch.object(jobs,'is_linux',return_value=True))
            stack.enter_context(patch.object(jobs,'PUBLIC_KEY',PUBLIC))
            stack.enter_context(patch.object(jobs,'capture_parent',return_value=dict(TICKET)))
            stack.enter_context(patch.object(worker,'execution_context'))
            self.wait=stack.enter_context(patch.object(worker,'wait_for_parent'))
            self.probe=stack.enter_context(patch.object(worker,'run_check',return_value=dict(returncode=0,output_bytes=0,evidence={})))
            if sys.platform=='win32':
                stack.enter_context(patch.object(jobs,'RootedFiles',WindowsFilesModel));stack.enter_context(patch.object(worker,'RootedFiles',WindowsFilesModel))
                stack.enter_context(patch.object(jobs,'ownership'));stack.enter_context(patch.object(worker,'ownership'))
                stack.enter_context(patch.object(worker,'worker_lease',side_effect=lambda cache:nullcontext()))
                stack.enter_context(patch.object(jobs,'verify_launcher',side_effect=lambda p:launcher.verify_launcher(p,mode=0o755)))
            yield

    def prepare(self):
        with patch.object(sys,'frozen',True,create=True),patch.object(sys,'executable',str(self.root/'LuXiaoGuan')):
            jobs.prepare_job(self.root,self.stage)
            return jobs.arm_job(self.root,self.stage)

    def test_signed_update_prepares_runtime_and_runs_transaction_after_parent_exit(self):
        with self.model():
            runtime,path=self.prepare();self.assertEqual(path,self.stage/jobs.JOB)
            self.assertEqual((runtime/'LuXiaoGuan').read_bytes(),self.old_data['LuXiaoGuan'])
            result=worker.run_job(path,restart=False)
        self.wait.assert_called_once_with(TICKET);self.probe.assert_called_once()
        self.assertEqual(result['status'],'applied');self.assertTrue(result['safe_to_restart'])
        self.assertEqual((self.root/'resources/library.dat').read_bytes(),b'new lib')
        self.assertFalse((self.stage.parent/launcher.PENDING).exists())
        self.assertEqual((self.root/'config/settings.ini').read_bytes(),b'user settings')

    def test_failed_native_startup_rolls_back_before_allowing_restart(self):
        with self.model():
            runtime,path=self.prepare();self.probe.return_value=dict(returncode=1,output_bytes=10,evidence={'error':'startup_module_missing'})
            result=worker.run_job(path,restart=False)
        self.assertEqual(result['status'],'failed');self.assertTrue(result['safe_to_restart']);self.assert_old()
        self.assertFalse((self.stage.parent/launcher.PENDING).exists())

    def test_parent_timeout_leaves_old_installation_and_recovery_pointer(self):
        with self.model():
            runtime,path=self.prepare();self.wait.side_effect=TimeoutError('still running')
            result=worker.run_job(path,restart=False)
        self.assertFalse(result['safe_to_restart']);self.assert_old();self.probe.assert_not_called()
        self.assertTrue((self.stage.parent/launcher.PENDING).exists())
        self.assertFalse((self.stage/'linux-transaction.json').exists())

    def test_startup_recovery_of_only_prepared_update_does_not_install_it(self):
        with self.model():
            runtime,path=self.prepare();result=worker.run_job(path,recover=True,restart=False)
        self.assertEqual(result['status'],'recovered');self.assertTrue(result['safe_to_restart'])
        self.assert_old();self.probe.assert_not_called();self.assertFalse((self.stage.parent/launcher.PENDING).exists())

    def test_invalid_signature_is_rejected_before_preparing_helper(self):
        envelope=json.loads((self.stage/'manifest.json').read_bytes());envelope['payload']['notes']='tampered'
        self.write(self.stage/'manifest.json',canonical(envelope))
        with self.model(),self.assertRaises(InvalidSignature):self.prepare()
        self.assertFalse((self.stage/'runtime').exists());self.assertFalse((self.stage.parent/launcher.PENDING).exists());self.assert_old()

    def test_altered_catalog_is_rejected_before_parent_wait(self):
        with self.model():
            runtime,path=self.prepare();self.write(self.stage/'catalog.json',canonical(self.new)+b' ')
            with self.assertRaises(ValueError):worker.run_job(path,restart=False)
        self.wait.assert_not_called();self.assert_old()

    def test_custom_theme_survives_both_runtime_snapshot_and_update(self):
        self.write(self.root/'themes/dark.qss',b'custom local theme')
        with self.model():
            runtime,path=self.prepare()
            self.assertEqual((runtime/'themes/dark.qss').read_bytes(),b'custom local theme')
            result=worker.run_job(path,restart=False)
        self.assertEqual(result['status'],'applied');self.assertEqual((self.root/'themes/dark.qss').read_bytes(),b'custom local theme')

    def test_other_stage_recovery_pointer_is_never_cleared(self):
        with self.model():
            runtime,path=self.prepare();self.write(self.stage.parent/launcher.PENDING,b'f'*64+b'\n',0o600)
            with self.assertRaises(ValueError):worker.run_job(path,restart=False)
        self.assertEqual((self.stage.parent/launcher.PENDING).read_bytes(),b'f'*64+b'\n');self.assert_old()

    def test_worker_must_be_a_frozen_copy_of_the_verified_runtime(self):
        with patch.object(sys,'frozen',False,create=True),self.assertRaises(ValueError):worker.execution_context(self.stage/'runtime')

    def test_modified_program_runtime_is_not_trusted_as_a_helper(self):
        self.write(self.root/'resources/library.dat',b'manual runtime edit')
        with self.model(),self.assertRaises(ValueError):self.prepare()
        self.assertFalse((self.stage.parent/launcher.PENDING).exists())
        self.assertEqual((self.root/'resources/library.dat').read_bytes(),b'manual runtime edit')

    def test_successful_restart_is_launched_after_recovery_pointer_is_removed(self):
        def restart(*args,**kwargs):
            self.assertFalse((self.stage.parent/launcher.PENDING).exists())
            self.assertEqual((self.root/'LuXiaoGuan').read_bytes(),self.new_data['LuXiaoGuan'])
            self.assertEqual(kwargs['env']['PYINSTALLER_RESET_ENVIRONMENT'],'1')
            self.assertNotIn('LD_LIBRARY_PATH',kwargs['env'])
        with self.model(),patch.object(worker.subprocess,'Popen',side_effect=restart) as start:
            runtime,path=self.prepare();result=worker.run_job(path)
        self.assertEqual(result['status'],'applied');start.assert_called_once()

    def test_restart_failure_does_not_rollback_a_successfully_validated_installation(self):
        with self.model(),patch.object(worker.subprocess,'Popen',side_effect=OSError('cannot launch')):
            runtime,path=self.prepare();result=worker.run_job(path)
        self.assertTrue(result['restart_failed']);self.assertEqual(result['installation_status'],'applied')
        self.assertEqual((self.root/'LuXiaoGuan').read_bytes(),self.new_data['LuXiaoGuan'])
        self.assertFalse((self.stage.parent/launcher.PENDING).exists())

    def test_recovery_worker_restores_an_installation_with_missing_main_program(self):
        cls=WindowsFilesModel if sys.platform=='win32' else worker.RootedFiles
        original=cls.rename
        def interrupt(store,source,destination):
            original(store,source,destination)
            if store.root==self.root and source=='LuXiaoGuan' and destination.endswith('.old'):
                raise SimulatedPowerLoss()
        with self.model():
            runtime,path=self.prepare()
            with patch.object(cls,'rename',interrupt),self.assertRaises(SimulatedPowerLoss):
                worker.run_job(path,restart=False)
            self.assertFalse((self.root/'LuXiaoGuan').exists())
            result=worker.run_job(path,recover=True,restart=False)
        self.assertEqual(result['status'],'recovered');self.assertTrue(result['safe_to_restart'])
        self.assert_old();self.assertFalse((self.stage.parent/launcher.PENDING).exists())

    def test_background_preparation_does_not_intercept_normal_app_launches(self):
        with self.model(),patch.object(sys,'frozen',True,create=True),patch.object(sys,'executable',str(self.root/'LuXiaoGuan')):
            runtime,path=jobs.prepare_job(self.root,self.stage)
            self.assertTrue(path.is_file());self.assertTrue(runtime.is_dir())
            self.assertFalse((self.stage.parent/launcher.PENDING).exists())
            self.assertFalse((self.stage.parent/'active.json').exists())

    def test_commit_rechecks_job_permissions_before_arming_recovery(self):
        with self.model(),patch.object(sys,'frozen',True,create=True),patch.object(sys,'executable',str(self.root/'LuXiaoGuan')):
            runtime,path=jobs.prepare_job(self.root,self.stage)
            self.write(path,path.read_bytes(),0o644)
            with self.assertRaises(ValueError):jobs.arm_job(self.root,self.stage)
        self.assertFalse((self.stage.parent/launcher.PENDING).exists());self.assert_old()
