"""Signed Linux download-to-recovery flow with native IO on Linux, Windows IO models here."""
import base64
from contextlib import ExitStack,nullcontext,contextmanager
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock,patch
import zlib
from tests import test_linux_update_worker as fixtures
from tests.test_linux_transaction import LinuxInstallFixture,WindowsFilesModel,MODES
from modules.app_update_services import linux_desktop_plan as planner,linux_download as download,linux_update_cache as cache
from modules.app_update_services import linux_update_worker as worker
from modules.app_update_services.manifest import canonical


class Response:
    status_code=200
    headers={}
    def __init__(self,data):self.data=data
    def __enter__(self):return self
    def __exit__(self,*_):pass
    def iter_content(self,size):
        for n in range(0,len(self.data),size):yield self.data[n:n+size]


class ModelPartial:
    """Windows IO fixture; native tests use real dirfd-backed partials."""
    def __init__(self,files,name):self.path=files.path(name)
    def lstat(self):return self.path.lstat()
    def is_symlink(self):return self.path.is_symlink()
    def unlink(self,missing_ok=False):self.path.unlink(missing_ok=missing_ok)
    @contextmanager
    def open(self,mode):
        with self.path.open(mode) as stream:
            try:yield stream
            finally:
                if self.path.exists():
                    info=self.path.stat();MODES[info.st_dev,info.st_ino]=0o600


class LinuxPipelineTests(LinuxInstallFixture,unittest.TestCase):
    setUp=fixtures.LinuxUpdateWorkerTests.setUp
    prepare=fixtures.LinuxUpdateWorkerTests.prepare
    model=fixtures.LinuxUpdateWorkerTests.model
    def io_model(self):
        stack=ExitStack();stack.enter_context(self.model())
        if sys.platform=='win32':
            for module in (planner,download,cache):stack.enter_context(patch.object(module,'RootedFiles',WindowsFilesModel))
            stack.enter_context(patch.object(download,'RootedPartial',ModelPartial))
            stack.enter_context(patch.object(cache,'ownership'))
            stack.enter_context(patch.object(cache,'worker_lease',side_effect=lambda *a:nullcontext()))
        return stack
    def test_plan_preserves_custom_resources_and_lists_conflicts(self):
        with self.io_model():
            self.write(self.root/'themes/dark.qss',b'custom theme')
            self.write(self.root/'resources/library.dat',b'edited critical')
            value=planner.make_plan(self.root,self.new)
            self.assertEqual(value['preserved'],['themes/dark.qss'])
            self.assertEqual(value['conflicts'],['resources/library.dat'])
            self.assertEqual(len(value['removed']),1)
    def test_permission_only_change_downloads_payload_for_transaction(self):
        with self.io_model():
            self.write(self.root/'LuXiaoGuan',self.new_data['LuXiaoGuan'],0o644)
            # This is a local critical edit, not silently corrected by a download.
            self.assertIn('LuXiaoGuan',planner.make_plan(self.root,self.new)['conflicts'])
    def test_cancelled_plan_does_not_change_installation(self):
        with self.io_model(),self.assertRaises(InterruptedError):planner.make_plan(self.root,self.new,lambda:True)
        self.assert_old()
    def source(self,data,*,compressed=None,cancel=lambda:None):
        compressed=zlib.compress(data) if compressed is None else compressed
        entry=dict(blob_size=len(compressed),blob_sha256=hashlib.sha256(compressed).hexdigest())
        return download.InflatedSource(Response(compressed),entry,cancel,lambda *_:None)
    def test_stream_checks_hashes_and_memory_bound(self):
        data=b'a'*3000000;stream=self.source(data);blocks=[]
        while True:
            block=stream.read(1024*1024)
            if not block:break
            self.assertLessEqual(len(block),1024*1024);blocks.append(block)
        self.assertEqual(b''.join(blocks),data)
    def test_trailing_truncated_and_wrong_compressed_hash_fail(self):
        cases=[zlib.compress(b'data')+b'extra',zlib.compress(b'data')[:-1]]
        for raw in cases:
            with self.subTest(raw=raw),self.assertRaises(ValueError):
                stream=self.source(b'data',compressed=raw)
                while stream.read(1024):pass
        stream=self.source(b'data');stream.entry['blob_sha256']='0'*64
        with self.assertRaises(ValueError):stream.read(1024)
    def test_decompression_cancellation_is_not_swallowed(self):
        def stop():raise InterruptedError('cancelled')
        with self.assertRaises(InterruptedError):self.source(b'data',cancel=stop).read(1024)
    def download_fixture(self):
        catalog=copy.deepcopy(self.new);blobs={}
        for entry in catalog['files']:
            compressed=zlib.compress(self.new_data[entry['path']]);digest=hashlib.sha256(compressed).hexdigest()
            entry.update(blob_sha256=digest,blob_size=len(compressed));blobs[digest]=compressed
        catalog['history']=copy.deepcopy(self.new['history']);digest=hashlib.sha256(canonical(catalog)).hexdigest()
        envelope=json.loads((self.stage/'manifest.json').read_bytes());envelope['payload'].update(catalog_sha256=digest,catalog_size=len(canonical(catalog)),catalog_url='https://example.invalid/catalog/'+digest+'.json')
        envelope['signature']=base64.b64encode(fixtures.KEY.sign(canonical(envelope['payload']))).decode()
        request=NS(root=self.root,catalog=catalog,manifest=canonical(envelope),cancel_event=threading.Event(),channels=[NS(proxy_url='')],progress=Mock())
        return request,blobs
    def test_signed_download_prepares_only_after_releasing_lease(self):
        request,blobs=self.download_fixture();events=[]
        def prepared(root,stage,**kwargs):
            from modules.app_update_services.locations import cache_lock
            with cache_lock(stage.parent) as acquired:self.assertTrue(acquired)
            events.append(stage)
        with self.io_model(),patch.object(download,'PUBLIC_KEY',fixtures.PUBLIC),patch.object(download,'prepare_helper',side_effect=prepared),patch.object(cache,'prune_completed'),patch.object(download.requests,'Session') as session:
            session.return_value.__enter__.return_value.get.side_effect=lambda url,**kw:Response(blobs[url.rsplit('/',1)[1][:-2]])
            plan=planner.make_plan(self.root,request.catalog);download.download(request,plan)
            self.assertEqual(len(events),1)
            for entry in plan['files']:self.assertEqual((Path(plan['stage'])/(entry['sha256']+'.file')).read_bytes(),self.new_data[entry['path']])
        self.assert_old()
    def test_download_snapshot_install_and_restart_boundary(self):
        request,blobs=self.download_fixture()
        from modules.app_update_services import linux_update_jobs as jobs
        with self.io_model(),patch.object(download,'PUBLIC_KEY',fixtures.PUBLIC),patch.object(cache,'prune_completed'),patch.object(download.requests,'Session') as session,patch.object(sys,'frozen',True,create=True),patch.object(sys,'executable',str(self.root/'LuXiaoGuan')):
            session.return_value.__enter__.return_value.get.side_effect=lambda url,**kw:Response(blobs[url.rsplit('/',1)[1][:-2]])
            plan=planner.make_plan(self.root,request.catalog);download.download(request,plan)
            _,path=jobs.arm_job(self.root,plan['stage'])
            result=worker.run_job(path,restart=False)
            self.assertEqual(result['status'],'applied')
            self.assertFalse((Path(plan['stage']).parent/'pending-linux').exists())
            self.assertEqual((self.root/'resources/library.dat').read_bytes(),b'new lib')
            self.assertEqual((self.root/'config/settings.ini').read_bytes(),b'user settings')

    def test_transport_failure_retries_only_the_same_signed_object(self):
        request,blobs=self.download_fixture();request.channels=[NS(proxy_url=''),NS(proxy_url='')];calls=[]
        def receive(url,**kw):
            calls.append(url)
            if len(calls)==1:raise download.requests.ConnectionError('fixture')
            return Response(blobs[url.rsplit('/',1)[1][:-2]])
        with self.io_model(),patch.object(download,'PUBLIC_KEY',fixtures.PUBLIC),patch.object(download,'prepare_helper'),patch.object(cache,'prune_completed'),patch.object(download.requests,'Session') as session:
            session.return_value.__enter__.return_value.get.side_effect=receive
            plan=planner.make_plan(self.root,request.catalog);download.download(request,plan)
        self.assertEqual(calls[0],calls[1]);self.assertEqual(len(calls),len(plan['files'])+1)

    def test_fresh_download_task_requests_only_interrupted_compressed_suffix(self):
        request,blobs=self.download_fixture();calls=[];first=[True];cut=[0]
        class InterruptedResponse(Response):
            def iter_content(self,size):
                cut[0]=max(1,len(self.data)//2)
                yield self.data[:cut[0]]
                raise download.requests.ConnectionError('interrupted socket fixture')
        def receive(url,**kw):
            calls.append((url,kw.get('headers',{})))
            data=blobs[url.rsplit('/',1)[1][:-2]]
            if first[0]:first[0]=False;return InterruptedResponse(data)
            offset=int(kw['headers'].get('Range','bytes=0-')[6:-1])
            result=Response(data[offset:])
            if offset:
                result.status_code=206;result.headers={'Content-Range':f'bytes {offset}-{len(data)-1}/{len(data)}'}
            return result
        with self.io_model(),patch.object(download,'PUBLIC_KEY',fixtures.PUBLIC),patch.object(download,'prepare_helper') as prepare,patch.object(cache,'prune_completed'),patch.object(download.requests,'Session') as session:
            session.return_value.__enter__.return_value.get.side_effect=receive
            plan=planner.make_plan(self.root,request.catalog)
            with self.assertRaises(RuntimeError):download.download(request,plan)
            prepare.assert_not_called()
            download.download(request,plan)
            self.assertEqual(calls[0][0],calls[1][0])
            self.assertEqual(calls[1][1]['Range'],f'bytes={cut[0]}-')
            self.assertEqual(prepare.call_count,1)
            for entry in plan['files']:
                self.assertEqual((Path(plan['stage'])/(entry['sha256']+'.file')).read_bytes(),self.new_data[entry['path']])

    def test_interrupted_partial_is_recovered_from_its_exact_intent(self):
        request,blobs=self.download_fixture();envelope=json.loads(request.manifest)
        stage=self.root/'.routermanager-update'/envelope['payload']['catalog_sha256'];stage.mkdir()
        intent=dict(schema=1,stage=stage.name,partials=sorted({e['sha256']+'.partial' for e in request.catalog['files']}))
        self.write(stage/'linux-download.json',canonical(intent),0o600)
        partial=stage/intent['partials'][0];self.write(partial,b'interrupted bytes',0o600)
        with self.io_model(),patch.object(download,'PUBLIC_KEY',fixtures.PUBLIC),patch.object(download,'prepare_helper'),patch.object(cache,'prune_completed'),patch.object(download.requests,'Session') as session:
            session.return_value.__enter__.return_value.get.side_effect=lambda url,**kw:Response(blobs[url.rsplit('/',1)[1][:-2]])
            plan=planner.make_plan(self.root,request.catalog);download.download(request,plan)
        self.assertFalse(partial.exists());self.assertFalse((stage/'linux-download.json').exists())

    def test_corrupt_network_body_cannot_prepare_or_publish_payload(self):
        request,blobs=self.download_fixture()
        with self.io_model(),patch.object(download,'PUBLIC_KEY',fixtures.PUBLIC),patch.object(download,'prepare_helper') as prepare,patch.object(cache,'prune_completed'),patch.object(download.requests,'Session') as session:
            session.return_value.__enter__.return_value.get.side_effect=lambda url,**kw:Response(b'Z'*len(blobs[url.rsplit('/',1)[1][:-2]]))
            plan=planner.make_plan(self.root,request.catalog)
            with self.assertRaises((ValueError,zlib.error)):download.download(request,plan)
            prepare.assert_not_called();self.assertNotIn('stage',plan)
        self.assert_old()

    def test_cancellation_during_stream_does_not_prepare_installation(self):
        request,blobs=self.download_fixture();request.progress.emit.side_effect=lambda *args:request.cancel_event.set()
        with self.io_model(),patch.object(download,'PUBLIC_KEY',fixtures.PUBLIC),patch.object(download,'prepare_helper') as prepare,patch.object(cache,'prune_completed'),patch.object(download.requests,'Session') as session:
            session.return_value.__enter__.return_value.get.side_effect=lambda url,**kw:Response(blobs[url.rsplit('/',1)[1][:-2]])
            plan=planner.make_plan(self.root,request.catalog)
            with self.assertRaises(InterruptedError):download.download(request,plan)
            prepare.assert_not_called();self.assertNotIn('stage',plan)
        self.assert_old()

    def test_link_entries_need_no_blob_but_still_form_a_valid_native_plan(self):
        catalog=copy.deepcopy(self.new);catalog['files'].append(dict(path='resources/link.so',kind='symlink',target='library.dat'))
        with self.io_model():value=planner.make_plan(self.root,catalog)
        self.assertTrue(all(item['kind']=='file' for item in value['files']))
        self.assertNotIn('resources/link.so',value['conflicts'])

    def test_foreign_signature_stops_before_cache_mutation(self):
        request,_=self.download_fixture();request.manifest=request.manifest.replace(b'test fixture',b'changed text')
        with self.io_model(),patch.object(download,'PUBLIC_KEY',fixtures.PUBLIC),self.assertRaises(Exception):download.download(request,{})
        self.assertFalse(any(p.name=='pending-linux' for p in self.stage.parent.iterdir()))
    def test_completed_runtime_reclaimed_unknown_user_file_preserved(self):
        with self.io_model():
            runtime,path=self.prepare();worker.run_job(path,restart=False)
            self.write(self.stage/'user-note.txt',b'keep this')
            self.assertFalse(cache.cleanup_stage(self.stage,remove_metadata=True))
            self.assertFalse(runtime.exists());self.assertEqual((self.stage/'user-note.txt').read_bytes(),b'keep this')
    def test_locally_changed_snapshot_is_preserved(self):
        with self.io_model():
            runtime,path=self.prepare();worker.run_job(path,restart=False)
            self.write(runtime/'LuXiaoGuan',b'unexpected edit',0o755)
            self.assertFalse(cache.cleanup_stage(self.stage));self.assertEqual((runtime/'LuXiaoGuan').read_bytes(),b'unexpected edit')
    def test_pending_update_blocks_startup_cleanup(self):
        with self.io_model(),patch.object(cache,'cleanup_stage') as cleanup:
            self.prepare();cache.prune_completed(self.root,current_version=self.new['version']);cleanup.assert_not_called()


if __name__=='__main__':unittest.main()
