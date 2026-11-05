"""Native-only syscall checks. Windows runs the transaction model separately."""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from modules.platform_services.linux_update_files import RootedFiles
from modules.app_update_services.linux_install_plan import byte_state


@unittest.skipUnless(sys.platform.startswith('linux'),'Native Linux directory descriptors required')
class LinuxUpdateFilesTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.app=self.root/'application';self.app.mkdir()

    def test_native_create_mode_digest_and_no_replace_rename(self):
        data=b'checked bytes';state=byte_state(data,0o755)
        with RootedFiles(self.app) as files:
            files.create('resources/prepared',state,io.BytesIO(data))
            self.assertEqual(files.state('resources/prepared'),state)
            files.create('resources/existing',byte_state(b'original'),io.BytesIO(b'original'))
            with self.assertRaises(FileExistsError):files.rename('resources/prepared','resources/existing')
            files.rename('resources/prepared','resources/published')
            self.assertEqual(files.state('resources/published'),state)
            self.assertEqual(files.read_bytes('resources/existing',64),b'original')

    def test_linked_parent_cannot_redirect_write_to_external_directory(self):
        outside=self.root/'external';outside.mkdir();(outside/'owned').write_bytes(b'user file')
        (self.app/'resources').symlink_to(outside,target_is_directory=True)
        with RootedFiles(self.app) as files,self.assertRaises(OSError):
            files.create('resources/owned',byte_state(b'new'),io.BytesIO(b'new'))
        self.assertEqual((outside/'owned').read_bytes(),b'user file')

    def test_no_follow_read_and_relative_path_guard(self):
        outside=self.root/'secret';outside.write_bytes(b'secret');(self.app/'link').symlink_to(outside)
        with RootedFiles(self.app) as files:
            with self.assertRaises(OSError):files.read_bytes('link',64)
            with self.assertRaises(ValueError):files.state('../secret')
            self.assertEqual(files.state('link')['kind'],'symlink')

    def test_bad_source_and_cancellation_clean_only_new_partial_inode(self):
        with RootedFiles(self.app) as files:
            with self.assertRaises(ValueError):files.create('resources/new',byte_state(b'expected'),io.BytesIO(b'wrong'))
            self.assertIsNone(files.state('resources/new'))
            def cancel():raise InterruptedError('cancelled')
            with self.assertRaises(InterruptedError):files.create('resources/cancelled',byte_state(b'data'),io.BytesIO(b'data'),cancel)
            self.assertIsNone(files.state('resources/cancelled'))

    def test_durable_json_is_bounded_and_does_not_follow_existing_link(self):
        outside=self.root/'secret';outside.write_bytes(b'keep');(self.app/'record.json').symlink_to(outside)
        with RootedFiles(self.app) as files:
            with self.assertRaises(ValueError):files.write_json('record.json',{'schema':1})
            self.assertEqual(outside.read_bytes(),b'keep')
            files.write_json('real.json',{'schema':1})
            self.assertEqual(files.read_json('real.json'),{'schema':1})
            with self.assertRaises(ValueError):files.read_json('real.json',2)

    def test_open_directory_descriptor_stays_bound_after_path_is_replaced_by_link(self):
        outside=self.root/'external';outside.mkdir()
        with RootedFiles(self.app) as files:
            moved=self.root/'original-application';self.app.rename(moved)
            self.app.symlink_to(outside,target_is_directory=True)
            files.create('owned',byte_state(b'original tree'),io.BytesIO(b'original tree'))
        self.assertEqual((moved/'owned').read_bytes(),b'original tree')
        self.assertEqual(list(outside.iterdir()),[])

    def test_fifo_read_is_rejected_without_blocking(self):
        import subprocess
        os.mkfifo(self.app/'fifo')
        script="from modules.platform_services.linux_update_files import RootedFiles; import sys; f=RootedFiles(sys.argv[1]);\ntry:f.read_bytes('fifo',64)\nexcept ValueError:raise SystemExit(0)\nraise SystemExit(1)"
        result=subprocess.run([sys.executable,'-c',script,str(self.app)],capture_output=True,timeout=3)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_cache_lock_rejects_symlink_and_special_file(self):
        from modules.app_update_services.locations import cache_lock
        cache=self.app/'.routermanager-update';cache.mkdir();outside=self.root/'keep';outside.write_bytes(b'keep')
        lock=cache/'install.lock';lock.symlink_to(outside)
        with self.assertRaises((OSError,ValueError)):
            with cache_lock(cache):pass
        self.assertEqual(outside.read_bytes(),b'keep');lock.unlink();os.mkfifo(lock)
        with self.assertRaises((OSError,ValueError)):
            with cache_lock(cache):pass

    def test_private_partial_resume_keeps_written_bytes(self):
        from modules.platform_services.linux_partial_file import RootedPartial
        from modules.resumable_download import PartialDownload
        import hashlib
        from types import SimpleNamespace
        data=b'first second';digest=hashlib.sha256(data).hexdigest()
        with RootedFiles(self.app) as files:
            storage=RootedPartial(files,'payload.z.part')
            with storage.open('wb') as output:output.write(data[:5])
            partial=PartialDownload('unused',len(data),digest,storage=RootedPartial(files,'payload.z.part'))
            self.assertFalse(partial.prepare());self.assertEqual(partial.headers()['Range'],'bytes=5-')
            response=SimpleNamespace(status_code=206,headers={'Content-Range':f'bytes 5-{len(data)-1}/{len(data)}'},iter_content=lambda size:iter([data[5:]]))
            partial.receive(response,lambda *_:None)
            self.assertEqual(files.read_bytes('payload.z.part',1024),data)
            self.assertEqual(files.state('payload.z.part')['mode'],0o600)

    def test_private_partial_rejects_links_and_replaced_identity(self):
        from modules.platform_services.linux_partial_file import RootedPartial
        outside=self.root/'outside';outside.write_bytes(b'keep');outside.chmod(0o600)
        linked=self.app/'linked';linked.symlink_to(outside)
        with RootedFiles(self.app) as files:
            with self.assertRaises((OSError,ValueError)):
                with RootedPartial(files,'linked').open('wb'):pass
            linked.unlink();os.link(outside,linked)
            with self.assertRaises(ValueError):
                with RootedPartial(files,'linked').open('wb'):pass
            storage=RootedPartial(files,'owned')
            with storage.open('wb') as output:output.write(b'first')
            storage.lstat();(self.app/'owned').rename(self.app/'old-owned')
            (self.app/'owned').write_bytes(b'replacement');(self.app/'owned').chmod(0o600)
            with self.assertRaises(ValueError):
                with storage.open('wb'):pass
        self.assertEqual(outside.read_bytes(),b'keep')
